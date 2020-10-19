import os 
import ujson as json

import networkx as nx
import traceback
import time
import sys
from pathlib import Path

import logger_utils
from trace_utils import *
from dag_utils import *
from collect import Collector
from replay import Replayer
from progress_utils import progressBar
import arg_utils
import debug_utils
import optimizer
# from cost_model_xla import XlaDataset, FusionCostModel

args = arg_utils.SingleArg().args
logger = logger_utils.SingleLogger(args.path.split(',')[0], 
    args.option, args.logging_level, 
    is_clean=args.clean, 
    show_progress=args.progress)
logger.info(args)
QueueType("NCCL")
debug_utils.DebugRecorder(is_enable=args.debug_traces)

sys.setrecursionlimit(1000000)

path_list = args.path.split(',')
""" Read traces and prepare statitic info"""
if args.option not in ['critical', 'combine', 'mapping', 'compare', "replay", "topo_sort", "collect", "3dcompare", "optimize"]:
    pm = PathManager(path_list[0])
    traces = read_traces(pm.search(FileName.TRACE))
    name2sta, cat2sta = return_stat(traces)

if args.option == "combine":
    rst = []
    for path in path_list:
        rst += read_traces(path)
    save_path = os.path.join(os.path.dirname(path_list[0]), "combine{}Json.json".format(len(path_list)))
    with open(save_path, 'w') as fp:
        json.dump(rst, fp)

### map from operators to CUDA kernels, path[0] should be operator level traces and path[1] should be kernel level traces
if args.option == "mapping":
    op_traces = sorted(read_traces(path_list[0]), key=lambda x: x["ts"])
    kernel_traces = sorted(read_traces(path_list[1]), key=lambda x: x["ts"])
    kernel_idx = 0
    max_bias = 0 # real ts of kernel traces = trace['ts'] - max_bias
    
    op_name2kernels = {}
    kernels_table = {}
    kernel_cnt = 0

    def kernel_trace_ts(_idx, _bias=None):
        return kernel_traces[_idx]['ts'] if _bias is None else kernel_traces[_idx]['ts'] - _bias

    for op_trace in op_traces:
        if 'args' not in op_trace or 'BW' in op_trace['name']:
            continue
        if 'FW' not in op_trace['name']:
            continue
        if op_trace['args']['cnt'] == 0:
            ### for the first iteration, check the bias
            while kernel_trace_ts(kernel_idx) < op_trace["ts"]:
                kernel_idx += 1
            while kernel_trace_ts(kernel_idx) < op_trace["ts"] + op_trace["dur"]:
                ### check those relatively large kernel-level traces
                ### it overlapping with a op-level trace but is not convered by that
                ### NOTE: time unit is `ns`
                if kernel_traces[kernel_idx]['dur'] > 100 and kernel_trace_ts(kernel_idx) + kernel_traces[kernel_idx]['dur'] > \
                    op_trace['ts'] + op_trace['dur']:
                    ### check the overlapping ratio, if the ratio > a threshold, take this kernel trace as a mapping from the op trace
                    overlapping_ratio = ((op_trace['ts'] + op_trace['dur']) - (kernel_trace_ts(kernel_idx))) / kernel_traces[kernel_idx]['dur']
                    if overlapping_ratio > 0.9:
                        bias = (kernel_trace_ts(kernel_idx) + kernel_traces[kernel_idx]['dur']) - (op_trace['ts'] + op_trace['dur'])
                        max_bias = max(bias, max_bias)
                        logger.info("Update kernel-level traces bias: {}".format(max_bias))
                kernel_idx += 1
        elif op_trace['args']['cnt'] == 1:
            ### for the second iteration, generate the mapping
            op_name2kernels[op_trace['name']] = []
            while kernel_trace_ts(kernel_idx, _bias=max_bias) < op_trace["ts"]:
                kernel_idx += 1
            while kernel_trace_ts(kernel_idx, _bias=max_bias) < op_trace["ts"] + op_trace["dur"]:
                if kernel_traces[kernel_idx]['name'] not in kernels_table:
                    kernels_table[kernel_traces[kernel_idx]['name']] = kernel_cnt
                    op_name2kernels[op_trace['name']].append(kernel_cnt)
                    kernel_cnt += 1
                else:
                    op_name2kernels[op_trace['name']].append(kernels_table[kernel_traces[kernel_idx]['name']])
                kernel_idx += 1
        else:
            assert op_trace['name'] in op_name2kernels
            while kernel_idx < len(kernel_traces) and kernel_trace_ts(kernel_idx, _bias=max_bias) < op_trace["ts"]:
                kernel_idx += 1
            while kernel_idx < len(kernel_traces) and kernel_trace_ts(kernel_idx, _bias=max_bias) < op_trace["ts"] + op_trace["dur"]:
                assert kernel_traces[kernel_idx]['name'] in kernels_table
                if kernels_table[kernel_traces[kernel_idx]['name']] not in op_name2kernels[op_trace['name']]:
                    logger.info("{} has addional kernels in the following iterations".format(op_trace['name']))
                kernel_idx += 1

    import xlsxwriter
    workbook = xlsxwriter.Workbook(os.path.join(os.path.dirname(path_list[1]), 'mapfrom_op2kernels.xlsx'))
    worksheet = workbook.add_worksheet("resnet50_v1_B32_1080Ti")
    row = 0
    for name, kernels in sorted(op_name2kernels.items()):
        if row == 0:
            # -- Output the header of the sheet
            worksheet.write(0, 0, "operator name")
            worksheet.write(0, 1, "kernel name")
        row += 1
        col = 0
        worksheet.write(row, col, name)
        for _k in kernels:
            col += 1
            worksheet.write(row, col, _k)
    worksheet = workbook.add_worksheet("kernel_name2index")
    row = 0
    for name, idx in sorted(kernels_table.items(), key=lambda x: x[1]):
        if row == 0:
            worksheet.write(0, 0, "index")
            worksheet.write(0, 1, "kernel name")
        row += 1
        worksheet.write(row, 0, idx)
        worksheet.write(row, 1, name)
    workbook.close()

