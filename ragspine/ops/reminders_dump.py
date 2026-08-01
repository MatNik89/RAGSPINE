# Degraded-mode dump: flat reminders.json on the NAS so an accountant can
# still see upcoming deadlines if the RAGSPINE server itself is down.
import json
import os
from datetime import date, timedelta

from ragspine.business import expiry, kalendar

_WINDOW_DAYS = 30


def dump(spine, cfg, now_fn=None) -> dict:
    today = (now_fn or date.today)()
    end = today + timedelta(days=_WINDOW_DAYS)

    reminders = spine.read().execute(
        "SELECT id, user, body, due FROM reminders WHERE done=0 AND due BETWEEN ? AND ? ORDER BY due",
        (today.isoformat(), end.isoformat()),
    ).fetchall()
    rokovi = kalendar.upcoming(spine, days=_WINDOW_DAYS)
    istek = expiry.expiring(spine, days=_WINDOW_DAYS)

    payload = {
        "generated": today.isoformat(),
        "note": "Server je pao? Rokovi su ovdje.",
        "reminders": [dict(r) for r in reminders],
        "rokovi": [dict(r) for r in rokovi],
        "istek": [dict(r) for r in istek],
    }

    out_dir = cfg.nas_root or cfg.data_dir
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "reminders.json")
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)  # atomic on POSIX and Windows (same filesystem)

    count = len(payload["reminders"]) + len(payload["rokovi"]) + len(payload["istek"])
    return {"path": path, "count": count}
