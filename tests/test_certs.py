import os
import stat
import sys

import ssl

from atlas.ops import certs


def test_generate_and_fingerprint(tmp_path):
    cert, key = certs.generate_self_signed(str(tmp_path), ips=["192.168.1.7"],
                                           hostnames=["atlas.local"])
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
    from atlas import __main__ as m
    from atlas.core.spine import init_spine
    from atlas.config import Config, set_config

    monkeypatch.setenv("ATLAS_DATA_DIR", str(tmp_path))
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


def test_generate_warns_on_stale_san(tmp_path):
    """Nalaz e: nakon promjene IP-a stroja, postojeći cert ima stari SAN —
    upozori umjesto tihe regeneracije (trust je možda već instaliran)."""
    c1, k1 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    lines = []
    c2, k2 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.99"], out=lines.append)
    assert (c1, k1) == (c2, k2)                      # ne regenerira automatski
    assert any("10.0.0.99" in l for l in lines)


def test_generate_no_warning_when_san_covers_ip(tmp_path):
    c1, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    lines = []
    certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"], out=lines.append)
    assert lines == []


def test_friendly_names_includes_fqdn_suffix(monkeypatch):
    monkeypatch.setattr(certs.socket, "gethostname", lambda: "Nick")
    monkeypatch.setattr(certs.socket, "getfqdn", lambda: "nick.fritz.box")
    assert certs.friendly_names() == ["nick", "nick.fritz.box", "nick.local", "atlas.local"]


def test_friendly_names_fqdn_equals_hostname_no_suffix(monkeypatch):
    monkeypatch.setattr(certs.socket, "gethostname", lambda: "nick")
    monkeypatch.setattr(certs.socket, "getfqdn", lambda: "nick")
    assert certs.friendly_names() == ["nick", "nick.local", "atlas.local"]


def test_friendly_names_socket_exception_falls_back(monkeypatch):
    def _boom():
        raise OSError("no network")
    monkeypatch.setattr(certs.socket, "gethostname", _boom)
    assert certs.friendly_names() == ["atlas.local"]


def test_san_dns_names_returns_cert_dns_entries(tmp_path):
    cert, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"],
                                         hostnames=["atlas.local"])
    assert certs.san_dns_names(cert) == ["atlas.local"]


def test_san_dns_names_empty_on_unreadable_cert(tmp_path):
    bad = tmp_path / "cert.pem"
    bad.write_text("nije certifikat")
    assert certs.san_dns_names(str(bad)) == []
    assert certs.san_dns_names(str(tmp_path / "ne-postoji.pem")) == []


def test_verified_display_host_stari_cert_prikazuje_samo_san_ime(tmp_path):
    """Nalaz: stara instalacija — cert SAN pokriva samo atlas.local, dok
    friendly_names() vec nudi novije ime (nick.fritz.box). Display MORA
    ostati na imenu koje cert stvarno pokriva — ne smije nuditi ime koje
    izaziva browser warning."""
    cert, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"],
                                         hostnames=["atlas.local"])
    names = ["nick", "nick.fritz.box", "nick.local", "atlas.local"]
    assert certs.verified_display_host(cert, names, "10.0.0.1") == "atlas.local"


def test_verified_display_host_novi_cert_sa_svim_imenima(tmp_path):
    names = ["nick", "nick.fritz.box", "nick.local", "atlas.local"]
    cert, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"],
                                         hostnames=names)
    assert certs.verified_display_host(cert, names, "10.0.0.1") == "nick.fritz.box"


def test_verified_display_host_necitljiv_cert_pada_na_ip(tmp_path):
    bad = tmp_path / "cert.pem"
    bad.write_text("nije certifikat")
    names = ["nick", "nick.fritz.box", "nick.local", "atlas.local"]
    assert certs.verified_display_host(str(bad), names, "10.0.0.1") == "10.0.0.1"


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