if args.option == "statistic":
    """ Output the statistic results """
    # \TODO: device id
    def output(_name2sta):
        logger.info("Profile Statistics.")
        logger.info("===================")
        logger.info("%-60s\t Total Count\t Time (ms)\t Min Time (ms)\t Max Time (ms)\t Avg Time (ms)\t Variance (ms^2)" % "Name")
        logger.info("%-60s\t -----------\t ---------\t -------------\t -------------\t -------------\t ---------------" % "----")
        line_cnt = 0
        for name, statistic in _name2sta:
            if (args.head and line_cnt >= args.head):
                break        
            logger.info("%-60s\t %11d\t %9.4f\t %12.4f\t %13.4f\t %13.4f\t %13.4f" % 
                    (name,
                    statistic["cnt"],
                    statistic["time"],
                    statistic["min_t"],
                    statistic["max_t"],
                    statistic["avg"],
                    statistic["var"]
                    ))
            line_cnt += 1

    # output(sorted(name2sta.items(), lambda x, y: cmp(x[1]["avg"], y[1]["avg"])))
    if args.sort:
        sort_sta = sorted(name2sta.items(), key=lambda x: x[1]["avg"], reverse=True)
    else:
        sort_sta = name2sta.items()
    output(sort_sta)
    if args.xlsx:
        export2xlsx([name2sta], os.path.dirname(path_dict["trace_path"]))

    # Group by category
    logger.info("")
    logger.info("Group by category")
    logger.info("===================")
    line_cnt = 0
    for cat, statistic in cat2sta.items():
        if (args.head and line_cnt >= args.head):
                break
        logger.info("Category: %-10s\t The most time-consuming OP: %-30s -> %13.4f (ms)" % (cat, statistic["max_name"], statistic["max_t"] / 1000.0))
        line_cnt += 1

if args.option == "graph":
    mygraph = nx.read_gml(pm.search(FileName.DAG))
    visualize_gml(mygraph)

if args.option == "critical":
    ''' 
    Args:
        -- args.path: the dir of a worker, which contains multiple folders 
                        storing traces of GPUs of this worker
    '''
    assert pm.dir_level == DirLevel.WORKER
    #! used to store all dags generated from GPUs
    graphs = []
    for _dir in pm.dirs:
        dagmanager = DAGManager(os.path.join(pm.path, _dir), platform=args.platform)
        dagmanager.gen_dag_with_prefix_weight()
        dag_longest_path(dagmanager.dag, dagmanager.pm, weight="weight", default_weight=0)
        graphs.append(dagmanager.dag)

    graph = nx.compose_all(graphs)
    dag_longest_path(graph, pm, weight="weight", default_weight=0)

if args.option == "timeline":
    raise NotImplementedError()

