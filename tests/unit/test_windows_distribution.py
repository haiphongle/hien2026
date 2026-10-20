"""Windows portability contracts; no account changes or model downloads."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from agent.services import media_jobs, tts_runtime


def test_windows_pe_architecture_rejects_non_executables(tmp_path):
    file = Path(__file__).resolve().parents[2]/'desktop/build-windows.py'
    spec = importlib.util.spec_from_file_location('windows_builder',file)
    builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    executable = tmp_path/'test.exe'
    data=bytearray(128);data[:2]=b'MZ';data[0x3c:0x40]=(64).to_bytes(4,'little')
    data[64:70]=b'PE\0\0\x64\x86';executable.write_bytes(data)
    assert builder.pe_arch(executable)==0x8664
    executable.write_bytes(b'not an executable')
    with pytest.raises(ValueError): builder.pe_arch(executable)


def test_windows_pipe_reader_reuses_worker_and_reaps_on_close(tmp_path, monkeypatch):
    script=tmp_path/'test.py'
    script.write_text("import sys,json\nfor line in sys.stdin:\n item=json.loads(line)['items'][0];print(json.dumps({'event':'result','results':[{'text':item['text']}]}),flush=True)",encoding='utf-8')
    worker=tts_runtime.ManagedTTSWorker('kokoro',sys.executable,tmp_path,'test',script)
    # Exercise the Windows pipe path on macOS too, without replacing pathlib's OS.
    monkeypatch.setattr(tts_runtime,'os',SimpleNamespace(name='nt',read=os.read,environ=os.environ))
    try:
        assert worker.generate({'items':[{'id':'one','text':'Tiếng Việt · 中文'}]})==[{'text':'Tiếng Việt · 中文'}]
        pid=worker._process.pid;reader=worker._reader
        assert worker.generate({'items':[{'id':'two','text':'Lần thứ hai'}]})==[{'text':'Lần thứ hai'}]
        assert worker._process.pid==pid and worker._reader is reader
    finally: worker.close()
    assert not psutil.pid_exists(pid) and not reader.is_alive()


@pytest.mark.asyncio
async def test_media_timeout_kills_owned_child_and_grandchild():
    pids=[]
    code="import subprocess,sys,time,json,os; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); print(json.dumps([os.getpid(),child.pid]),flush=True);time.sleep(60)"
    def line(value):
        if value.startswith('['): pids.extend(json.loads(value))
    with pytest.raises(TimeoutError):
        await media_jobs.process([sys.executable,'-u','-c',code],timeout=3,on_line=line)
    assert len(pids)==2
    def alive(pid):
        try: return psutil.Process(pid).status()!=psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess: return False
    deadline=time.monotonic()+3
    while any(alive(pid) for pid in pids) and time.monotonic()<deadline: await asyncio.sleep(.05)
    assert not any(alive(pid) for pid in pids)
