import os, signal, subprocess

try:
    import resource
except ImportError:
    resource = None


def _preexec(mem_mb: int):
    limit = mem_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def run_isolated(cmd: list[str], timeout: int = 60, cwd=None, mem_mb: int = 512) -> tuple[int, str, str]:
    preexec_fn = None
    if os.name == "posix" and resource is not None:
        preexec_fn = lambda: _preexec(mem_mb)

    proc = subprocess.Popen(
        cmd, cwd=cwd, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        preexec_fn=preexec_fn,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err
    except subprocess.TimeoutExpired:
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        rc = proc.returncode if proc.returncode is not None else -9
        return rc, out, err
