"""Scheduler jobs: thin wrappers over the existing doer modules. Each fn has
signature fn(spine, cfg) -> None per ops/scheduler.py's Job contract."""
import logging
import time
from datetime import date

from atlas.business import expiry, folder_sync, kalendar, obveze, power, rokovi
from atlas.core import memory
from atlas.docs import imap_fetch
from atlas.ops import digest, health, reminders_dump
from atlas.ops.scheduler import Job
from atlas.web import watchlist

logger = logging.getLogger(__name__)


def _period_now() -> str:
    return date.today().strftime("%Y-%m")


def _notify_once(spine, kind: str, body: str, client_id=None) -> None:
    """Insert a notification unless an identical kind+body pair was already
    created in the last 7 days — keeps daily jobs from spamming duplicates."""
    exists = spine.read().execute(
        "SELECT 1 FROM notifications WHERE kind=? AND body=? AND at >= datetime('now','-7 days')",
        (kind, body),
    ).fetchone()
    if exists:
        return
    with spine.write() as c:
        c.execute(
            "INSERT INTO notifications(kind, body, client_id) VALUES(?,?,?)",
            (kind, body, client_id),
        )


def watchlist_job(spine, cfg) -> None:
    changes = watchlist.check_all(spine, cfg)
    items = watchlist.check_rss(spine, cfg)
    logger.info("watchlist_job: %d changes, %d rss items", len(changes), len(items))


def imap_job(spine, cfg) -> None:
    if cfg.imap_host:  # naslijeđeni env-konfiguriran IMAP
        logger.info("imap_job(env): %s", imap_fetch.fetch_new(spine, cfg))
    # konektor-bazirani IMAP (UI, per-org, tajne šifrirane) — svaki izoliran
    from atlas.business import mail_ingest
    rows = spine.read().execute(
        "SELECT id, org_id FROM connectors WHERE kind='mail_imap' AND status='connected'").fetchall()
    for r in rows:
        try:
            logger.info("imap_job(c%s): %s", r["id"],
                        mail_ingest.fetch_new(spine, cfg, r["id"], org_id=r["org_id"]))
        except Exception as e:
            logger.warning("imap_job connector %s: %s", r["id"], type(e).__name__)


def deadlines_job(spine, cfg) -> None:
    for row in kalendar.upcoming(spine, days=7):
        _notify_once(spine, "deadline", f"{row['description']} — rok {row['due']}")


def expiry_job(spine, cfg) -> None:
    for row in expiry.expiring(spine, days=30):
        body = f"{row['label']} ({row['client_name']}) istice {row['expires']}"
        _notify_once(spine, "expiry", body, client_id=row["client_id"])


def obveze_job(spine, cfg) -> None:
    period = _period_now()
    for kind in obveze.active_kinds(spine):
        obveze.ensure_period(spine, kind, period)


def rokovi_job(spine, cfg) -> None:
    added = rokovi.generate(spine)
    logger.info("rokovi_job: %d new deadline dates materialised", added)


def folders_sync_job(spine, cfg) -> None:
    r = folder_sync.sync_all(spine, cfg)
    logger.info("folders_sync_job: %(folders)d folders, %(ingested)d ingested, "
                "%(superseded)d superseded, %(skipped)d skipped", r)


def stale_job(spine, cfg) -> None:
    count = watchlist.mark_stale(spine)
    logger.info("stale_job: %d documents marked stale", count)


def health_job(spine, cfg) -> None:
    health.check(spine, cfg)


def backup_job(spine, cfg) -> None:
    from atlas.ops import backup
    backup.create_backup(cfg)
    backup.prune(cfg, keep=14)


def reminders_dump_job(spine, cfg) -> None:
    result = reminders_dump.dump(spine, cfg)
    logger.info("reminders_dump_job: wrote %s (%d items)", result["path"], result["count"])


def memory_decay_job(spine, cfg) -> None:
    count = memory.decay_all(spine)
    logger.info("memory_decay_job: %d rows decayed", count)


def memory_distill_job(spine, cfg) -> None:
    """L0 → L1 atomi → L3 persona za svakog korisnika s nedestiliranim
    replikama. Bez dostupnog LLM-a distill sam tiho preskače."""
    from atlas.business import model_settings
    from atlas.core.llm import LLMClient
    from atlas.knowledge import memory_layers
    llm = LLMClient(model_settings.apply(spine, cfg))
    pairs = spine.read().execute(
        "SELECT DISTINCT org_id, user_id FROM mem_l0 WHERE distilled=0").fetchall()
    done = 0
    for p in pairs:
        try:
            res = memory_layers.distill(spine, p["org_id"], p["user_id"], llm)
            if res.get("atoms"):
                memory_layers.build_persona(spine, p["org_id"], p["user_id"], llm)
            done += 1
        except Exception:
            logger.exception("memory_distill_job: org=%s user=%s", p["org_id"], p["user_id"])
    logger.info("memory_distill_job: %d/%d korisnika destilirano", done, len(pairs))


def power_job(spine, cfg) -> None:
    """UPS poller: jedan tik stroja stanja kad je nadzor uključen."""
    if not power.get_config(spine)["enabled"]:
        return
    out = power.evaluate(spine, cfg, now=time.time())
    if out.get("executed"):
        logger.warning("power_job: gašenje redom izvršeno: %s", out["executed"])


def scheduled_tasks_job(spine, cfg) -> None:
    """Fira dospjele owner-zakazane zadatke (allowlist akcija, dedupe po danu)."""
    from atlas.business import scheduler_tasks
    fired = scheduler_tasks.run_due(spine, cfg)
    if fired:
        logger.info("scheduled_tasks_job: %d firano", len(fired))


def register_defaults(sched) -> None:
    sched.register(Job(name="watchlist", fn=watchlist_job, interval_s=3600))
    sched.register(Job(name="imap", fn=imap_job, interval_s=300))
    sched.register(Job(name="scheduled_tasks", fn=scheduled_tasks_job, interval_s=300))
    sched.register(Job(name="deadlines", fn=deadlines_job, interval_s=0, daily=True, at_hour=7))
    sched.register(Job(name="expiry", fn=expiry_job, interval_s=0, daily=True, at_hour=7))
    sched.register(Job(name="obveze", fn=obveze_job, interval_s=0, daily=True, at_hour=6))
    sched.register(Job(name="rokovi", fn=rokovi_job, interval_s=0, daily=True, at_hour=5))
    sched.register(Job(name="folders_sync", fn=folders_sync_job, interval_s=0, daily=True, at_hour=5))
    sched.register(Job(name="stale", fn=stale_job, interval_s=0, daily=True, at_hour=6))
    sched.register(Job(name="health", fn=health_job, interval_s=900))
    sched.register(Job(name="power", fn=power_job, interval_s=30))
    sched.register(Job(name="backup", fn=backup_job, interval_s=0, daily=True, at_hour=2))
    sched.register(
        Job(name="digest", fn=digest.digest_job, interval_s=0, daily=True, at_hour=sched.cfg.digest_hour)
    )
    sched.register(Job(name="reminders_dump", fn=reminders_dump_job, interval_s=3600))
    sched.register(Job(name="memory_decay", fn=memory_decay_job, interval_s=0, daily=True, at_hour=4))
    sched.register(Job(name="memory_distill", fn=memory_distill_job, interval_s=0, daily=True, at_hour=3))
