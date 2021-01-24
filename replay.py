import os 
import ujson as json
import networkx as nx
import traceback
import time
import bisect
import collections
import re

from dag_utils import QueueType
from trace_utils import *
from progress_utils import progressBar
import logger_utils
import debug_utils
import arg_utils

FIXED_GAP_us = 5
args_ = arg_utils.SingleArg().args

if args_.comm_backend == "NCCL":
    from hvd.graph import *
elif args_.comm_backend == "BYTEPS":
    from bps_helper.graph import *

def ret_priority(n_):
    ### The smaller the rank is, the higher priority the node has
    if "FW" in n_:
        return 0
    elif "OUTPUT" in n_:
        return 1
    elif "BW" in n_:
        return 2
    elif "UPDATE_" in n_:
        return 3
    else:
        return 4

def _schedule(_a, _b):
    _ap = ret_priority(_a[0])
    _bp = ret_priority(_b[0])
    if _ap == _bp:
        ### The same priority, compare the start time		
        return _a[1] < _b[1]
    else:
        return _ap < _bp

class Device:
    def __init__(self, device_name, _replayer, infi_para=False, comm_backend = "NCCL"):
        self.replayer = _replayer
        self.device_time = 0
        self.device_name = device_name
        #! infi_para devices allow to excute in infinite parallelism
        self.infi_para = infi_para
        ### Used to record the last event generated by this device
        self.prev_name_dur = None
        self.comm_backend = comm_backend

        ### nodes to be executed, in a **partial order**
        self.queue = []

    def reset(self):
        self.device_time = 0
        self.prev_name_dur = None
        self.queue = []

    def real_start_t(self, _last_end_time):
        return max(_last_end_time, self.device_time)

    def exct(self, name, _last_end_time, step_idx):
        ''' Execute one op on this device 

        Parameters
        ----
        name: str
            The name of the op to run
        _last_end_time: float
            The time when this op can start to run, 
            i.e., all the dependent ops have been done
        '''
        ### for debug
        debug_utils.DebugRecorder().debug_event_start()

        # TODO(chenyu): what if self.infi_para?
        if not self.infi_para:
            start_t = self.real_start_t(_last_end_time)
        else:
            raise NotImplementedError("Infi para is not yet implemented.")

        if name == "END":
            #! No event is generated, but has successors
            self.mark_as_exct(name, start_t, start_t)
            return

        ### Really start to execute
        avg = self.replayer.dag.nodes[name]["avg"]
        if "+" in name and "Comm" not in name:
            pid, raw_name, cat, suffix = parse_allinfo_from_name(name.split("+")[0])
        else:
            pid, raw_name, cat, suffix = parse_allinfo_from_name(name)
        delay, ratio = self.get_delay_para(name)
        duration = (1000.0 * max(avg + delay, 0)) * ratio
        if self.comm_backend == "BYTEPS" and "UPDATE_CAL" in name:
            duration = 0

        if duration == 0:
            self.prev_name_dur = (name, 0)
        else:
            event = {
                        "name": raw_name,
                        "ts": start_t,
                        "dur": duration,
                        "pid": pid,
                        "cat": cat,
                        "ph": "X",
                        "tid": self.device_name,
                        "args": {
                            "name": name,
                            "cnt": step_idx
                        }
                    }
            ### Expose dependency info in trace["args"], time-consuming
            if args_.full_trace:
                _id = 0
                for prev, _ in self.replayer.dag.in_edges(name):
                    event["args"]["input%d"%_id] = prev
                    _id += 1
            ### Construct execution graphs
            ### 1. Add new edges according to the execution order
            if self.prev_name_dur is not None:
                if (self.prev_name_dur[0], name) not in self.replayer.exct_dag.edges:
                    self.replayer.exct_dag.add_edge(self.prev_name_dur[0], name, weight=(self.prev_name_dur[1] / 1000.0))
            self.prev_name_dur = (event["args"]["name"], event['dur'])
            # ### 2. Update edge weight
            # for next_ in self.replayer.exct_dag.successors(name):
            # 	self.replayer.exct_dag.edges[name, next_]["weight"] = duration / 1000.0
            self.replayer.rst_traces.append(event)
        debug_utils.DebugRecorder().debug_event_start()

        self.mark_as_exct(name, start_t, start_t + duration)
        debug_utils.DebugRecorder().debug_event_end(name, self.device_name, "mark_as_exct")
        pid = parse_pid_from_name(name)
        self.replayer.step_end_time[pid] = start_t + duration
        #! TODO: for debug
        debug_utils.DebugRecorder().debug_event_end(name, self.device_name, "exct")
    
    def _update_device_time(self, name, _end_time):
        ### Apply the gap between two nodes
        gap = 0
        for key, value in self.replayer.dag.nodes[name].items():
            if "GAP" in key:
                ### e.g. "gap.operator.operator"
                key_s = key.split("GAP")
                ### TODO (huhanpeng): does this fit to the BytePS or use intra-gap instead
                if key_s[0] == key_s[1]:
                    gap += value
                    if gap > 1000:
                        SingleLogger().debug("Large GAP detected: {}, key = {}, gap = {}".format(name, key, value))
        if gap < 0:
            raise RuntimeError(
                "Negative GAP detected: {}, gap = {}".format(name, gap))
        self.device_time = _end_time + gap

    def mark_as_exct(self, name, _start_t, _end_time):
        ''' Mark that the op has been executed '''
        self._update_device_time(name, _end_time)
        self.replayer.node_status.pop(name)
        this_cat = parse_cat_from_name(name)
        for _succ in self.replayer.dag.successors(name):
            next_cat = parse_cat_from_name(_succ)
            if _succ in self.replayer.node_status:
                _status = self.replayer.node_status[_succ]
                ### Calculate the ready time
                if self.comm_backend == "NCCL" and ("SEND" in name and "RECV" in _succ):
                    ### For Send->Recv edge, there exist some overlap
                    ### TODO (huhanpeng): how do decide the end time of the RECV event
                    avg = self.replayer.dag.nodes[_succ]["avg"]
                    _status["ready"] = _end_time
                else:
                    ## For BYTEPS and Horovod, Only apply BW->Comm gaps
                    ## Other gaps should be considered with the device time.
                    gap = 0
                    if GAP_STR_OP2COMM in self.replayer.dag.nodes[name] and next_cat == CatName.COMM.value and this_cat == CatName.OPERATOR.value:
                    # if GAP_STR_OP2COMM in self.replayer.dag.nodes[name] and next_cat == "Comm":
                        gap += self.replayer.dag.nodes[name][GAP_STR_OP2COMM]
                        if self.replayer.dag.nodes[name][GAP_STR_OP2COMM] > 10000:
                            SingleLogger().debug("Large OP2COMM gap detected, {} -> {},  gap: {}".format(name, _succ, self.replayer.dag.nodes[name][GAP_STR_OP2COMM]))
                    _status["ready"] = (_end_time + gap) if _status["ready"] is None else max(_end_time + gap, _status["ready"])

                ### Whether the dependency has met
                _status["in_degree"] -= 1
                # self.replayer.debuger.mark_as_exct(name, _succ)
                if _status["in_degree"] == 0:
                    if _status["ready"] is None:
                        raise RuntimeError("{}\'s ready time is not decided".format(_succ))
                    self.replayer.insert_next_node(_succ, _status["ready"])
        # self.replayer.debuger.show_staue()

    def get_delay_para(self, name_):
        #! Get the delay parameters.
        delay = 0
        ratio = 1.0
        if self.replayer.delay_dict is not None:
            cat = parse_cat_fine_grained(name_)
            if name_ in self.replayer.delay_dict:
                delay = self.replayer.delay_dict[name_]["delay"]
                ratio = self.replayer.delay_dict[name_]["ratio"]
            elif "DELAY_ALL_CMP" in self.replayer.delay_dict and cat in COMP_CAT:
                delay = self.replayer.delay_dict["DELAY_ALL_CMP"]["delay"]
                ratio = self.replayer.delay_dict["DELAY_ALL_CMP"]["ratio"]
            elif "DELAY_ALL_COMM" in self.replayer.delay_dict and cat in COMM_CAT:
                delay = self.replayer.delay_dict["DELAY_ALL_COMM"]["delay"]
                ratio = self.replayer.delay_dict["DELAY_ALL_COMM"]["ratio"]
            elif "DELAY_ALL" in self.replayer.delay_dict:
                delay = self.replayer.delay_dict["DELAY_ALL"]["delay"]
                ratio = self.replayer.delay_dict["DELAY_ALL"]["ratio"]
        return delay, ratio