if args.option == "replay":
    ''' Re-generate the timeline according to the dependency 
    graph with time for each node.
    Args:
        --path: the root path for 
        --step_num: number of steps we want to generate.
    '''    
    clct = Collector(path_list[0], comm_backend=args_.comm_backend, platform=args.platform)
    iter_times = clct.init(args.force)

    ### Replay traces
    logger.info("# Start to Replay")
    replayer = Replayer(dag=clct.trail_dag, 
            _step_num=args.step_num, 
            leaf_dirs=clct.all_prefix_list(), 
            dump_path=clct.pm.path,
            comm_backend=clct.comm_backend,
            byteps_graph=clct.byteps_graph)
    
    def replay_with_delay(idx_, rst, node_name=None):
        logger.info(node_name)
        delay_dict = {node_name: {"delay": -5, "ratio": 1}} if node_name is not None else None
        step_end_time = replayer.replayAndDelay(delay_dict, _ouput=True)
        for trace in replayer.rst_traces:
            trace["tid"] = "%d-->%s"%(idx_, trace["tid"] if "tid" in trace else "tid")
            rst.append(trace) 
        return idx_ + 1

    if args.sub_option is None:
        ''' Directly replay '''
        replayer.replay()
        cal_edge_cost(replayer.exct_dag)
        critical_path = dag_longest_path(replayer.exct_dag, clct.pm, weight="cost", default_weight=0, _debug_level=1)
    elif args.sub_option == "smlt_delay_cmp":
        ''' Replay with computation delays'''
        delay_dict = {"DELAY_ALL_CMP": {"delay": 0, "ratio": args.delay_ratio}}
        step_end_time = replayer.replayAndDelay(delay_dict, _output=True)
    elif args.sub_option == "smlt_delay_comm":
        ''' Replay with communication delays'''
        delay_dict = {"DELAY_ALL_COMM": {"delay": 0, "ratio": args.delay_ratio}}
        step_end_time = replayer.replayAndDelay(delay_dict, _output=True)
    elif args.sub_option == "map_delay":
        ''' Replay and add delays to each node respectively.'''
        iter_time = max([e[2] for e in iter_times])
        node_lists = list(wk_dag.nodes())
        total_len = len(node_lists)
        pgsbar = progressBar(start=0, end=total_len)
        idx = 0
        while idx < total_len:
            nodename = node_lists[idx]
            delay_dict = {nodename: {"delay": 10, "ratio": 1.0}}
            step_end_time = replayer.replayAndDelay(delay_dict, _ouput=False)
            logger.info("Delay %s ==> %s ==> %s critical path." % (nodename, str(step_end_time), "in" if nodename in critical_path else "not in"))
            if args.progress:
                pgsbar.showBar(idx)
            idx += 10

    elif args.sub_option == "bottleneck":
        ''' Replay and add delays to some of the node on the critical path respectively.'''
        ### Get the execution graph first
        replayer.replay()
        cal_edge_cost(replayer.exct_dag)

        critical_path = dag_longest_path(replayer.exct_dag, clct.pm, weight="cost", default_weight=0, _debug_level=2)
        critical_path = sorted(critical_path, key=lambda x: x[1], reverse=True)
        total_len = len(critical_path)
        pgsbar = progressBar(start=0, end=total_len)
        iter_time = max([e[2] for e in iter_times])
        idx = 0
        max_diff = 0
        bottleneckt_ = None

        while idx < total_len:
            nodename, node_len = critical_path[idx]
            if node_len == 0:
                idx += 1
                continue
            ### TODO (huhanpeng): change the value 10
            delay_dict = {nodename: {"delay": -5, "ratio": 1}}
            step_end_time_ms = [t / 1000 for t in replayer.replayAndDelay(delay_dict, _ouput=False).values()]
            cur_iter_time_ = max(step_end_time_ms)
            diff_ = cur_iter_time_ - iter_time if cur_iter_time_ > iter_time else iter_time - cur_iter_time_
            logger.info("Delay %s" % (nodename))
            logger.info(" ==> %s." % (str(step_end_time_ms)))
            if diff_ > max_diff:
                max_diff = diff_
                bottleneckt_ = nodename
            if args.progress:
                pgsbar.showBar(idx)
            ### TODO (huhanpeng): how to pick these nodes
            idx += 10
        logger.info("bottleneckt: %s" % bottleneckt_)
    elif args.sub_option == "compare":
        rst = []
        idx = 0
        idx = replay_with_delay(idx, rst)
        idx = replay_with_delay(idx, rst, "host0.rank1->FW.bertencoder0_transformer0_multiheadattentioncell0_batch_dot1")
        # idx = replay_with_delay(idx, rst, "host1.rank0->BW.bertencoder0_slice0")
        rst = sorted(rst, key=lambda x: (x["pid"], x["tid"]))
        with open(os.path.join(clct.pm.path, "replay_compare.json"), 'w') as f:
            json.dump(rst, f)

if args.option == "topo_sort":
    pm = PathManager(path_list[0])
    assert pm.dir_level == DirLevel.GPU
    local_rank = int(pm.path.split("/")[-1])
    dagmanager = DAGManager(pm.path, local_rank, platform=args.platform)
    dagmanager.gen_fw_bw_dag()

