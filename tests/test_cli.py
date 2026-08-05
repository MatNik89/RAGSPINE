from ragspine.__main__ import main
from ragspine.ops import doctor

def test_doctor_exit_0_despite_ollama_down(tmp_path, monkeypatch, capsys):
    # Ollama unreachable is expected on a non-Ollama (cloud-LLM/OAuth) host and
    # must not hard-fail `ragspine doctor`'s exit code.
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(doctor, "_ollama_alive", lambda cfg: False)
    assert main(["doctor"]) == 0

def test_auth_add_and_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAGSPINE_PASS", "tajna123")
    assert main(["auth", "add", "ana"]) == 0
    out = capsys.readouterr().out
    assert "ana" in out

def test_unknown_cmd():
    assert main(["nepostojece"]) == 2

def test_watch_ocr_wired(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RAGSPINE_DATA_DIR", str(tmp_path))
    assert main(["watch", "run"]) == 0
    assert "changes=" in capsys.readouterr().out
    assert main(["ocr", "/nonexistent.pdf"]) == 1


def test_forget_command_removed(capsys):
    # brisanje klijentskih podataka namjerno NIJE dostupno (zakonska retencija)
    assert main(["forget", "bilo-sto"]) != 0
