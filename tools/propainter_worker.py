"""Isolated upstream inference entry point used by FrameWeaver's bounded trial."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--width', type=int, required=True)
    parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--dilation', type=int, default=4)
    args = parser.parse_args()
    os.environ.setdefault('PYTORCH_ENABLE_MPS_FALLBACK', '1')
    import torch
    work, source = Path(args.work), Path(args.source)
    sys.path.insert(0, str(source))
    os.chdir(work)
    # Bound CPU helper work on Apple Silicon, including model initialization.
    torch.set_num_threads(min(4, os.cpu_count() or 1))
    if torch.backends.mps.is_available():
        # Upstream releases CUDA caches between stages; release the MPS cache too.
        # Otherwise cached Metal allocations can push a Mac into heavy swapping.
        torch.cuda.empty_cache = torch.mps.empty_cache
    from model.modules.flow_comp_raft import RAFT_bi
    from model.recurrent_flow_completion import RecurrentFlowCompleteNet
    from model.propainter import InpaintGenerator
    from functools import wraps
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
    phase(RAFT_bi, 'forward', 'flow')
    phase(RecurrentFlowCompleteNet, 'forward_bidirect_flow', 'complete')
    phase(InpaintGenerator, 'img_propagation', 'propagate')
    phase(InpaintGenerator, 'forward', 'inpaint')
    # TQDM uses carriage returns. Newlines let the parent report actual iteration progress.
    class ProgressOutput:
        def write(self, text):
            sys.__stderr__.write(text.replace('\r', '\n'))
        def flush(self):
            sys.__stderr__.flush()
    sys.stderr = ProgressOutput()
    stopped, peak = threading.Event(), [0]
    def sample():
        while not stopped.wait(.1):
            if torch.backends.mps.is_available():
                peak[0] = max(peak[0], torch.mps.driver_allocated_memory())
    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    started = time.monotonic()
    try:
        sys.argv = ['inference_propainter.py', '-i', str(work / 'frames'), '-m', str(work / 'mask.png'),
                    '-o', str(work / 'output'), '--width', str(args.width), '--height', str(args.height),
                    '--neighbor_length', '6', '--subvideo_length', '12', '--ref_stride', '12',
                    '--raft_iter', '10', '--save_fps', '12', '--mask_dilation', str(args.dilation)]
        runpy.run_path(str(source / 'inference_propainter.py'), run_name='__main__')
        if torch.backends.mps.is_available():
            torch.mps.synchronize()
        (work / 'metrics.json').write_text(json.dumps({'inference_seconds': round(time.monotonic() - started, 2),
            'device': 'mps' if torch.backends.mps.is_available() else 'cpu', 'peak_mps_driver_bytes': peak[0]}))
    finally:
        stopped.set()
        monitor.join(timeout=1)


if __name__ == '__main__':
    main()
