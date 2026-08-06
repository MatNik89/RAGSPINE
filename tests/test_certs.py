import os
import stat
import sys

import ssl

from ragspine.ops import certs


def test_generate_and_fingerprint(tmp_path):
    cert, key = certs.generate_self_signed(str(tmp_path), ips=["192.168.1.7"],
                                           hostnames=["ragspine.local"])
    assert cert.endswith("cert.pem") and key.endswith("key.pem")
    # cert je parsabilan standardnim ssl modulom
    der = ssl.PEM_cert_to_DER_cert(open(cert).read())
    assert len(der) > 100
    fp = certs.fingerprint_sha256(cert)
    assert len(fp.replace(":", "")) == 64 and fp.count(":") == 31


def test_generate_idempotent(tmp_path):
    c1, k1 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    fp1 = certs.fingerprint_sha256(c1)
    c2, k2 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.2"])
    assert (c1, k1) == (c2, k2)
    assert certs.fingerprint_sha256(c2) == fp1   # ne regenerira postojeći


def test_key_file_private(tmp_path):
    _, key = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    mode = stat.S_IMODE(os.stat(key).st_mode)
    if sys.platform != "win32":
        assert mode == 0o600


def test_trust_command_prints_fingerprint(tmp_path, monkeypatch, capsys):
    from ragspine import __main__ as m
    from ragspine.core.spine import init_spine
    from ragspine.config import Config, set_config

    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    set_config(cfg)
    try:
        s = init_spine(cfg.db_path)
        cert, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
        s.set_override("net", "cert_path", cert)
        rc = m.main(["trust"])
        out = capsys.readouterr().out
        assert rc == 0 and "SHA256" in out and "key.pem se nikad" in out
    finally:
        set_config(None)


def test_generate_recovers_from_orphan_key(tmp_path):
    """Ako ostane samo key.pem od crasha, regenerira se s novim certom."""
    # Simuliraj crash: kreiraj samo key.pem bez certa
    key_p = tmp_path / "key.pem"
    key_p.write_text("fake key from crash")

    cert, key = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    assert cert.endswith("cert.pem") and key.endswith("key.pem")
    assert (tmp_path / "cert.pem").exists()
    assert (tmp_path / "key.pem").exists()
    # Cert je validan i parsabilan
    fp = certs.fingerprint_sha256(cert)
    assert len(fp.replace(":", "")) == 64 and fp.count(":") == 31
