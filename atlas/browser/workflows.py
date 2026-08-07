"""Chrome DevTools Recorder import -> internal steps, parametrize, run via Bridge."""
import copy
import json

_TYPE_MAP = {"navigate": "navigate", "click": "click", "change": "type"}


def _translate_step(step: dict) -> dict:
    action = _TYPE_MAP.get(step["type"], step["type"])
    out = {"action": action}
    if "url" in step:
        out["url"] = step["url"]
    selectors = step.get("selectors")
    if selectors:
        out["selector"] = selectors[0][0]
    if "value" in step:
        out["value"] = step["value"]
    return out


def import_recorder(spine, name: str, recorder_json: dict) -> int:
    steps = [_translate_step(s) for s in recorder_json["steps"]]
    with spine.write() as c:
        c.execute(
            "INSERT OR REPLACE INTO browser_workflows(id, name, steps) "
            "VALUES ((SELECT id FROM browser_workflows WHERE name=?), ?, ?)",
            (name, name, json.dumps(steps)),
        )
        row = c.execute("SELECT id FROM browser_workflows WHERE name=?", (name,)).fetchone()
    return row["id"]


def get_steps(spine, name: str) -> list[dict]:
    row = spine.read().execute(
        "SELECT steps FROM browser_workflows WHERE name=?", (name,)
    ).fetchone()
    return json.loads(row["steps"]) if row else []


def _sub(value, params: dict):
    if isinstance(value, str):
        for key, val in params.items():
            value = value.replace("{{" + key + "}}", str(val))
    return value


def parametrize(steps: list[dict], params: dict) -> list[dict]:
    out = copy.deepcopy(steps)
    for step in out:
        for field in ("url", "value", "selector"):
            if field in step:
                step[field] = _sub(step[field], params)
    return out


def run(spine, bridge, name: str, params: dict | None = None, timeout: int = 60) -> list[dict]:
    steps = parametrize(get_steps(spine, name), params or {})
    results = []
    for step in steps:
        cmd_id = bridge.enqueue(step)
        res = bridge.wait_result(cmd_id, timeout)
        results.append(res)
        if res is None or res.get("error"):
            break
    return results
