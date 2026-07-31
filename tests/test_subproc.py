import sys, time
from ragspine.core.subproc import run_isolated

def test_ok():
    rc, out, _ = run_isolated([sys.executable, "-c", "print('hi')"])
    assert rc == 0 and out.strip() == "hi"

def test_timeout_kills_tree():
    t0 = time.time()
    rc, _, _ = run_isolated([sys.executable, "-c",
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"],
        timeout=2)
    assert rc != 0 and time.time() - t0 < 10
