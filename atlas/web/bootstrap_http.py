"""HTTP bootstrap server for workers: the /postavi page explains how to import
the self-signed cert (a browser must not do that on its own by security design);
the .bat downloads cert.pem from the server and imports it into the Windows Root
store (certutil).

Pure stdlib `http.server` — no new dependency, no second uvicorn. The bootstrap
is a HELP, not a requirement: a bind error (port in use) does not crash `atlas
serve` — it is simply skipped with a warning in the log.
"""
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from atlas.ops.certs import best_display_host  # noqa: F401 - re-export, single implementation (certs.py)

logger = logging.getLogger(__name__)

BAT_NAME = "postavi-vezu.bat"


def bat_content(cert_url: str, https_url: str) -> bytes:
    """Contents of the .bat file (Windows requires CRLF line endings)."""
    lines = [
        "@echo off",
        "echo Postavljam sigurnu vezu za ATLAS...",
        f'certutil -urlcache -split -f "{cert_url}" "%TEMP%\\atlas-cert.pem"',
        "if errorlevel 1 (echo GRESKA - server nedostupan & pause & exit /b 1)",
        'certutil -addstore -f Root "%TEMP%\\atlas-cert.pem"',
        "if errorlevel 1 (echo GRESKA - pokreni kao administrator & pause & exit /b 1)",
        f"echo Gotovo! Otvaram {https_url}",
        f'start "" "{https_url}"',
    ]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def postavi_html(https_url: str, bat_name: str = BAT_NAME) -> str:
    """The /postavi HTML page — 3 steps + an advanced (manual) section."""
    return f"""<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<title>ATLAS — postavljanje veze</title>
</head>
<body>
<h1>Postavljanje sigurne veze na ATLAS</h1>
<ol>
<li><a href="/{bat_name}">Preuzmi postavljanje</a></li>
<li>Dupli klik na preuzetu datoteku, zatim desni klik →
"Pokreni kao administrator" → Da (UAC upit)</li>
<li>Otvori <a href="{https_url}">{https_url}</a> — ubuduće radi i
prečac/bookmark na tu adresu</li>
</ol>
<h2>Ručno (napredno)</h2>
<p>Preuzmi <a href="/cert.pem">certifikat</a> pa ga ručno uvezi kao
administrator naredbom:</p>
<pre>certutil -addstore -f Root cert.pem</pre>
</body>
</html>"""


def _make_handler(cert_path: str, https_url: str, cert_url: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            logger.debug(format, *args)

        def _send(self, status: int, content_type: str, body: bytes,
                  extra_headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self.send_response(302)
                self.send_header("Location", "/postavi")
                self.end_headers()
            elif self.path == "/postavi":
                self._send(200, "text/html; charset=utf-8",
                           postavi_html(https_url).encode("utf-8"))
            elif self.path == f"/{BAT_NAME}":
                self._send(200, "application/octet-stream",
                           bat_content(cert_url, https_url),
                           {"Content-Disposition": f'attachment; filename="{BAT_NAME}"'})
            elif self.path == "/cert.pem":
                try:
                    body = Path(cert_path).read_bytes()
                except OSError:
                    self.send_error(404)
                    return
                self._send(200, "application/x-pem-file", body)
            else:
                self.send_error(404)

    return Handler


def start_bootstrap_server(cert_path: str, https_url: str, host: str,
                            port: int = 8080) -> threading.Thread | None:
    """Start the bootstrap server on a daemon thread. `host` is the bind address
    (may be "0.0.0.0"); the display host for the /cert.pem URL in the .bat is
    taken from `https_url` (the same name the worker sees in the instructions).

    Returns a thread with an extra `.server` attribute (ThreadingHTTPServer) —
    tests need the actual port when port=0 (`thread.server.server_address[1]`)
    and a clean shutdown (`thread.server.shutdown()`). A bind error (port in use
    etc.) is only a warning — returns None, `atlas serve` continues normally.
    """
    display_host = urlparse(https_url).hostname or host
    cert_url = f"http://{display_host}:{port}/cert.pem"
    handler_cls = _make_handler(cert_path, https_url, cert_url)
    try:
        server = ThreadingHTTPServer((host, port), handler_cls)
    except (OSError, OverflowError) as e:
        # OverflowError: port outside 0-65535 (e.g. ATLAS_BOOTSTRAP_PORT=70000) —
        # the socket layer reports this not as OSError but as OverflowError.
        logger.warning("Bootstrap server ne može se pokrenuti na %s:%s (%s)",
                        host, port, e)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.server = server
    thread.start()
    return thread
