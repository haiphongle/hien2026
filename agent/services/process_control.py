"""Process groups on POSIX; owned child trees without console windows on Windows."""
import os
import signal
import subprocess

import psutil


def spawn_options():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def kill_tree(pid):
    # Callers register only processes they started, never persisted/user PIDs.
    if os.name != 'nt':
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return
    for process in [*reversed(children), parent]:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
