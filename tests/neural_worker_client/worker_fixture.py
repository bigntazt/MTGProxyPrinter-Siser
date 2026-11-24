"""Test-only protocol child. Copied into a private bundle as worker.py."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def emit(value): sys.stdout.buffer.write(canonical(value)+b'\n'); sys.stdout.buffer.flush()
parser=argparse.ArgumentParser()
for option in ('checkpoint','expected-sha256','model-id','device','session-dir'): parser.add_argument('--'+option)
args=parser.parse_args()
bundle=Path(__file__).parent
scenario=json.loads((bundle/'scenario.json').read_text())
mode=scenario.get('mode','normal')
session=Path(args.session_dir); session.mkdir()
(session/'input').mkdir(); (session/'output').mkdir()
token='a'*32
model={'id':args.model_id,'sha256':args.expected_sha256,'bytes':2406816,'architecture':'Compact',
       'scale':2,'input_channels':3,'output_channels':3}
settings={'precision':'float32','seed':2200,'torch_threads':4,'cudnn_benchmark':False,
          'cudnn_deterministic':True,'matmul_tf32':False,'cudnn_tf32':False,'deterministic_algorithms':False,
          'eval':True,'inference_mode':True,'passes':1,'tiling':None,'precision_fallback':False,
          'quantization':'round-nearest-ties-even-rgb8-v1'}
provenance={'implementation':'m33-neural-worker-v1','protocol':1,'model':model,
            'source_sha256':{name:digest((bundle/name).read_bytes()) for name in
                             ('worker.py','protocol.py','backend.py','reference-model.json')},
            'runtime':{'packages':{p:'test-fixture' for p in ('torch','torchvision','spandrel','numpy','safetensors','einops','packaging')},
                       'python':'test-fixture','device':args.device,'device_name':'test-only device',
                       'device_capability':[8,9],'cuda_runtime':'test-fixture','cudnn':91002,'driver':None,
                       'architecture':'Compact','scale':2,'input_channels':3,'output_channels':3,'settings':settings}}
ready={'protocol':1,'type':'ready','session':token,'fingerprint':digest(canonical(provenance)),
       'provenance':provenance,'timings':{'startup_seconds':0,'model_load_seconds':0},
       'capabilities':{'format':'RGB8','layout':'tightly-packed-row-major','scale':2,'max_input_pixels':4194304,
                       'max_requests':scenario.get('max_requests',4096),'max_line_bytes':8192,
                       'sequential':True,'cancel':False,'shutdown':'between-jobs'}}
for path,value in scenario.get('ready_changes',[]):
    obj=ready
    for key in path[:-1]: obj=obj[key]
    obj[path[-1]]=value
if scenario.get('rehash',True): ready['fingerprint']=digest(canonical(ready['provenance']))

if mode=='startup_silent': time.sleep(60)
if mode=='startup_partial':
    for _ in range(1000): sys.stdout.buffer.write(b' ');sys.stdout.buffer.flush();time.sleep(.02)
if mode=='startup_stderr':
    for _ in range(1000): sys.stderr.buffer.write(b'x'*4096);sys.stderr.buffer.flush();time.sleep(.02)
if mode=='early_exit': raise SystemExit(3)
if mode=='partial_eof': sys.stdout.buffer.write(b'{');sys.stdout.buffer.flush();raise SystemExit(0)
if mode=='malformed': sys.stdout.buffer.write(b'{not json}\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='duplicate_keys': sys.stdout.buffer.write(b'{"protocol":1,"protocol":1}\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='nonfinite': sys.stdout.buffer.write(b'{"protocol":1,"v":1e999}\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='utf16': sys.stdout.buffer.write(json.dumps(ready).encode('utf-16-be')+b'\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='utf32': sys.stdout.buffer.write(json.dumps(ready).encode('utf-32-be')+b'\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='invalid_utf8': sys.stdout.buffer.write(b'\xff\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='oversized': sys.stdout.buffer.write(b'x'*65537+b'\n');sys.stdout.buffer.flush();time.sleep(60)
if mode=='overflow':
    for _ in range(100): emit(ready)
    time.sleep(60)
if mode=='stderr_flood': sys.stderr.buffer.write(b'x'*200000+b'END-TAIL');sys.stderr.buffer.flush()
emit(ready)
if mode=='no_stdin': time.sleep(60)
jobs=0
for line in sys.stdin.buffer:
    request=json.loads(line.decode('utf-8'))
    with (bundle/'requests.jsonl').open('ab') as log: log.write(canonical(request)+b'\n')
    identifier=request['id']
    if request['type']=='shutdown':
        if mode=='shutdown_hang': time.sleep(60)
        emit({'protocol':1,'type':'bye','session':token,'id':identifier if mode!='bad_bye' else 'bad','reason':'shutdown'})
        raise SystemExit(0)
    jobs+=1
    if mode=='job_hang': time.sleep(60)
    if mode=='job_eof': raise SystemExit(4)
    if mode=='request_error' and jobs==1:
        emit({'protocol':1,'type':'request_error','session':token,'id':identifier,'code':'test_reject','detail':'test rejection'})
        continue
    if mode=='fatal':
        emit({'protocol':1,'type':'fatal','session':token,'id':identifier,'code':'cuda_oom','detail':'test OOM'})
        raise SystemExit(1)
    samples=(session/'input'/f'job-{identifier}.rgb8').read_bytes()
    assert len(samples)==request['width']*request['height']*3 and digest(samples)==request['sha256']
    data=b'\x10\x20\x30'*(request['width']*request['height']*4)
    path=session/'output'/f'job-{identifier}.rgb8'
    if mode!='missing_file': path.write_bytes(data[:-1] if mode=='partial_file' else (b'x'*len(data) if mode=='corrupt_file' else data))
    if mode=='file_only': time.sleep(60)
    result={'protocol':1,'type':'result','session':token,'id':identifier,'fingerprint':ready['fingerprint'],
            'input':{'sha256':request['sha256'],'width':request['width'],'height':request['height']},
            'output':{'sha256':digest(data),'width':request['width']*2,'height':request['height']*2,'scale':2,
                      'format':'RGB8','bytes':len(data),'file':f'output/job-{identifier}.rgb8'},
            'timings':{'inference_seconds':0,'quantization_seconds':0,'publication_seconds':0}}
    for keys,value in scenario.get('result_changes',[]):
        obj=result
        for key in keys[:-1]: obj=obj[key]
        obj[keys[-1]]=value
    emit(result)
    if mode=='duplicate_result': emit(result)
