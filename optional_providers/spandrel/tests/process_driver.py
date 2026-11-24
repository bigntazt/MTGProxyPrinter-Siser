"""Test-only child entry point. Never exposed as a production worker option."""
import importlib.abc
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,*args):
        if fullname.split('.')[0] in {'torch','torchvision','numpy','spandrel','safetensors','PySide6','mtg_proxy_printer'}:
            raise ImportError('Optional/application import forbidden in inert protocol test')
sys.meta_path.insert(0,BlockOptional())
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import worker

if sys.argv[1]=='--hang':
    # A controlled test child for the parent's timeout/kill/wait path.
    import time
    time.sleep(30)
    raise SystemExit(0)

class Inert:
    facts={'test_only':True}
    load_seconds=0
    def __init__(self,*args): print('test diagnostic')
    def infer(self,*args): raise AssertionError('Driver receives shutdown only')

config=SimpleNamespace(model_id='nomos_compact_2x',device='cuda:0',session_dir=sys.argv[1],checkpoint='unused',expected_sha256='unused')
with patch.object(worker,'load_checkpoint',return_value=b'test'):
    raise SystemExit(worker.serve(config,sys.stdin.buffer,sys.stdout,sys.stderr,Inert))