'''below options use special --path'''
if args.option == "compare":
    if len(path_list) < 2:
        raise ValueError("To compare two files, two paths must be given")
    if os.path.isfile(path_list[0]):
        traces = [read_traces(path_list[0]), read_traces(path_list[1])]
    else:
        clct = [Collector(path_list[0], comm_backend=args_.comm_backend, platform=args.platform), 
            Collector(path_list[1], comm_backend=args_.comm_backend, platform=args.platform)]
        traces = [c.iter_combine() for c in clct]
    name2sta = [return_stat(_traces)[0] for _traces in traces]
    name2compare = {}
    for name, statistic in name2sta[0].items():
        if name not in name2sta[1]:
            continue
        name2compare[name] = {
                "avg_absolute": name2sta[1][name]["avg"] - statistic["avg"],
                "avg_relative": (name2sta[1][name]["avg"] - statistic["avg"]) / statistic["avg"]
            }

    if args.sort:
        sort_sta = sorted(name2compare.items(), key=lambda x: x[1]["avg_relative"], reverse=True)
    else:
        sort_sta = name2compare.items()

    name2sta.append(name2compare)
    if args.xlsx:
        def gen_sheet_name(l):
            if len(l) >= 31:
                l = l[-31:]
            return "_".join(l.split("/")[1:])

        sheet_name = [gen_sheet_name(l) for l in path_list]
        sheet_name.append("comparison")
        export2xlsx(name2sta, 
            os.path.abspath(path_list[0]) if os.path.isdir(path_list[0]) else os.path.dirname(path_list[0]), 
            filename="compare",
            sheet_name=sheet_name)

    logger.info("Compare following two files:")
    logger.info("File 1: " + path_list[0])
    logger.info("File 2: " + path_list[1])
    logger.info("===================")
    logger.info("%-100s\t Absolute Avg Time Increase (ms)\t Relative Avg Time Increase" % "Name")
    line_cnt = 0
    for name, compare in sort_sta:
        if (args.head and line_cnt >= args.head):
            break    
        logger.info("%-100s\t %24.4f\t %24.4f" %
                (name, compare["avg_absolute"], compare["avg_relative"]))
        line_cnt += 1

if args.option == "collect":
    clct = Collector(path_list[0], comm_backend=args_.comm_backend, platform=args.platform)
    clct.init(args.force)
    if args.sub_option == "combine":
        clct.iter_combine()
    elif args.sub_option == "xlsx":
        clct.traceM.export2xlsx(path_list[0])
    elif args.sub_option == "visual_dag":
        clct.traceM.export2xlsx(path_list[0])
    elif args.sub_option == "iter_time":
        clct.iter_time()
    elif args.sub_option == "straggler":
        clct.detect_straggler1()
    elif args.sub_option == "bottleneck":
        clct.detect_bottleneck1()
    elif args.sub_option == "query":
        while True:
            name = input("\nQuerying: \n\t 1). The tensor name \n\t 2). \\sta_by_cnt \n\t 3). q or Q to quit \nInput your command: ")
            if name.lower() == "q":
                break
            elif "\\sta_by_cnt" in name or name == "2":
                clct.detect_straggler1()
            else:
                avg = clct.traceM.lookup_stat(None, None, name)
                print("Average time: %f ms" % (avg))

if args.option == "optimize":

    from cost_model_amp.amp_pred import AMPPredictor, AMPTrainer
    clct = Collector(path_list[0], comm_backend=args_.comm_backend, platform=args.platform)
    clct.init(args.force)
    amp_pred = AMPTrainer(
        os.path.join(path_list[0], "host0/0/metadata.json"),
        path_list[0])
    amp_pred.collect_raw_data(path_list[0])
    amp_pred.gen_train_data(clct.trail_dag)
    amp_pred.train(test=True)
    '''
    if len(path_list) < 2:
        raise RuntimeError("optimize requires positional path arguments: profile data path & cost model path.")
    clct = Collector(path_list[0], comm_backend=args_.comm_backend)
    models_dir = path_list[1]
    clct.init(args.force)
    cost_models = {}
    logger.info("Searching for model dumps in {}".format(models_dir))
    for model_dump in os.listdir(models_dir):
        model_path = os.path.join(models_dir, model_dump)
        p = Path(model_path)
        if p.is_file():
            node_name = p.stem
            cm = FusionCostModel(os.path.join(args.cost_model_tmp_dir, node_name))
            cm.load(model_path)
            cost_models[node_name] = cm
            logger.info("Added cost model for {}".format(node_name))
        else:
            logger.warn("{} not a file.".format(model_path))
    if args.optimizer == "MCTS":
        opt = optimizer.MCTSOptimizer(clct, cost_models=cost_models, ucb_type=args.ucb_type, no_mutation=args.no_mutation)
    elif args.optimizer == "MCMC":
        opt = optimizer.MCMCOptimizer(clct, cost_models=cost_models)
    opt.search()
    '''
### Output debug traces
debug_utils.DebugRecorder().dump_traces(path_list[0])



