import os 
import json
import networkx as nx
import traceback
import time

from dag_utils import QueueType
from trace_utils import lookup_stat, split_name

class Replayer:
	'''Used to replay distributed training

	Parameters
	----------
	_all_name2sta: dict
		A dict storing all static info on onw worker.
		The value of key "traces" is list of dict, 
		The i th dict element stores the name2sta of the GPU with local_rank=i
	_local_size: int
		The number of GPUs on one worker.
	_wk_dag: networkx.classes.digraph.DiGraph
		The combined execution order graph on one worker.
	_step_num: int
		The number of steps to replay.
	_path: str
		The path to store the synthetic traces.
	_logger:logging.Logger
		The logger used to output logging
	'''
	def __init__(self, _all_name2sta, _local_size, _wk_dag, _step_num, _path, _logger):
		self.all_name2sta = _all_name2sta
		self.step_end_time = [0.0] * _local_size
		self.wk_dag = _wk_dag
		self.step_num = _step_num
		self.path = _path
		self.logger = _logger

		#! Inital next_nodes, start replay from I/O nodes of each GPU
		self.next_nodes = set(["rank%d."%i + "I/O" for i in range(_local_size)])
		self.rst_traces = []
		self.loop_cnt = 0

	def record_end_time(self, _name, _end_time):
		''' Record the latest end time for current node
		
		Parameters
		----------
		_name: str
			The name of the node we want to record.
		_end_time: int or long
			The latest end time.
		'''
		if "rank" not in _name:
			if _name not in self.all_name2sta:
				self.all_name2sta[_name] = {"latest_end" : _end_time}
			else:
				self.all_name2sta[_name]["latest_end"] = _end_time
			return 0

		_local_rank, _raw_name = split_name(_name)
		_name2sta = self.all_name2sta["traces"][_local_rank]
		if _raw_name not in _name2sta:
			_name2sta[_raw_name] = {"latest_end" : _end_time}
		else:
			_name2sta[_raw_name]["latest_end"] = _end_time
		return 0

	def has_arrived(self, _name):
		'''Check whether a node has been replayed.
		The node may be in other GPUs, so use all_name2sta

		Return
		------
		_arrive: bool
			A node has been replayed if set True
		_local_rank: int
			The local rank of the GPU which generates this op.
		_raw_name: str
			If the node is not a shared node, e.g. Sync nodes, 
			return its original name without local rank
		_name2sta: dict
		step_end_time: int
			The latest step end time of current GPU.
		'''
		if "rank" not in _name:
			#! shared nodes across GPUs
			_arrive = _name in self.all_name2sta and "latest_end" in self.all_name2sta[_name] and self.all_name2sta[_name]["latest_end"] >= 0
			return _arrive, None, None, None, 0
		else:
			_local_rank, _raw_name = split_name(_name)
			_name2sta = self.all_name2sta["traces"][_local_rank]
			_arrive = _raw_name in _name2sta and "latest_end" in _name2sta[_raw_name] and _name2sta[_raw_name]["latest_end"] >= 0
			return _arrive, _local_rank, _raw_name, _name2sta, self.step_end_time[_local_rank]

	def _reproduce_one_op(self, name, reserve=False, FIXED_GAP_us=10):
		''' Process one op, if the op has been replayed, ignore it, 
		or process all its dependent upstream nodes in a DFS manner,
		and process its successor nodes in a BFS manner, by adding them to a set.

		Parameters
		----------
		reserve: bool
			If this is set True, denotes this is a call from a child node to its parent node
				!!!require the graph ends with one node (otherwise some nodes may be missed)!!!
				!!!A dense graph (or the recursion depth is too large)!!!
		FIXED_GAP_us: int
			A synthetic fixed gap between two steps.
		'''
		self.logger.debug("Name: %s, call parents?: %s" % (name, "True" if reserve else "False"))
		#! avoid repeated processing
		arrive_flag, _local_rank, raw_name, _name2sta, _last_end_time = self.has_arrived(name)
		if arrive_flag:
			#! When being re-called from its successors, this node must have finished
			#! Directly return the end time of the node
			return lookup_stat(self.all_name2sta, name, "latest_end")

		self.loop_cnt += 1
		for u, v in self.wk_dag.in_edges(name):
			arrive_flag, _, _, _, _ = self.has_arrived(u)
			if arrive_flag:
				_last_end_time = max(_last_end_time, lookup_stat(self.all_name2sta, u, "latest_end"))
			else:
				#! Use recursive/dfs to process parents, be
				_last_end_time = max(_last_end_time, self._reproduce_one_op(u, reserve=True))

		def call_successor(_name):
			if not reserve:
				for _succ in self.wk_dag.successors(_name):
					self.next_nodes.add(_succ)

		#! All dependent nodes have been processed
		if "I/O" in name:
			cat = tid = "I/O"
			pid = name
		elif "Comm" in name:
			cat = "Comm"
			_name_split = name.split(".")
			assert len(_name_split) >= 2
			if _name_split[-2] in QueueType:
				#! sub-task
				pid = ".".join(_name_split[:-2])
				tid = _name_split[-1]
			else:
				#! main task
				pid = name
				tid = "total"
		elif "FW" in name or "BW" in name or "STEP" in name:
			pid = "rank%d.operator"%_local_rank
			cat = "operator"
			tid = "tmp"
		elif "OUTPUT" in name or "Sync" in name:
			#! No event is generated, but has successors
			self.record_end_time(name, _last_end_time)
			call_successor(name)
			return _last_end_time	
		else:
			raise ValueError("Unknown node name: " + name)
		
		#! Some BW nodes of dag is not profiled, ignore them.
		if raw_name not in _name2sta or "avg" not in _name2sta[raw_name]:
			self.logger.warning("%s is not in _name2sta" % name)
			self.record_end_time(name, _last_end_time)
			call_successor(name)
			return _last_end_time

		_dur = 1000 * _name2sta[raw_name]["avg"]
		self.rst_traces.append({
				"name": name,
				"ts": _last_end_time + FIXED_GAP_us ,
				"dur": _dur,
				"pid": pid,
				"cat": cat,
				"ph": "X",
				"tid": tid
			})
		
		if "STEP" in name:
			#! current STEP of this GPU ends
			self.step_end_time[_local_rank] = _last_end_time + FIXED_GAP_us + _dur
			return self.step_end_time[_local_rank]	
		else:
			self.record_end_time(name, _last_end_time + FIXED_GAP_us + _dur)
			call_successor(name)
			return _last_end_time + FIXED_GAP_us + _dur

	def replay(self):
		for step_idx in range(self.step_num):
			time_before_gen = time.time()
			while len(self.next_nodes) > 0:
				self._reproduce_one_op(self.next_nodes.pop())
			#! prepare for the next step
			if step_idx == 0:
				self.logger.info("One step time: %s ms" % (str([_t / 1000.0 for _t in self.step_end_time])))
				self.logger.info("Take %f s and %d loops to produce %d events" % 
				(time.time() - time_before_gen, self.loop_cnt, len(self.rst_traces)))
			for key, value in self.all_name2sta.items():
				if key == "traces":
					for _name2sta in value:
						for _, _v in _name2sta.items():
							_v["latest_end"] = -1
				else:
					value["latest_end"] = -1
			self.loop_cnt = 0

		#! Output the synthetic traces.
		rst = {
			"traceEvents": self.rst_traces,
			"displayTimeUnit": "ms"
		}
		with open(os.path.join(self.path, "synthetic.json"), 'w') as f:
			json.dump(rst, f, indent=4)