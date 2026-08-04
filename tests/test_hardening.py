"""P1 sigurnosno ojačanje: zaglavlja, body-cap, LAN guard, DB perms, formula, prirez."""
import os
import stat

import pytest
from fastapi.testclient import TestClient

from ragspine.core import lan
from ragspine.core.spine import Spine
from ragspine.web import watchlist as w
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def test_security_headers_present(spine, cfg):
    r = _client(spine, cfg).get("/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Server"] == "RAGSPINE"


def test_oversized_body_rejected(spine, cfg):
    c = _client(spine, cfg)
    r = c.post("/auth/login", headers={"content-length": str(100 * 1024 * 1024),
                                       "content-type": "application/json"}, content=b"{}")
    assert r.status_code == 413


def test_is_lan_addr_blocks_metadata_and_public():
    assert lan._is_lan_addr("192.168.1.5")
    assert lan._is_lan_addr("10.0.0.1")
    assert lan._is_lan_addr("127.0.0.1")          # loopback zadržan (admin-only + testovi)
    assert lan._is_lan_addr("::1")                # IPv6 loopback (prije krivo odbijen)
    assert lan._is_lan_addr("fd12:3456::1")       # legit ULA LAN
    assert not lan._is_lan_addr("169.254.169.254")       # IPv4 cloud metadata
    assert not lan._is_lan_addr("::ffff:169.254.169.254")  # IPv4-mapped metadata bypass
    assert not lan._is_lan_addr("fd00:ec2::254")   # AWS IPv6 IMDS
    assert not lan._is_lan_addr("0.0.0.0")         # unspecified
    assert not lan._is_lan_addr("8.8.8.8")


def test_db_file_perms_0600(tmp_path):
    p = str(tmp_path / "t.db")
    Spine(p)
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_cell_neutralizes_leading_control_chars():
    assert w._cell("\t=1+1").startswith("'")
    assert w._cell("\r=cmd").startswith("'")
    assert w._cell("=SUM(A1)").startswith("'")
    assert w._cell("normalno") == "normalno"


def test_prirez_out_of_bounds_ignored(spine, cfg):
    from unittest.mock import patch
    src = "prirez Zagreb 999%"
    with spine.write() as c:
        c.execute("INSERT INTO watch_sources(url,kind,active,category) VALUES('http://x','html',1,'')")
        row = c.execute("SELECT * FROM watch_sources").fetchone()
    with patch.object(w, "safe_fetch", lambda *a, **k: src.encode()):
        w.check_source(spine, cfg, dict(row))
    # 999% ne smije postati override
    ov = spine.read().execute("SELECT COUNT(*) FROM config_overrides WHERE key LIKE 'prirez.%'").fetchone()[0]
    assert ov == 0
