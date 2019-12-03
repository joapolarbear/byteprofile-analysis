import networkx as nx
import matplotlib.pyplot as plt
from trace_utils import read_traces, return_stat

QueueType = [
  "COORDINATE_REDUCE",
  "REDUCE",
  "COPYD2H",
  "PCIE_REDUCE",
  "COORDINATE_PUSH",
  "PUSH",
  "PULL",
  "COPYH2D",
  "COORDINATE_BROADCAST",
  "BROADCAST",
  "QUEUE_NUM_AND_NOT_A_REAL_QUEUE_TYPE_AND_MUST_BE_THE_LAST"
]

def visualize_gml(graph, layout="circular"):
  if layout == "spectral":
    pos = nx.spectral_layout(graph, dim=2, scale=0.5)
  elif layout == "circular":
    pos = nx.circular_layout(graph)
  elif layout == "random":
    pos = nx.random_layout(graph)
  nx.draw(graph, pos, with_labels=True, font_size=6)
  plt.show()
  # import matplotlib.pyplot as plt; plt.ion()
  # import netgraph
  # netgraph.draw(graph)
  # plot_instance = netgraph.InteractiveGraph(graph, node_positions=pos)
  # node_positions = plot_instance.node_positions

def dag_longest_path(G, local_rank, logger, weight='weight', default_weight=0):
  critical_path = nx.algorithms.dag.dag_longest_path(G, weight=weight, default_weight=default_weight)
  prefix = "Critical Path of " + ("the Entire Graph: " if local_rank == -1 else "GPU-%d: " % local_rank)
  logger.info(prefix + " => ")
  path_length = 0
  for (u, v) in nx.utils.pairwise(critical_path):
    path_length += G[u][v].get(weight, default_weight)
    logger.info("%s -> %s: %f ms" % (u, v, G[u][v].get(weight, default_weight)))
  # logger.info(prefix + str(critical_path) + " => " + prefix + "%12.4f ms" % path_length)
  logger.info("Length of the " + prefix + "%12.4f ms\n" % path_length)


def gen_dag_from_gml_and_traces(name2sta, gml_path, rank, del_queue, logger):
  '''
  Return: A dag, containing FW, BW, OUTPUT, Comm, I/O and Sync nodes
    node names start with 'rank{id}.'
  '''
  mygraph = nx.read_gml(gml_path)
  dag = nx.DiGraph()
  def add_prefix(name):
    return "rank%d."%rank + name
  def _read_stat(node_name, _assert=False):
    return name2sta[node_name]["avg"] if node_name in name2sta else 0.0

  for u, v in mygraph.edges:
    if "Comm" in u:
      if del_queue == True:
        prev_nodes = [_u for _u, _ in mygraph.in_edges(u)]
        assert len(prev_nodes) == 1
        prev_node = prev_nodes[0]
        for suffix in QueueType[-1:]:
          cur_node = u + '.' + suffix
          if _read_stat(cur_node) == 0:
            continue
          dag.add_edge(add_prefix(prev_node), add_prefix(cur_node), weight=_read_stat(prev_node))
          prev_node = cur_node
        dag.add_edge(add_prefix(prev_node), "Sync", weight=_read_stat(prev_node))
      else:
        dag.add_edge(add_prefix(u), "Sync", weight=_read_stat(u))
    else:
      dag.add_edge(add_prefix(u), add_prefix(v), weight= _read_stat(u)) 
  for e in dag.edges.data("weight"):
    logger.debug(e)
  # visualize_gml(dag, layout="circular")
  return dag

def gen_gpu_dag(traces, name2sta, path_dict, del_queue, logger, _pretty=False):
  traces = sorted(traces, key=lambda x: (x["ts"], x["name"]), reverse=False)
  mygraph = gen_dag_from_gml_and_traces(name2sta, path_dict["gml_path"], del_queue, path_dict["local_rank"], logger)
  prefix = "rank%d."%path_dict["local_rank"]

  in_process_events = []
  max_para_degree = 1
  first = True
  start_time = None
  def relative_time(time):
    return (time - start_time) / 1000.0
  gpu_dag = nx.DiGraph()
  arrive_dict = set()
  for node in mygraph.nodes:
    if "FW" in node or "BW" in node:
      arrive_dict.add(".".join(node.split(".")[1:]))
  logger.info("Total number of operators: %d" % len(arrive_dict))
  
  #! Go through one step of traces
  for event in traces:
    if first:
      logger.info("The first event - name: %s, ts: %s, dur: %s" %
        (event["name"], str(event["ts"]), str(event["dur"])))
      start_time = event["ts"]
      first = False

    #! only consider FW and BW nodes
    if event["cat"] != "operator":
      continue

    node_name = event["name"]
    if node_name in arrive_dict:
      arrive_dict.remove(node_name)
      if len(arrive_dict) == 0:
        break
    else:
      continue

    i = 0
    while True:
      if i >= len(in_process_events):
          break
      prev_event = in_process_events[i]
      assert event["ts"] >= prev_event["ts"]
      if event["ts"] >= prev_event["ts"] + prev_event["dur"]:
        #! prev event has ended, should be deleted from in_process_events
        del in_process_events[i]
        #! TODO: only add once, to verify
        gpu_dag.add_edge(prefix + prev_event["name"], prefix + event["name"], weight=name2sta[prev_event["name"]]["avg"])
      else:
        parent_list_of_prev = [(u, gpu_dag.edges[(u, v)]["weight"]) for u, v in gpu_dag.in_edges(prefix + prev_event["name"])]
        for u, w in parent_list_of_prev:
          gpu_dag.add_edge(u, prefix + event["name"], weight=w)
        i += 1

    if len(in_process_events) + 1 > max_para_degree:
      max_para_degree = len(in_process_events) + 1

    def in_process_events2str():
      s = ''
      for _event in in_process_events:
        _n, _ts, _te = _event["name"], _event["ts"], _event["ts"] + _event["dur"]
        s += "\n\t\t\t\t%-60s: %s~%s (%-13.4f ~ %-13.4f)" % (_n, str(_ts), str(_te), relative_time(_ts), relative_time(_te))
      return s

    if not _pretty and len(in_process_events) > 0:
      logger.info("%s (%-13.4f): D=%d => %-60s%s" %
        (event["ts"], relative_time(event["ts"]),
          len(in_process_events)+1,
          event["name"], 
          in_process_events2str()))
    in_process_events.append(event)

  logger.info("max_para_degree: %d" % max_para_degree)

  #! Then, read IO, Comm, OUTPUT, and Sync nodes
  def is_computation_node(_node):
    return "BW" in _node or "FW" in _node
  for u, v in mygraph.edges:
    if is_computation_node(u) and is_computation_node(v):
      #! ignore edges whose u v are both computation nodes
      continue
    gpu_dag.add_edge(u, v, weight=mygraph.edges[(u, v)]["weight"])

  if not _pretty:
    dag_longest_path(gpu_dag, path_dict["local_rank"], logger, weight="weight", default_weight=0)

  return gpu_dag, max_para_degree
