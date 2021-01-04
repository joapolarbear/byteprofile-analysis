''' Manage the parameter info of a DNN model
### TODO (huhanpeng): mitigrate to ml_platform/mxnet/metadata.py
'''
import re
from trace_utils import *

class ParameterDict:
    def __init__(self, _pm, platform, metadata_path=None):
        ### collect metadata
        if metadata_path is None:
            metadata_path = os.path.dirname(_pm.search(FileName.METADATA))
        if metadata_path is None:
            SingleLogger().error(
                "{} not found. Fail to load metadata".format(FileName.METADATA.value))
        
        if platform == "MXNET":
            from ml_platform.mxnet.metadata import MetaInfo
            SingleLogger().info("Use MXNET metadata")
        elif platform == "TENSORFLOW":
            from ml_platform.tensorflow.metadata import MetaInfo
            SingleLogger().info("Use TENSORFLOW metadata")
        else:
            raise NotImplementedError()

        self.metainfo = MetaInfo(metadata_path)
        self.cnt = len(self.metainfo.gradient_name_list)

    def gradient_name_list(self):
        return self.metainfo.gradient_name_list
    
    def gradient_num(self):
        return self.cnt
    
    def tensor_id2update_id(self, tensor_id):
        '''tensor id may be 'max' to return the maximum update id '''
        return self.metainfo.tensor2update[tensor_id]

    def name_to_tensor_id(self, name):
        return self.metainfo.gradient_name_list.index(name)

    def tensor_id_to_name(self, tensor_id):
        return self.metainfo.gradient_name_list[tensor_id]

    def tensor_id2size(self, tensor_id):
        tensor_size = self.metainfo.ret_tensor_size(tensor_id)
        return tensor_size
    
    def ret_metadata(self, *args, **kwargs):
        return self.metainfo.ret_metadata(*args, **kwargs)

    def ret_rawmeta(self, *args, **kwargs):
        return self.metainfo.ret_rawmeta(*args, **kwargs)
    
    def check_amp_lists(self, *args, **kwargs):
        return self.metainfo.check_amp_lists(*args, **kwargs)
    
    def parse_op_type(self, *args, **kwargs):
        return self.metainfo.parse_op_type(*args, **kwargs)
    
    def standarize_name(self, *args, **kwargs):
        return self.metainfo.standarize_name(*args, **kwargs)
    
    def ret_op_precision(self, *args, **kwargs):
        return self.metainfo.ret_op_precision(*args, **kwargs)
    
    def in_metadata(self, *args, **kwargs):
        return self.metainfo.in_metadata(*args, **kwargs)
    
    def is_const(self, *args, **kwargs):
        return self.metainfo.is_const(*args, **kwargs)
    
    def is_variable(self, *args, **kwargs):
        return self.metainfo.is_variable(*args, **kwargs)
