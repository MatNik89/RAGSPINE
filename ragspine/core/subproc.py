import os, signal, subprocess

try:
    import resource
except ImportError:
    resource = None


def _preexec(mem_mb: int):
    limit = mem_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


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
        try:
            if posix and hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            elif os.name == "nt":
                # ubij CIJELO stablo — samo proc.kill() ostavlja unuke koji drže
                # stdout pipe pa communicate() visi do njihova kraja
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                proc.kill()
        except (ProcessLookupError, OSError):
            pass
        out, err = proc.communicate()
        rc = proc.returncode if proc.returncode is not None else -9
        return rc, out, err
