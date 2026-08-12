"""Zakazani zadaci ureda (owner kreira): pokreni ODOBRENU akciju na dan/sat
(npr. 20. u mjesecu u 8h -> kampanja podsjetnika za nepredanu obvezu).

Sigurnost (kao fleet program_key): akcija je iz ALLOWLISTE — NIKAD proizvoljan
kod. Owner-only kreiranje. Consent-gate ostaje na razini slanja (send_to_client
preskače klijente bez pristanka). Ponytail: bez croniter-a — day_of_month/hour
pokriva uredske potrebe; poller (5 min) fira dospjele, dedupe po datumu."""
import calendar
import json
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Zagreb")
except Exception:  # pragma: no cover — tzdata nedostaje
    _TZ = None


def _now(now: datetime | None = None) -> datetime:
    """Uredski sat = Europe/Zagreb (ne naivni host-clock) — inače UTC host fira u
    krivi sat/mjesec i due-calc se razilazi s periodom kampanje (Codex)."""
    if now is not None:
        return now
    return datetime.now(_TZ) if _TZ else datetime.now()


def _resolve_actor(spine, org_id, created_by):
    """Actor (identitet+ovlasti+vidljivost) za autonomni run — po created_by u org-u."""
    from atlas.business import tenancy
    row = spine.read().execute("SELECT id FROM users WHERE username=?", (created_by or "",)).fetchone()
    if row is None:
        return None
    actor = tenancy.actor_for(spine, org_id, row["id"])
    if actor is not None:  # atribucija: audit/autor mora nositi ime, ne prazno (Codex)
        actor.username = created_by
    return actor


def _run_autonomni_pregled(spine, cfg, params: dict, org_id, created_by) -> dict:
    """Autonomni (nenadzirani) agent-run nad zadanim promptom: agent AUTONOMNO
    odradi read/draft; svaku write-radnju PARKIRA za odobrenje vlasnika (ne dira
    podatke; high uvijek parkiran). 'Priprema -> potpis' obrazac (OpenWorker/MateClaw)."""
    from atlas.business import model_settings
    from atlas.core.llm import LLMError, LLMUnavailable
    from atlas.rag import agent
    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("akcija autonomni_pregled traži 'prompt'")
    actor = _resolve_actor(spine, org_id, created_by)
    if actor is None:
        raise ValueError("ne mogu razriješiti korisnika zadatka za autonomni run")
    llm = model_settings.build_llm(spine, cfg)
    try:
        out = agent.run_unattended(spine, cfg, prompt, actor, llm,
                                   source=f"zakazano:{created_by}")
    except (LLMUnavailable, LLMError) as e:
        raise ValueError(f"LLM nedostupan za autonomni run: {type(e).__name__}") from e
    return {"parkirano": len(out.get("parkirano", [])),
            "izvrseno": len(out.get("izvrseno", [])), "tekst": (out.get("text") or "")[:200]}


def _validate_autonomni(params: dict) -> None:
    if not (params.get("prompt") or "").strip():
        raise ValueError("'prompt' je obavezan (što agent treba pripremiti)")


def _run_kampanja_obveza(spine, cfg, params: dict, org_id, created_by=None) -> dict:
    """Pošalji podsjetnik klijentima s NEPREDANOM obvezom `kind` za TEKUĆI mjesec
    (period se računa pri firanju). Consent-gated (send_to_client preskače bez
    pristanka). NAPOMENA: clients/obligations u ATLAS-u NISU org-particionirani
    (jedan ured) — doseg je identičan postojećoj ručnoj /messaging/campaign (admin,
    office-wide); org_id se čuva na zadatku za buduću multi-tenancy."""
    from atlas.web import messaging
    kind = (params.get("kind") or "").strip()
    if not kind:
        raise ValueError("akcija kampanja_obveza traži 'kind' (npr. PDV)")
    period = _now().strftime("%Y-%m")
    subject = params.get("subject") or f"Podsjetnik: {kind} obveza"
    body = params.get("body") or f"Poštovani, molimo dostavu dokumentacije za {kind}."
    return messaging.send_to_filter(spine, cfg, "compliance_missing", subject, body,
                                    dry_run=False, kind=kind, period=period)