class CommKernelDevice(Device):
    '''For horovod Communication Kernels, can be occupied by a tensor'''
    def __init__(self, device_name, _replayer, comm_delay = 0, comm_backend = "NCCL", infi_para=False):
        '''period: cycle time in ms'''
        super().__init__(device_name, _replayer, infi_para=infi_para, comm_backend = comm_backend)
        self.lock = None
        self.blocked = []

    def acquire_lock(self, name):
        if self.lock is None:
            ### prefix->op_type.op_name.sub_op~>suffix
            self.lock = parse_rawname_from_name(name).split(".")[1]
            return True
        elif self.lock == parse_rawname_from_name(name).split(".")[1]:
            return True
        else:
            return False

    def release_lock(self):
        self.lock = None

    def exct(self, name, _last_end_time, step_idx):
        if self.acquire_lock(name):
            super().exct(name, _last_end_time, step_idx)
            if QueueType().ret_list()[-1] in name:
                ### release the device if this is the last sub_op of this tensor
                self.release_lock()
                if len(self.blocked) > 0:
                    ### pop an operator from the blocked queue to execute
                    ### since these block operators have high priority (early start time)
                    ### they are expected to be execute immediately
                    self.replayer.insert_next_node(*self.blocked.pop(0))
        else:
            ### This device is occupied by another tensor, blocked
            self.blocked.append((name, _last_end_time))

