import urllib.request
import urllib.error

from atlas.web.bootstrap_http import (
    bat_content, postavi_html, best_display_host, start_bootstrap_server,
)


def test_bat_content_crlf_and_commands():
    body = bat_content("http://nick.local:8080/cert.pem", "https://nick.local:8443")
    text = body.decode("utf-8")
    lines = text.split("\r\n")[:-1]  # zadnji je prazan (trailing \r\n)
    assert lines  # ima sadržaja
    for line in lines:
        assert "\n" not in line  # svaki redak čisti CRLF, bez golog LF
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", "")  # nema golog LF nigdje
    assert "certutil -urlcache" in text
    assert "-addstore -f Root" in text
    assert "https://nick.local:8443" in text
    assert "http://nick.local:8080/cert.pem" in text
    assert "administrator" in text.lower() or "GRESKA" in text


def test_postavi_html_sadrzi_korake_i_link():
    html = postavi_html("https://nick.fritz.box:8443")
    assert "https://nick.fritz.box:8443" in html
    assert "postavi-vezu.bat" in html
    assert "administrator" in html.lower()
    # 3 koraka
    assert "1." in html and "2." in html and "3." in html
    assert "cert.pem" in html


def test_best_display_host_prefers_fqdn():
    assert best_display_host(["nick", "nick.fritz.box", "nick.local", "atlas.local"],
                              "1.2.3.4") == "nick.fritz.box"


def test_best_display_host_falls_back_to_dot_local():
    assert best_display_host(["nick", "nick.local", "atlas.local"], "1.2.3.4") == "nick.local"


def test_best_display_host_falls_back_to_ip():
    assert best_display_host(["nick", "atlas.local"], "1.2.3.4") == "1.2.3.4"


def test_bind_error_returns_none(monkeypatch, tmp_path):
    from atlas.web import bootstrap_http

    class BoomServer:
        def __init__(self, *a, **k):
            raise OSError("port zauzet")

    monkeypatch.setattr(bootstrap_http, "ThreadingHTTPServer", BoomServer)
    result = start_bootstrap_server(str(tmp_path / "cert.pem"), "https://x:8443", "127.0.0.1", port=1)
    assert result is None


def test_bootstrap_server_routes(tmp_path):
    cert_p = tmp_path / "cert.pem"
    cert_p.write_text("-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
    https_url = "https://127.0.0.1:8443"
    thread = start_bootstrap_server(str(cert_p), https_url, "127.0.0.1", port=0)
    assert thread is not None
    server = thread.server
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"

        with urllib.request.urlopen(f"{base}/postavi") as r:
            assert r.status == 200
            assert "text/html" in r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8")
            assert "https://127.0.0.1:8443" in body

        with urllib.request.urlopen(f"{base}/cert.pem") as r:
            assert r.status == 200
            assert r.headers.get("Content-Type") == "application/x-pem-file"
            assert b"FAKE" in r.read()

        with urllib.request.urlopen(f"{base}/postavi-vezu.bat") as r:
            assert r.status == 200
            data = r.read()
            assert b"\r\n" in data

        # / -> 302 na /postavi (urlopen prati redirect pa provjerimo preko no-redirect handlera)
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            opener.open(f"{base}/")
            assert False, "očekivan 302"
        except urllib.error.HTTPError as e:
            assert e.code == 302
            assert e.headers.get("Location") == "/postavi"

        try:
            urllib.request.urlopen(f"{base}/nema")
            assert False, "očekivan 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()
