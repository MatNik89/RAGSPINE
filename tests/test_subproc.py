import io, os, sys, time
from atlas.core.subproc import run_isolated, run_streaming


def test_ok():
    rc, out, _ = run_isolated([sys.executable, "-c", "print('hi')"])
    assert rc == 0 and out.strip() == "hi"

def test_timeout_kills_tree():
    t0 = time.time()
    rc, _, _ = run_isolated([sys.executable, "-c",
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); time.sleep(60)"],
        timeout=2)
    assert rc != 0 and time.time() - t0 < 10

def test_timeout_without_killpg(monkeypatch):
    # simulate a platform (e.g. Windows) without os.killpg — must fall back to proc.kill()
    monkeypatch.delattr(os, "killpg", raising=False)
    t0 = time.time()
    rc, _, _ = run_isolated([sys.executable, "-c", "import time; time.sleep(60)"], timeout=2)
    assert rc != 0 and time.time() - t0 < 10


class _FakeProc:
    def __init__(self, data: bytes, rc: int = 0):
        self.stdout = io.BytesIO(data)
        self.returncode = rc
        self.pid = 4242

    def wait(self, timeout=None):
        return self.returncode


def test_run_streaming_linije_lf():
    lines = []
    rc = run_streaming(["x"], out=lines.append,
                       popen=lambda cmd, **k: _FakeProc(b"prva\ndruga\n"))
    assert rc == 0
    assert lines == ["prva", "druga"]


def test_run_streaming_cr_segmenti_kao_retci():
    """Injektirani out: svaki \\r segment = zaseban redak (progress povijest)."""
    lines = []
    rc = run_streaming(
        ["x"], out=lines.append,
        popen=lambda cmd, **k: _FakeProc(b"pull 10%\rpull 50%\rpull 100%\ngotovo\n"))
    assert rc == 0
    assert lines == ["pull 10%", "pull 50%", "pull 100%", "gotovo"]


def test_run_streaming_returncode_i_prazni_redci():
    lines = []
    rc = run_streaming(["x"], out=lines.append,
                       popen=lambda cmd, **k: _FakeProc(b"\n\nx\n", rc=3))
    assert rc == 3
    assert lines == ["x"]


def test_run_streaming_utf8_replace():
    lines = []
    run_streaming(["x"], out=lines.append,
                  popen=lambda cmd, **k: _FakeProc(b"\xff\xfezlo\n"))
    assert any("zlo" in l for l in lines)