class PeriodicDevice(Device):
    '''For horovod negotiation, simulate cycles in Horovod'''
    def __init__(self, device_name, _replayer, period=5, comm_delay = 0, comm_backend = "NCCL", infi_para=False):
        '''period: cycle time in ms'''
        super().__init__(device_name, _replayer, infi_para=infi_para, comm_backend = comm_backend)
        self.period = period * 1000

    def real_start_t(self, _last_end_time):
        while self.device_time < _last_end_time:
            self.device_time += self.period
        return self.device_time

    def _update_device_time(self, name, _end_time):
        while self.device_time < _end_time:
            self.device_time += self.period

class PSCommDevice(Device):
    def __init__(self, device_name, _replayer, op_counter, comm_delay = 0, comm_backend = "NCCL", infi_para=False):
        super().__init__(device_name, _replayer, infi_para=infi_para, comm_backend = comm_backend)
        self.op_counter = op_counter
        self.comm_delay = comm_delay
        self.source = device_name.split("::")[0]
        self.target = device_name.split("::")[1].split(DEL)[0]

    # def exct(self, name, _last_end_time, step_idx):
    # 	if "PUSH_REQ" in name:
    # 		_last_end_time = max(_last_end_time + self.bw_delay, self.device_time)
    # 	super().exct(name, _last_end_time, step_idx)
    
    def mark_as_exct(self, name, _start_t, _end_time):
        next_name = self.op_counter.get_next_op(name)
        self._update_device_time(name, _end_time)
        self.replayer.node_status.pop(name)
        for _succ in self.replayer.dag.successors(name):
            if _succ in self.replayer.node_status:
                _status = self.replayer.node_status[_succ]
                _status["in_degree"] -= 1
                if _status["ready"] is None:
                    if self.comm_delay and (self.source, self.target) in self.comm_delay:
                        _status["ready"] = _end_time + self.comm_delay[(self.source, self.target)]
                    else:
                        _status["ready"] = _end_time
                else:
                    if self.comm_delay and (self.source, self.target) in self.comm_delay:
                        _status["ready"] = max(_end_time + self.comm_delay[(self.source, self.target)], _status["ready"])
                    else:
                        _status["ready"] = max(_end_time, _status["ready"])
                if _status["in_degree"] == 0:
                    self.replayer.insert_next_node(_succ, _status["ready"])

        if next_name is not None:
            if next_name in self.replayer.node_status:
                _status = self.replayer.node_status[next_name]
                _status["in_degree"] -= 1
                if _status["ready"] is None:
                    if self.comm_delay and (self.source, self.target) in self.comm_delay:
                        _status["ready"] = _end_time + self.comm_delay[(self.source, self.target)]
                    else:
                        _status["ready"] = _end_time
                else:
                    if self.comm_delay and (self.source, self.target) in self.comm_delay:
                        _status["ready"] = max(_end_time + self.comm_delay[(self.source, self.target)], _status["ready"])
                    else:
                        _status["ready"] = max(_end_time, _status["ready"])

                if _status["in_degree"] == 0:
                    pid = parse_pid_from_name(next_name)
                    self.replayer.insert_next_node(next_name, _status["ready"])
            else:
                SingleLogger().error("{} not in status!".format(next_name))
                exit(0)

