from types import SimpleNamespace

from atlas.ops import doctor, health, nis2


def test_doctor_run_returns_checks(cfg):
    results = doctor.run(cfg)
    assert len(results) >= 8
    for r in results:
        assert set(r) >= {"check", "ok", "detail"}
        assert isinstance(r["ok"], bool)


def test_doctor_python_version_ok(cfg):
    results = doctor.run(cfg)
    r = next(x for x in results if x["check"] == "python_version")
    assert r["ok"] is True


def test_doctor_db_writable_ok(cfg):
    results = doctor.run(cfg)
    r = next(x for x in results if x["check"] == "db_writable")
    assert r["ok"] is True


def test_doctor_survives_missing_binary(cfg, monkeypatch):
    def _boom(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(doctor, "run_isolated", _boom)
    results = doctor.run(cfg)
    assert len(results) >= 8
    ntp = next(x for x in results if x["check"] == "ntp")
    assert ntp["ok"] is True
    luks = next(x for x in results if x["check"] == "luks")
    assert luks["ok"] is True


def test_format_report_is_string(cfg):
    report = doctor.format_report(doctor.run(cfg))
    assert isinstance(report, str)
    assert "python_version" in report


def test_required_ok_ignores_ollama_down(cfg, monkeypatch):
    # ollama unreachable is normal on a cloud-LLM/OAuth host — must not fail the gate.
    # Disk space is stubbed healthy so this test doesn't flake on a low-disk dev machine
    # (it tests ollama-vs-gate logic, not the real disk).
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(total=0, used=0, free=50_000_000_000))
    results = doctor.run(cfg)
    for r in results:
        if r["check"] == "ollama":
            r["ok"] = False
    assert doctor.required_ok(results) is True


def test_required_ok_false_when_required_check_fails(cfg):
    results = doctor.run(cfg)
    for r in results:
        if r["check"] == "db_writable":
            r["ok"] = False
    assert doctor.required_ok(results) is False


def test_health_check_fresh_spine(spine, cfg):
    result = health.check(spine, cfg)
    assert result["disk_free_mb"] > 0
    assert result["integrity"] == "ok"
    assert result["wal_size_kb"] >= 0


def test_nis2_checklist_has_12():
    assert len(nis2.CHECKLIST) == 12


def test_nis2_report_default_unknown(spine):
    rows = nis2.report(spine)
    assert len(rows) == 12
    assert all(r["status"] == "nepoznato" for r in rows)


def test_nis2_set_status_reflected(spine):
    nis2.set_status(spine, "backup", "implementirano")
    rows = nis2.report(spine)
    r = next(x for x in rows if x["id"] == "backup")
    assert r["status"] == "implementirano"


def test_nis2_stubs():
    assert nis2.smart() == {"status": "stub"}
    assert nis2.lynis() == {"status": "stub"}
    assert nis2.nmap() == {"status": "stub"}


def test_doctor_production_checks_present(cfg):
    """Nove produkcijske provjere (deploy priprema): korisnici, llm, nas, perms."""
    names = {r["check"] for r in doctor.run(cfg)}
    assert {"korisnici", "llm_provider", "nas", "perms"} <= names


def test_doctor_flags_missing_users(cfg):
    from atlas.core.spine import init_spine
    init_spine(cfg.db_path)  # prazna baza, 0 korisnika
    r = next(x for x in doctor.run(cfg) if x["check"] == "korisnici")
    assert r["ok"] is False
