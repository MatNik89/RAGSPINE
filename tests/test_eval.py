from ragspine.__main__ import main
from ragspine.ops import evalrun


def test_run_returns_expected_keys():
    report = evalrun.run()
    assert set(report) == {"router_ok", "retrieval_ok", "router_pass", "retrieval_pass", "pass"}


def test_router_pass():
    assert evalrun.run()["router_pass"] is True


def test_retrieval_pass():
    assert evalrun.run()["retrieval_pass"] is True


def test_overall_pass():
    assert evalrun.run()["pass"] is True


def test_independent_of_real_db(monkeypatch):
    monkeypatch.delenv("RAGSPINE_DB_PATH", raising=False)
    assert evalrun.run()["pass"] is True


def test_cli_eval_returns_0(cfg):
    assert main(["eval"]) == 0


def test_cli_reminders_add_then_list(cfg, capsys):
    assert main(["reminders"]) == 0
    assert main(["reminders", "add", "test podsjetnik", "2026-08-15"]) == 0
    assert main(["reminders"]) == 0
    out = capsys.readouterr().out
    assert "test podsjetnik" in out
