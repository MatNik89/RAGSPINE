import pytest
from ragspine.core.net import safe_fetch, EgressBlocked

@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/x",
                                 "http://192.168.1.1/x", "ftp://porezna.hr/x", "file:///etc/passwd"])
def test_blocked(url, cfg):
    with pytest.raises(EgressBlocked): safe_fetch(url)

def test_allowlist(cfg, monkeypatch):
    cfg.egress_allow.append("127.0.0.1")
    # dalje puca na connection refused, NE na EgressBlocked
    with pytest.raises(OSError): safe_fetch("http://127.0.0.1:1/x", timeout=1)
