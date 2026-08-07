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
        pass  # macOS zna odbiti RLIMIT_AS — bolje bez mem-limita nego pad svakog spawna


def _kill_tree(proc) -> None:
    """Ubij proces i CIJELO stablo (Windows: taskkill /T /F; POSIX: killpg)."""
    try:
        if os.name == "posix" and hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        elif os.name == "nt":
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
        # start_new_session/preexec_fn su POSIX-only (Windows Popen ih odbija)
        kwargs["start_new_session"] = True
        if resource is not None:
            kwargs["preexec_fn"] = lambda: _preexec(mem_mb)

    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",  # tesseract stdout je UTF-8, ne locale cp1252
        **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            # bounded drain: preživjeli unuk s naslijeđenim pipeom ne smije
            # držati poziv zauvijek
            out, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        rc = proc.returncode if proc.returncode is not None else -9
        return rc, out, err


def run_streaming(cmd, *, timeout: int = 600, out=print,
                  popen=subprocess.Popen) -> int:
    """Pokreni proces i prosljeđuj izlaz UŽIVO (winget/instalacije — kraj
    mrtvog ekrana, E2E nalaz). Poštuje \r: na pravom TTY-ju redak se
    osvježava u mjestu; injektirani out dobiva segmente kao retke.
    stdout+stderr spojeni; utf-8 errors=replace. -9 na timeout."""
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
