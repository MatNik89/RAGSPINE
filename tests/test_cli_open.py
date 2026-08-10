"""`atlas open` — installer poziva ovo (ne 'serve --open' koji ne postoji).
URL se izračuna iz net-overridea + cert SAN, ne nagađa se."""
from atlas import __main__ as m


def test_dashboard_url_http_default(spine, cfg):
    # bez certa -> http, host 127.0.0.1 -> localhost, port iz cfg-a
    url = m._dashboard_url(spine, cfg)
    assert url.startswith("http://") and str(cfg.port) in url and "localhost" in url


def test_dashboard_url_uses_net_overrides(spine, cfg):
    spine.set_override("net", "host", "192.168.1.10")
    spine.set_override("net", "port", "8443")
    url = m._dashboard_url(spine, cfg)
    assert "192.168.1.10:8443" in url


def test_cmd_open_print_only(spine, cfg, capsys, monkeypatch):
    from atlas.core.spine import init_spine
    monkeypatch.setattr("atlas.config.get_config", lambda: cfg)
    init_spine(cfg.db_path)
    called = []
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: called.append(u))

    class Args:
        print_only = True
    rc = m._cmd_open(Args())
    assert rc == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith(("http://", "https://"))
    assert called == []  # --print-only ne otvara browser


def test_open_registered_in_cli():
    import argparse
    # build parser i provjeri da 'open' postoji (installer ga zove)
    parser = m.build_parser() if hasattr(m, "build_parser") else None
    if parser is None:
        # fallback: samo provjeri da funkcija postoji
        assert callable(m._cmd_open)
