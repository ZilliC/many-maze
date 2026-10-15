"""H-071: tracking worker processes end when the program that started them is killed."""

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

CHILD = textwrap.dedent("""
    import multiprocessing as mp, os, sys, time
    from concurrent.futures import ProcessPoolExecutor
    from manymaze.core import batch

    if __name__ == "__main__":
        ctx = mp.get_context("spawn")
        q, stop = ctx.Queue(), ctx.Event()
        with ProcessPoolExecutor(2, mp_context=ctx, initializer=batch._init_worker,
                                 initargs=(q, stop, 1, os.getpid())) as pool:
            pids = {pool.submit(batch._probe_pid).result() for _ in range(6)}
            print(" ".join(map(str, pids)), flush=True)
            time.sleep(120)
""")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="kill -9 / parent pid check is for POSIX")
def test_workers_exit_when_the_parent_is_killed(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, env=env)
    pids = [int(x) for x in proc.stdout.readline().split()]
    assert pids and all(_alive(p) for p in pids)
    proc.send_signal(signal.SIGKILL)
    proc.wait()
    t0 = time.time()
    try:
        while any(_alive(p) for p in pids) and time.time() - t0 < 15:
            time.sleep(0.2)
        assert not any(_alive(p) for p in pids), "workers survived the death of their parent"
    finally:
        for p in pids:
            if _alive(p):
                os.kill(p, signal.SIGKILL)
