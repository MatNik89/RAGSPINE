import os, signal, subprocess, sys, threading

try:
    import resource
except ImportError:
    resource = None


def _preexec(mem_mb: int):
    limit = mem_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ValueError, OSError):
        pass  # macOS can reject RLIMIT_AS — better without a mem-limit than every spawn failing


def _kill_tree(proc) -> None:
    """Kill the process and the WHOLE tree (Windows: taskkill /T /F; POSIX: killpg)."""
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        elif os.name == "nt":
            # kill the WHOLE tree — proc.kill() alone leaves grandchildren holding
            # the stdout pipe, so communicate() hangs until they exit
            tk = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                capture_output=True)
            if tk.returncode != 0:
                proc.kill()
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        pass


def run_isolated(cmd: list[str], timeout: int = 60, cwd=None, mem_mb: int = 512) -> tuple[int, str, str]:
    posix = os.name == "posix"
    kwargs = {}
    if posix:
        # start_new_session/preexec_fn are POSIX-only (Windows Popen rejects them)
        kwargs["start_new_session"] = True
        if resource is not None:
            kwargs["preexec_fn"] = lambda: _preexec(mem_mb)

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",  # tesseract stdout is UTF-8, not locale cp1252
        **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            # bounded drain: a surviving grandchild with an inherited pipe must not
            # hold the call forever
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        rc = proc.returncode if proc.returncode is not None else -9
        return rc, out, err


def run_streaming(cmd, *, timeout: int = 600, out=print,
                  popen=subprocess.Popen) -> int:
    """Run a process and forward output LIVE (winget/installs — no more
    dead screen, an E2E finding). Honors \r: on a real TTY the line is
    refreshed in place; an injected out receives segments as lines.
    stdout+stderr merged; utf-8 errors=replace. -9 on timeout."""
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **({"start_new_session": True} if os.name == "posix" else {}))
    timed_out = threading.Event()

    def _on_timeout():
        timed_out.set()
        _kill_tree(proc)

    timer = threading.Timer(timeout, _on_timeout)
    timer.start()
    tty = out is print and sys.stdout.isatty()
    inplace = False

    def _emit(segment: str, cr: bool) -> None:
        nonlocal inplace
        if not segment:
            return
        if tty and cr:
            sys.stdout.write("\r  " + segment + "        ")
            sys.stdout.flush()
            inplace = True
            return
        if tty and inplace:
            sys.stdout.write("\n")
            inplace = False
        out(segment)

    try:
        buf = b""
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                _emit(buf.decode("utf-8", errors="replace").rstrip(), ch == b"\r")
                buf = b""
            else:
                buf += ch
        _emit(buf.decode("utf-8", errors="replace").rstrip(), False)
        if inplace:
            sys.stdout.write("\n")
    finally:
        timer.cancel()
    if timed_out.is_set():
        return -9
    try:
        return proc.wait(timeout=15) or 0
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return -9
