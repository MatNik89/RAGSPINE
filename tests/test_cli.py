from ragspine.__main__ import main

def test_auth_add_and_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGSPINE_PASS", "tajna123")
    assert main(["auth", "add", "ana"]) == 0
    out = capsys.readouterr().out
    assert "ana" in out

def test_unknown_cmd():
    assert main(["nepostojece"]) == 2

def test_forget_watch_ocr_wired(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    assert main(["forget", "nepostojeca-firma-xyz", "--dry"]) == 0
    assert "documents" in capsys.readouterr().out
    assert main(["watch", "run"]) == 0
    assert "changes=" in capsys.readouterr().out
    assert main(["ocr", "/nonexistent.pdf"]) == 1
