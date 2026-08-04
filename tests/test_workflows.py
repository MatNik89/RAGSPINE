import json
from pathlib import Path

from ragspine.browser import sessions, workflows

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "recorder.json").read_text(encoding="utf-8"))


class FakeBridge:
    def __init__(self, results):
        self.results = list(results)
        self.enqueued = []

    def enqueue(self, cmd):
        self.enqueued.append(cmd)
        return len(self.enqueued)

    def wait_result(self, cmd_id, timeout=60):
        return self.results.pop(0)


def test_import_recorder_and_get_steps(spine):
    wf_id = workflows.import_recorder(spine, "login", FIXTURE)
    assert isinstance(wf_id, int)
    steps = workflows.get_steps(spine, "login")
    assert len(steps) == 3
    assert [s["action"] for s in steps] == ["navigate", "click", "type"]
    assert steps[0]["url"] == "{{url}}"


def test_parametrize_replaces_placeholders(spine):
    workflows.import_recorder(spine, "login", FIXTURE)
    steps = workflows.get_steps(spine, "login")
    out = workflows.parametrize(steps, {"url": "https://x.hr", "oib": "123"})
    assert out[0]["url"] == "https://x.hr"
    assert out[2]["value"] == "123"
    # original steps untouched (deep copy)
    assert steps[0]["url"] == "{{url}}"


def test_run_all_success(spine):
    workflows.import_recorder(spine, "login", FIXTURE)
    bridge = FakeBridge([{"ok": 1}, {"ok": 1}, {"ok": 1}])
    results = workflows.run(spine, bridge, "login", params={"url": "https://x.hr", "oib": "123"})
    assert len(results) == 3
    assert bridge.enqueued[0]["url"] == "https://x.hr"


def test_run_stops_on_error(spine):
    workflows.import_recorder(spine, "login", FIXTURE)
    bridge = FakeBridge([{"ok": 1}, {"error": "fail"}, {"ok": 1}])
    results = workflows.run(spine, bridge, "login", params={"url": "https://x.hr", "oib": "123"})
    assert len(results) == 2


def test_run_stops_on_timeout(spine):
    workflows.import_recorder(spine, "login", FIXTURE)
    bridge = FakeBridge([{"ok": 1}, None, {"ok": 1}])
    results = workflows.run(spine, bridge, "login", params={"url": "https://x.hr", "oib": "123"})
    assert len(results) == 2


def test_sessions_mode_default(spine):
    assert sessions.mode(spine, "example.hr") == "auto"


def test_sessions_set_mode(spine):
    sessions.set_mode(spine, "example.hr", "keep")
    assert sessions.mode(spine, "example.hr") == "keep"


def test_sessions_record_failure_twice_sets_keep_and_notifies(spine):
    sessions.record_failure(spine, "example.hr")
    m = sessions.record_failure(spine, "example.hr")
    assert m == "keep"
    assert sessions.mode(spine, "example.hr") == "keep"
    row = spine.read().execute(
        "SELECT kind, body FROM notifications WHERE kind='session_mode'"
    ).fetchone()
    assert row is not None
    assert "example.hr" in row["body"]


def test_sessions_record_success_resets_failures(spine):
    sessions.record_failure(spine, "example.hr")
    sessions.record_success(spine, "example.hr")
    m = sessions.record_failure(spine, "example.hr")
    assert m != "keep"