# allowlist: key -> (label, validate(params)->None, run(spine,cfg,params)->dict)
def _validate_kampanja(params: dict) -> None:
    if not (params.get("kind") or "").strip():
        raise ValueError("'kind' je obavezan (npr. PDV)")


ACTIONS = {
    "kampanja_obveza": ("Kampanja podsjetnika za nepredanu obvezu",
                        _validate_kampanja, _run_kampanja_obveza),
    "autonomni_pregled": ("Autonomni AI pregled/priprema (rezultat ide u red za odobrenje)",
                          _validate_autonomni, _run_autonomni_pregled),
}


def action_labels() -> list[dict]:
    return [{"key": k, "label": v[0]} for k, v in ACTIONS.items()]


def create_task(spine, org_id, title, action_key, params, day_of_month, hour,
                user="?") -> int:
    if action_key not in ACTIONS:
        raise ValueError(f"nepoznata akcija: {action_key!r}")
    ACTIONS[action_key][1](params or {})  # validacija parametara akcije
    dom = None if day_of_month in (None, "", 0) else int(day_of_month)
    if dom is not None and not (1 <= dom <= 31):
        raise ValueError("dan u mjesecu mora biti 1-31 (ili prazno = svaki dan)")
    h = int(hour if hour not in (None, "") else 8)
    if not (0 <= h <= 23):
        raise ValueError("sat mora biti 0-23")
    with spine.write() as c:
        return c.execute(
            "INSERT INTO scheduled_tasks(org_id,title,action_key,params_json,"
            "day_of_month,hour,created_by) VALUES(?,?,?,?,?,?,?)",
            (org_id, (title or action_key).strip()[:120], action_key,
             json.dumps(params or {}, ensure_ascii=False), dom, h, user)).lastrowid


