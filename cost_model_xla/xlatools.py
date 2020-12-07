import ctypes
from pathlib import Path
import subprocess
import os
import re

def _check_file_available_for_writing(path):
    p = Path(path)
    p_dir = p.resolve().parent
    if not p_dir.is_dir():
        p.mkdir(parents=True)

def _check_file_exist_for_reading(path):
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("Cannot find file {}".format(path))

def _check_arg_types(args, types):
    if len(args) != len(types):
        raise RuntimeError("Mismatch number of arguments and types in _check_arg_types. ({} v.s. {})".format(len(args), len(types)))
    for index, (arg, arg_type) in enumerate(zip(args, types)):
        if not isinstance(arg, arg_type):
            raise TypeError("Inappropriate argument type for argument {}. Expected {} but got {}".format(index, arg_type, type(arg)))

def compile_to_hlo(graph_path, config_path, dump_path_unopt, dump_path_opt, replay_exec=None):
    if replay_exec is None:
        replay_exec = "/root/tensorflow/bazel-bin/tensorflow/compiler/byteprofile_xlatools/tfcompile_hlo"
    _check_arg_types([graph_path, config_path, dump_path_unopt, dump_path_opt], [str] * 4)
    _check_file_exist_for_reading(graph_path)
    _check_file_exist_for_reading(config_path)
    _check_file_available_for_writing(dump_path_unopt)
    _check_file_available_for_writing(dump_path_opt)
    subprocess.run("CUDA_VISIBLE_DEVICES=0 {} --graph_path {} --config_path {} --unopt {} --opt {}".format(replay_exec, graph_path, config_path, dump_path_unopt, dump_path_opt), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, shell=True)

def replay_hlo(hlo_path, replay_exec=None):
    if replay_exec is None:
        replay_exec = "/root/tensorflow/bazel-bin/tensorflow/compiler/xla/tools/replay_computation_gpu"
    opt_1 = "--num_runs=800"
    opt_2 = "--use_fake_data=true"
    opt_3 = "--print_result=false"
    process = subprocess.run([replay_exec, opt_1, opt_2, opt_3, hlo_path], capture_output=True)
    output = process.stderr.decode("ascii")
    times = [float(line.split()[3][:-2]) for line in re.findall("Done executing in .*s:", output)]
    times = times[-20:]
    return sum(times) / len(times)

def replay_and_generate_kernel_sample(sample_id_start, hlo_path, tmp_dir, dataset_path, replay_exec=None):
    if replay_exec is None:
        replay_exec = "/root/tensorflow/bazel-bin/tensorflow/compiler/xla/tools/replay_computation_gpu"
    my_env = os.environ.copy()
    my_env["CUDA_VISIBLE_DEVICES"] = "0"
    opt_1 = "--num_runs=50"
    opt_2 = "--use_fake_data=true"
    opt_3 = "--print_result=false"
    opt_4 = "--dataset_path={}".format(dataset_path)
    opt_5 = "--temp_dir_path={}".format(tmp_dir)
    opt_6 = "--profile_start=30"
    opt_7 = "--profile_end=50"
    opt_8 = "--sample_id_start={}".format(sample_id_start)
    # subprocess.run(["CUDA_VISIBLE_DEVICES=0", replay_exec, opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8, hlo_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, env=my_env, shell=True)
    process = subprocess.run("CUDA_VISIBLE_DEVICES=0 {} {} {} {} {} {} {} {} {} {}".format(replay_exec, opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8, hlo_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=my_env, shell=True, check=True)

def extract_kernel_features_from_hlo(hlo_path, tmp_dir, extract_exec=None):
    if extract_exec is None:
        extract_exec = "/root/tensorflow/bazel-bin/tensorflow/compiler/xla/tools/extract_features_from_hlo"
    opt_1 = "--hlo_path={}".format(hlo_path)
    opt_2 = "--temp_dir_path={}".format(tmp_dir)
    subprocess.run([extract_exec, opt_1, opt_2], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)