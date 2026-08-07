"""HTTP bootstrap server za radnike: /postavi stranica objasni kako uvesti
self-signed cert (browser to ne smije sam po sigurnosnom dizajnu), .bat
skida cert.pem sa servera i uvozi ga u Windows Root store (certutil).

Čisti stdlib `http.server` — bez novog ovisnosti, bez drugog uvicorna.
Bootstrap je POMOĆ, ne uvjet: greška binda (port zauzet) ne ruši `atlas
serve` — samo se preskoči uz upozorenje u logu.
"""
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from atlas.ops.certs import best_display_host  # noqa: F401 - re-export, jedna implementacija (certs.py)

logger = logging.getLogger(__name__)

BAT_IME = "postavi-vezu.bat"


def bat_content(cert_url: str, https_url: str) -> bytes:
    """Sadržaj .bat datoteke (Windows traži CRLF završetke redaka)."""
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


def postavi_html(https_url: str, bat_ime: str = BAT_IME) -> str:
    """HTML stranica /postavi — 3 koraka + napredna (ručna) sekcija."""
    return f"""<!doctype html>
<html lang="hr">
<head>
<meta charset="utf-8">
<title>ATLAS — postavljanje veze</title>
</head>
<body>
<h1>Postavljanje sigurne veze na ATLAS</h1>
<ol>
<li><a href="/{bat_ime}">Preuzmi postavljanje</a></li>
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
        def log_message(self, format, *args):  # noqa: A002 - stdlib potpis
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
            elif self.path == f"/{BAT_IME}":
                self._send(200, "application/octet-stream",
                           bat_content(cert_url, https_url),
                           {"Content-Disposition": f'attachment; filename="{BAT_IME}"'})
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
    """Pokreni bootstrap server na daemon threadu. `host` je bind adresa
    (može biti "0.0.0.0"); prikazni host za /cert.pem URL u .bat-u vadi se
    iz `https_url` (isto ime koje radnik vidi u uputi).

    Vraća thread s dodatnim atributom `.server` (ThreadingHTTPServer) —
    testovima treba stvarni port kod port=0 (`thread.server.server_address[1]`)
    i uredan shutdown (`thread.server.shutdown()`). Bind greška (port zauzet
    i sl.) je samo upozorenje — vraća None, `atlas serve` nastavlja normalno.
    """
    display_host = urlparse(https_url).hostname or host
    cert_url = f"http://{display_host}:{port}/cert.pem"
    handler_cls = _make_handler(cert_path, https_url, cert_url)
    try:
        server = ThreadingHTTPServer((host, port), handler_cls)
    except (OSError, OverflowError) as e:
        # OverflowError: port izvan 0-65535 (npr. ATLAS_BOOTSTRAP_PORT=70000) —
        # socket sloj to ne javlja kao OSError nego kao OverflowError.
        logger.warning("Bootstrap server ne može se pokrenuti na %s:%s (%s)",
                        host, port, e)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.server = server
    thread.start()
    return thread