class Replayer:
    def __init__(self, dag, _step_num, leaf_dirs, dump_path, comm_backend, byteps_graph, show_queue=False):
        self.dag = dag
        self.step_num = _step_num
        self.leaf_dirs = leaf_dirs
        self.dump_path = dump_path
        self.comm_backend = comm_backend
        self.byteps_graph = byteps_graph

        self.logger = logger_utils.SingleLogger()
        ### Delay information, the unit of 'delay' field should be ms
        self.delay_dict = None
        ### maintain node status
        self.node_status = {}
        self.device_dict = {}
        self.queue_status = None

        self.reset_replayer()
        if self.comm_backend == "BYTEPS":
            self.op_counter = ServerOpCounter(self.byteps_graph)

        self.show_queue = show_queue
        if not self.show_queue:	
            self.queue = []

    def pre_prepare(self):
        ''' Initialize nodes that need to be replayed first
        '''
        def map_in_degree(n):
            if self.comm_backend == "BYTEPS":
                if self.byteps_graph.is_server_comp(n):
                    return self.dag.in_degree(n) + 1
            return self.dag.in_degree(n)

        self.node_status = []
        for n in self.dag.nodes():
            self.node_status.append((n, {"in_degree": map_in_degree(n), "ready": None}))
            if self.show_queue:
                ### if we want to show queue statuts, need to initialize the device list first
                self.name2device(n)
        self.node_status = dict(self.node_status)

        ### prepare nodes to be executed on each device
        for n, _status in self.node_status.items():
            if _status["in_degree"] == 0:
                try:
                    assert CatName.COMM.value not in n
                except:
                    raise RuntimeError("Invalid nodes {} with in_degree=0".format(n))
                pid = parse_pid_from_name(n)
                _last_end = self.step_end_time[pid] if _status["ready"] is None else _status["ready"]
                self.insert_next_node(n, _last_end)
    
    def replay_one_iter(self, step_idx):
        self.debuger = ReplayDebuger(self)
        # self.debuger.monitor_node()
        self.pre_prepare()
        while True:
            if self.pop_one_node_exec(step_idx) == 1:
                break
        debug_utils.DebugRecorder().dump_traces(".")

    def pop_one_node_exec(self, step_idx):
        if self.show_queue:
            sorted_device = sorted(self.device_dict.values(), key=lambda x: x.device_time)
            for device in sorted_device:
                if len(device.queue) == 0:
                    continue
                (n, t) = device.queue.pop(0)
                if self.show_queue:
                    self.record_queue_status(t)
                device.exct(n, t, step_idx)
                return 0
            return 1  ### no operators to execute
        else:
            if len(self.queue) == 0:
                return 1
            else:
                (n, t) = self.queue.pop(0)
                device = self.name2device(n)
                device.exct(n, t, step_idx)
                return 0

    def record_queue_status(self, cur_time):
        if self.queue_status is None:
            self.queue_status = {"names": list(self.device_dict.keys()), 'data': []}
        self.queue_status['data'].append([cur_time] + [len(self.device_dict[name].queue) for name in self.queue_status['names']])

    def replay(self, _output=True):
        self.reset_replayer()
        _ts = time.time()
        for step_idx in range(self.step_num):
            self.replay_one_iter(step_idx)
        self.logger.info("Take %f s to replay one iteration" % ((time.time() - _ts)/float(self.step_num)))
        if _output:
            self.output_traces()
        
    def replayAndDelay(self, delay_dict_, _output=False, _filename=None):
        self.reset_replayer()
        self.delay_dict = delay_dict_
        self.replay_one_iter(0)
        if _output:
            self.output_traces(_filename=_filename)
        return self.step_end_time

    def insert_next_node(self, n, t):
        ''' This is acutally equal to a scheduler of an **Engine**
        n: node string
        t: start time of this node, do NOT take the device time into consideration
        '''
        if self.show_queue:
            device = self.name2device(n)
            #! TODO (huhanpeng): if OPs are ranked, 
            # just to substitute func to compare their ranks.
            self.insort_right(device.queue, (n, t), func=_schedule)
        else:
            self.insort_right(self.queue, (n, t), func=_schedule)

    def insort_right(self, a, x, lo=0, hi=None, func=None):
        """Insert item x in list a, and keep it sorted assuming a is sorted.
        If x is already in a, insert it to the right of the rightmost x.
        Optional args lo (default 0) and hi (default len(a)) bound the
        slice of a to be searched.
        """
        def fun_cmp(x1, x2):
            if func is None:
                return x1 < x2
            else:
                return func(x1, x2)

        if lo < 0:
            raise ValueError('lo must be non-negative')
        if hi is None:
            hi = len(a)
        while lo < hi:
            mid = (lo+hi)//2
            if fun_cmp(x, a[mid]):
                hi = mid
            else:
                lo = mid+1
        a.insert(lo, x)

    def name2device(self, n):
        pid = parse_pid_from_name(n)
        cat = parse_cat_from_name(n)
        if cat == "Comm":
            if "SEND" in n:
                device_id = gen_long_name(pid, cat, "SEND")
            elif "RECV" in n:
                device_id = gen_long_name(pid, cat, "RECV")
            elif "Sync" in n:
                device_id = gen_long_name(pid, cat, "Sync")
            else:
                device_id = None
                in_queue_type = False
                for sub_op in QueueType().ret_list():
                    if sub_op in n:
                        device_id = gen_long_name(pid, cat, "Kernel")
                        in_queue_type = True
                        break
                
                if not in_queue_type:
                    device_id = gen_long_name(pid, cat)
        else:
            device_id = gen_long_name(pid, cat)

        if device_id not in self.device_dict:
            if cat == CatName.COMM.value and self.comm_backend == "BYTEPS":
                self.device_dict[device_id] = self.create_ps_comm_device(device_id)
            elif "Sync" in device_id:
                self.device_dict[device_id] = self.create_device(device_id)
                # self.device_dict[device_id] = self.create_periodic_device(device_id)
            elif cat == CatName.COMM.value and "Kernel" in device_id:
                self.device_dict[device_id] = self.create_comm_kernel_device(device_id)
            else:
                self.device_dict[device_id] = self.create_device(device_id)

        return self.device_dict[device_id]		
        
    def create_device(self, device_name, infi_para=False):
        d = Device(device_name, self, comm_backend = self.comm_backend, infi_para=infi_para)
        return d

    def create_comm_kernel_device(self, device_name, infi_para=False):
        d = CommKernelDevice(device_name, self, comm_backend = self.comm_backend, infi_para=infi_para)
        return d

    def create_periodic_device(self, device_name, infi_para=False):
        d = PeriodicDevice(device_name, self, period=5, comm_backend = self.comm_backend, infi_para=infi_para)
        return d

    def create_ps_comm_device(self, device_name, infi_para=False):
        d = PSCommDevice(device_name, self, self.op_counter, comm_backend = self.comm_backend, infi_para=infi_para)
        return d

    def reset_replayer(self):
        # self.step_end_time = dict([(_d, 0.0) for _d in self.leaf_dirs])
        self.step_end_time = collections.defaultdict(float)
        self.rst_traces = []
        ### Reset all devices
        for _, device_ in self.device_dict.items():
            device_.reset()

        ### Ininitalize the execution graph as the depdency graph
        self.exct_dag = self.dag.copy()

    def dump_critical_path(self, file, critical_path):
        rst = {
            "traceEvents": [],
            "displayTimeUnit": "ms"
        }
        for trace in self.rst_traces:
            if trace["args"]["name"] in critical_path:
                trace["name"] = "Y"
            else:
                trace["name"] = "N"
            rst["traceEvents"].append(trace)
        with open(os.path.join(self.dump_path, file), 'w') as f:
            json.dump(rst, f)
    
    def paint_bw_comm_depend(self, one_pid = None):
        ''' Paint the timeline to show the dependency between BW nodes and Comm nodes
        * E.g., if there is an edge from BW.A to Comm.1.Sync, BW.A would be renamed with Comm.1.Sync
        * This is useful when considering tensor fusion
        * NOTE: may not adapt to operator fusion
        '''
        final_trace = []
        start_ts = None

        def map_bw_2_comm(node_):
            ''' Some nodes may have not corresponding traces
            * E.g., BW.A -> BW.B --> Comm.1, but BW.B has no trace,
            *   in this case, we will paint BW.A to show the dependency
            '''
            ret = []
            succs = list(self.dag.successors(node_))
            for succ_ in succs:
                if "Comm" in succ_:
                    ret.append(succ_)
                else:
                    assert "BW" in succ_
                    if self.dag.nodes[succ_]["avg"] == 0:
                        ret += map_bw_2_comm(succ_)
            return ret
        
        for trace in self.rst_traces:
            if one_pid is None or trace["pid"] != one_pid:
                continue
            if "BW" in trace["name"]:
                long_name = trace["args"]["name"]
                comm_succs = map_bw_2_comm(long_name)
                assert len(comm_succs) <= 1, (trace["name"], comm_succs)
                if len(comm_succs) > 0:
                    rawname = parse_rawname_from_name(comm_succs[0])
                    trace["name"] = "BW." + rawname
                else:
                    trace["name"] = "BW"
            elif "Comm" in trace["name"]:
                pass
            else:
                trace["name"] = "others"
            trace["pid"] += ".replay"
            if start_ts is None:
                start_ts = trace["ts"]
            trace["ts"] -= start_ts
            final_trace.append(trace)
        with open(os.path.join(self.dump_path, "compare_replay_real.json"), 'w') as f:
            json.dump(final_trace, f)

    def output_traces(self, _filename=None):
        #! Output the synthetic traces.
        rst = {
            "traceEvents": [],
            "displayTimeUnit": "ms"
        }
        for trace in self.rst_traces:
            rst["traceEvents"].append(trace)
        TraceManager(self.rst_traces, DirLevel.TRIAL).get_iter_time()
        filename = "synthetic.json" if _filename is None else _filename
        with open(os.path.join(self.dump_path, filename), 'w') as f:
            json.dump(rst, f)
        if self.show_queue:
            with open(os.path.join(self.dump_path, 'queue_status.json'), 'w') as f:
                json.dump(self.queue_status, f, indent=4)

    def daydream_dag(self, metadata):
        ''' Convert the DFG to that described in Daydream
            1. Only keep operators on one GPU, i.e. host0.rank0
            2. Use Coarse-grained Comm operators
        '''
        SingleLogger().info("Convert dag to dagdream_dag ...")
        if metadata.metainfo is None:
            raise ValueError(
                "meta info is None, --metadata_path should be set: {} is given".format(args_.metadata_path))
        _dag = nx.DiGraph()

        DAYDREAM_BW_RATIO = 0.85

        def wrap_add_edge(u, v):
            _dag.add_edge(u, v)

        one_pid = "host0.rank0" if self.comm_backend == "NCCL" else "traces_0.rank0"
        for u, v in self.dag.edges():
            u_pid, u_raw_name, u_cat, _ = parse_allinfo_from_name(u)
            v_pid, v_raw_name, v_cat, _ = parse_allinfo_from_name(v)
            ### Only keep operators on one GPU, i.e. host0.rank0
            if u_pid != one_pid or v_pid != one_pid:
                continue
            if u_cat == CatName.COMM.value:
                continue
            if v_cat == CatName.COMM.value:
                assert "BW" in u_raw_name

                ### find corresponding UPDATE operator
                op_type, op_name, _ = v_raw_name.split(".")
                last_comm_op = gen_long_name(
                    one_pid, "{}.{}.{}".format(op_type, op_name, QueueType().ret_list()[-1]))
                succs_ = list(self.dag.successors(last_comm_op))
                assert len(succs_) == 1, (last_comm_op)
                update_op = succs_[0]
                assert "UPDATE_" in update_op, (u, v, last_comm_op, update_op)

                tensor_list = re.findall("[0-9]+", op_name)
                fused_size = 0
                for tensor_id_str in tensor_list:
                    comm_op = gen_long_name(
                        one_pid, "{}.{}".format(op_type, tensor_id_str))
                    ### BW --> Comm
                    wrap_add_edge(u, comm_op)
                    ### Comm --> UPDATE
                    wrap_add_edge(comm_op, update_op)
                    tensor_id = int(tensor_id_str)
                    bw_in_g = 100 * DAYDREAM_BW_RATIO
                    tensor_size = metadata.tensor_id2size(tensor_id)
                    _dag.nodes[comm_op]["avg"] = tensor_size / (bw_in_g * 1e6)
                    fused_size += tensor_size
                SingleLogger().info("Fused Tensor Size for {}: {} MB".format(op_name, fused_size / (1024.0 * 1024.0)))
            else:
                if "BW" in u and "UPDATE_" in v:
                    raise ValueError(u, v)
                wrap_add_edge(u, v)
        for node_ in _dag.nodes():
            if "Comm" in node_:
                continue
            nx.set_node_attributes(_dag, {node_: self.dag.nodes[node_]})
        self.dag = _dag

class ReplayDebuger:
    def __init__(self, replayer):
        self.debug_nodes = {}
        self.dag = replayer.dag
        self.enabled = False

    def monitor_node(self, n):
        self.enabled = True
        self.debug_nodes[n] = {"todo": [u for u, _ in self.dag.in_edges(n)], "done": []}

    def mark_as_exct(self, u, v):
        ''' node `u` is executed, and handle the status of `v` if it is monitored
        and (u, v) must be an edge in self.dag
        '''
        if not self.enabled or v not in self.debug_nodes:
            return
        self.debug_nodes[v]["todo"].remove(u)
        self.debug_nodes[v]["done"].append(u)
    
    def _show_staue(self, node):
        todos = self.debug_nodes[node]["todo"]
        if len(todos) > 0:
            print("{} is waiting for {} nodes: {}".format(node, len(todos), str(todos)))

    def show_staue(self, nodes=None):
        if nodes is None:
            for node in self.debug_nodes.keys():
                self._show_staue(node)
        elif isinstance(nodes, list):
            for node in nodes:
                self._show_staue(node)
        else:
            self._show_staue(nodes)
