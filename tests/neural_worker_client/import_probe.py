"""Fresh-process assertion: no application initializer or optional dependencies."""
import importlib.abc
import importlib.util
import sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self,name,*args):
        if name.split('.')[0] in {'torch','torchvision','spandrel','numpy','safetensors','PIL','PySide6','mtg_proxy_printer'}:
            raise AssertionError('Forbidden import: '+name)
sys.meta_path.insert(0,Block())
spec=importlib.util.spec_from_file_location('host_client_only',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
print('host-only')