def list_tasks(spine, org_id) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id,title,action_key,params_json,day_of_month,hour,enabled,last_run_date,"
        "created_by FROM scheduled_tasks WHERE org_id=? ORDER BY id", (org_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["params"] = json.loads(d.pop("params_json") or "{}")
        out.append(d)
    return out


def set_enabled(spine, task_id, org_id, enabled: bool) -> None:
    with spine.write() as c:
        c.execute("UPDATE scheduled_tasks SET enabled=? WHERE id=? AND org_id=?",
                  (1 if enabled else 0, task_id, org_id))


def delete_task(spine, task_id, org_id) -> None:
    with spine.write() as c:
        c.execute("DELETE FROM scheduled_tasks WHERE id=? AND org_id=?", (task_id, org_id))


def _effective_day(day_of_month, year: int, month: int) -> int | None:
    """Dan 29-31 u kraćem mjesecu -> zadnji dan mjeseca (inače mjesečni zadatak
    tiho nikad ne bi firao u veljači; Codex)."""
    if day_of_month is None:
        return None
    return min(int(day_of_month), calendar.monthrange(year, month)[1])


def _due(row, now: datetime) -> bool:
    if not row["enabled"]:
        return False
    eff = _effective_day(row["day_of_month"], now.year, now.month)
    if eff is not None and now.day != eff:
        return False
    if now.hour < (row["hour"] if row["hour"] is not None else 8):
        return False
    return row["last_run_date"] != now.date().isoformat()  # jednom po danu


# transient-retry ljestvica (min) — Paperclip bounded backoff; kratka jer poller je 5 min
_RETRY_BACKOFF_MIN = (2, 10, 30, 120)


def _is_transient(e: Exception) -> bool:
    """Samo PROLAZNE greške (LLM/mreža) idu na retry; logičke (validacija, uklonjena
    akcija) NE — inače bismo beskonačno ponavljali krivu radnju."""
    from atlas.core.llm import LLMError, LLMUnavailable
    return isinstance(e, (LLMError, LLMUnavailable, ConnectionError, TimeoutError, OSError))


def _schedule_retry(spine, task_id: int, attempt: int) -> bool:
    """Zakaži idući pokušaj (UTC, datetime('now') konzistentno). False = ljestvica
    iscrpljena -> pozivatelj dead-letteruje."""
    if attempt >= len(_RETRY_BACKOFF_MIN):
        return False
    delay = _RETRY_BACKOFF_MIN[attempt]
    with spine.write() as c:
        c.execute("UPDATE scheduled_tasks SET retry_at=datetime('now', ?), retry_attempt=? "
                  "WHERE id=?", (f"+{delay} minutes", attempt + 1, task_id))
    return True


def _clear_retry(spine, task_id: int) -> None:
    with spine.write() as c:
        c.execute("UPDATE scheduled_tasks SET retry_at=NULL, retry_attempt=0 WHERE id=?", (task_id,))


def _execute(spine, cfg, r, fired: list) -> None:
    """Pokreni jednu akciju. Uspjeh -> očisti retry. Prolazna greška + ljestvica ima
    mjesta -> zakaži retry. Trajna greška ili iscrpljena ljestvica -> dead-letter."""
    action = ACTIONS.get(r["action_key"])
    params = json.loads(r["params_json"] or "{}")
    status, detail = "ok", None
    try:
        if action is None:
            raise ValueError(f"akcija uklonjena iz allowliste: {r['action_key']}")
        res = action[2](spine, cfg, params, r["org_id"], r["created_by"])
        detail = json.dumps(res, ensure_ascii=False, default=str)[:300]
        _clear_retry(spine, r["id"])
    except Exception as e:  # ne ruši ostale zadatke
        attempt = r["retry_attempt"] or 0
        if _is_transient(e) and _schedule_retry(spine, r["id"], attempt):
            status = "retry"
        else:
            status = "error"
            _clear_retry(spine, r["id"])
            _notify(spine, r["org_id"], r["id"], type(e).__name__)
    spine.audit(r["created_by"] or "sustav", "scheduled_run",
                f"task:{r['id']}:{r['action_key']}", status)
    fired.append({"id": r["id"], "status": status, "detail": detail})


def run_due(spine, cfg, now: datetime | None = None) -> list[dict]:
    """Fira dospjele zadatke + dospjele retry-pokušaje. ATOMIČAN claim PRIJE izvršenja
    (dva paralelna pollera / pad NE dupliraju; at-most-once). Prolazna greška zakaže
    retry po backoff-ljestvici umjesto tihog gubitka mjesečne akcije (Paperclip)."""
    now = _now(now)
    today = now.date().isoformat()
    fired = []
    # 1) redovni dospjeli — dnevni claim
    for r in spine.read().execute("SELECT * FROM scheduled_tasks WHERE enabled=1").fetchall():
        if not _due(r, now):
            continue
        with spine.write() as c:  # claim: samo jedan poller prođe
            claim = c.execute("UPDATE scheduled_tasks SET last_run_date=? "
                              "WHERE id=? AND (last_run_date IS NULL OR last_run_date!=?)",
                              (today, r["id"], today))
            if claim.rowcount != 1:
                continue  # netko drugi već preuzeo danas
        _execute(spine, cfg, r, fired)
    # 2) dospjeli retry-pokušaji — claim pomicanjem retry_at=NULL (UTC usporedba)
    rrows = spine.read().execute(
        "SELECT * FROM scheduled_tasks WHERE enabled=1 AND retry_at IS NOT NULL "
        "AND retry_at <= datetime('now')").fetchall()
    for r in rrows:
        with spine.write() as c:  # claim retry: makni retry_at da ga drugi poller ne pokupi
            claim = c.execute("UPDATE scheduled_tasks SET retry_at=NULL "
                              "WHERE id=? AND retry_at IS NOT NULL", (r["id"],))
            if claim.rowcount != 1:
                continue
        _execute(spine, cfg, r, fired)  # r još nosi retry_attempt (nije nuliran)
    return fired


def _notify(spine, org_id, task_id, reason: str) -> None:
    # bez naslova zadatka (owner-birano, moglo bi biti osjetljivo) — samo org+id+razlog
    body = f"Zakazani zadatak #{task_id} (org {org_id}) nije uspio: {reason}"
    with spine.write() as c:
        seen = c.execute("SELECT 1 FROM notifications WHERE kind='scheduled_error' "
                         "AND body=? AND at >= datetime('now','-1 day')", (body,)).fetchone()
        if seen is None:
            c.execute("INSERT INTO notifications(kind, body) VALUES('scheduled_error', ?)", (body,))
