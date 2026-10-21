"""Bounded upstream inference with models shared across a task's windows."""
import argparse
import gc
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time
from functools import wraps


def cache_model(cls, cache):
    """Upstream constructs the same three models on every run; retain only weights.

    Keep the upstream inference algorithm intact. The fixed source/weights belong
    to one task; frame tensors and runpy globals must never enter this cache.
    """
    original_init = cls.__init__
    def allocate(model_cls, *args, **kwargs):
        return cache[cls] if cls in cache else object.__new__(model_cls)
    @wraps(original_init)
    def initialize(self, *args, **kwargs):
        if cls not in cache:
            print('FLOWKIT_STAGE:load', flush=True)
            original_init(self, *args, **kwargs)
            cache[cls] = self
    # Preserve the class object: upstream uses explicit super(Class, self).
    cls.__new__ = staticmethod(allocate)
    cls.__init__ = initialize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work')
    parser.add_argument('--source', required=True)
    parser.add_argument('--width', type=int)
    parser.add_argument('--height', type=int)
    parser.add_argument('--dilation', type=int, default=4)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    import torch
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source))
    # Bound CPU helper work on Apple Silicon, including model initialization.
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    if torch.backends.mps.is_available():
        # Upstream releases CUDA caches between stages; release the MPS cache too.
        # Otherwise cached Metal allocations can push a Mac into heavy swapping.
        torch.cuda.empty_cache = torch.mps.empty_cache
    from model.modules import flow_comp_raft
    from model import recurrent_flow_completion, propainter
    reported = set()
    def phase(cls, method, name):
        original = getattr(cls, method)
        @wraps(original)
        def call(*args, **kwargs):
            if name not in reported:
                reported.add(name)
                print(f'FLOWKIT_STAGE:{name}', flush=True)
            return original(*args, **kwargs)
        setattr(cls, method, call)
    phase(flow_comp_raft.RAFT_bi, 'forward', 'flow')
    phase(recurrent_flow_completion.RecurrentFlowCompleteNet, 'forward_bidirect_flow', 'complete')
    phase(propainter.InpaintGenerator, 'img_propagation', 'propagate')
    phase(propainter.InpaintGenerator, 'forward', 'inpaint')
    models = {}
    cache_model(flow_comp_raft.RAFT_bi, models)
    cache_model(recurrent_flow_completion.RecurrentFlowCompleteNet, models)
    cache_model(propainter.InpaintGenerator, models)
    # TQDM uses carriage returns. Newlines let the parent report actual iteration progress.
    class ProgressOutput:
        def write(self, text):
            sys.__stderr__.write(text.replace('\r', '\n'))
        def flush(self):
            sys.__stderr__.flush()
    sys.stderr = ProgressOutput()
    def infer(job):
        work = Path(job['work']).resolve()
        reported.clear()
        before_loads = len(models)
        stopped, peak = threading.Event(), [0]
        def sample():
            while not stopped.wait(.1):
                if torch.backends.mps.is_available():
                    peak[0] = max(peak[0], torch.mps.driver_allocated_memory())
        monitor = threading.Thread(target=sample, daemon=True)
        monitor.start()
        started = time.monotonic()
        try:
            os.chdir(work)
            sys.argv = ['inference_propainter.py', '-i', str(work / 'frames'), '-m', str(work / 'mask.png'),
                        '-o', str(work / 'output'), '--width', str(job['width']), '--height', str(job['height']),
                        '--neighbor_length', '6', '--subvideo_length', '12', '--ref_stride', '12',
                        '--raft_iter', '10', '--save_fps', '12', '--mask_dilation', str(job['dilation'])]
            # Discard the returned globals (including all frames and tensors).
            runpy.run_path(str(source / 'inference_propainter.py'), run_name='__main__')
            if torch.backends.mps.is_available():
                torch.mps.synchronize()
            (work / 'metrics.json').write_text(json.dumps({'inference_seconds': round(time.monotonic() - started, 2),
                'device': 'mps' if torch.backends.mps.is_available() else 'cpu', 'peak_mps_driver_bytes': peak[0],
                'model_loads': len(models) - before_loads, 'worker_pid': os.getpid()}))
        finally:
            stopped.set()
            monitor.join(timeout=1)
            os.chdir(source)
            gc.collect()
            torch.cuda.empty_cache()

    if not args.serve:
        if not args.work or not args.width or not args.height:
            parser.error('--work, --width and --height are required without --serve')
        infer(vars(args))
        return
    # stdin EOF also exits if the server disappears between windows.
    for line in sys.stdin:
        try:
            infer(json.loads(line))
            print('FLOWKIT_DONE:{"ok":true}', flush=True)
        except Exception as exc:
            print('FLOWKIT_DONE:' + json.dumps({'ok': False, 'error': str(exc)}), flush=True)
            return


if __name__ == '__main__':
    main()
