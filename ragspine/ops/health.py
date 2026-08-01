"""DB/disk health check + alerting (apprise if available, else notifications row)."""
import os
import shutil

from ragspine.core import optional

_LOW_DISK_MB = 1024


def check(spine, cfg) -> dict:
    disk_free_mb = shutil.disk_usage(cfg.data_dir).free // (1024 * 1024)
    wal_path = f"{cfg.db_path}-wal"
    wal_size_kb = (os.path.getsize(wal_path) // 1024) if os.path.exists(wal_path) else 0
    try:
        row = spine.read().execute("PRAGMA integrity_check").fetchone()
        integrity = row[0] if row else "unknown"
    except Exception as e:
        integrity = str(e)
    result = {"disk_free_mb": disk_free_mb, "wal_size_kb": wal_size_kb, "integrity": integrity}
    if integrity != "ok" or disk_free_mb < _LOW_DISK_MB:
        _alert(spine, result)
    return result


def _alert(spine, result: dict) -> None:
    apprise = optional.need("apprise", "health notifikacije")
    if apprise is not None:
        try:
            app = apprise.Apprise()
            # ponytail: no target URLs configured yet — no-op until cfg gains an
            # apprise-URL knob. Upgrade path: app.add(cfg.apprise_urls) then notify.
            app.notify(body=f"RAGSPINE health alert: {result}", title="RAGSPINE health")
        except Exception:
            pass
        return
    with spine.write() as c:
        c.execute("INSERT INTO notifications(kind, body) VALUES(?,?)",
                   ("health_alert", str(result)))
