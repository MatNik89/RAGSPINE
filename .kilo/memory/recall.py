"""FSRS-lite decay — true half-life for memory retrievability.

Pattern: frequently accessed memories decay slower. Retrievability = exp(-ln2 * days / stability).
ponytail: stability-weighted exponential, not full FSRS-6. One formula, zero complexity budget.
"""

import hashlib
import math
from datetime import datetime, timedelta, timezone

from .store import store

HALFLIFE_DAYS = 14.0
FLOOR = 0.25
_LN2 = math.log(2)


def _h(scope, content):
    return hashlib.sha1(f"{scope}\0{content}".encode()).hexdigest()


def _utc(dt):
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _now(now=None):
    return _utc(now) if now else datetime.now(timezone.utc)


def touch(scope, content, now=None):
    k, ts = _h(scope, content), _now(now).isoformat()
    store().execute(
        "INSERT INTO mem_stats(hash,scope,accesses,last_access) VALUES(?,?,1,?) "
        "ON CONFLICT(hash) DO UPDATE SET accesses=accesses+1, "
        "last_access=MAX(last_access, excluded.last_access)",
        (k, scope, ts))
    store().commit()


def retrievability(scope, content, now=None) -> float:
    row = store().execute(
        "SELECT accesses, last_access FROM mem_stats WHERE hash=?",
        (_h(scope, content),)).fetchone()
    if not row:
        return 1.0
    accesses, last = row
    try:
        days = max(0.0, (_now(now) - _utc(datetime.fromisoformat(last))).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 1.0
    stability = HALFLIFE_DAYS * (1.0 + math.log1p(max(0, accesses)))
    return math.exp(-_LN2 * days / stability)


def rescore(entries, now=None):
    return sorted(entries, key=lambda e: -retrievability(e.scope, e.content, now))


def layer(ranked, candidates, scope, limit, now=None):
    """Re-order by retrievability, pull in top hit's associates."""
    from .store import store as s
    ranked = rescore(ranked, now)
    if not ranked:
        return ranked
    present = {_h(e.scope, e.content) for e in ranked}
    t = ranked[0]
    rows = s().execute(
        "SELECT CASE WHEN a=? THEN b ELSE a END AS other, weight FROM mem_assoc "
        "WHERE (a=? OR b=?) AND a<>b ORDER BY weight DESC LIMIT 3",
        (_h(t.scope, t.content), _h(t.scope, t.content), _h(t.scope, t.content))).fetchall()
    want = {r[0] for r in rows if r[0] not in present}
    if want:
        seen, uniq = set(present), []
        for e in candidates:
            he = _h(e.scope, e.content)
            if he in want and he not in seen:
                seen.add(he)
                uniq.append(e)
        if uniq:
            ranked = ranked[:max(1, limit - min(len(uniq), 2))] + uniq[:2]
    for e in ranked[:limit]:
        touch(e.scope, e.content, now)
    return ranked[:limit]


def co_activate(items):
    """Strengthen associative edges among memories recalled together."""
    keys = list(dict.fromkeys(_h(s, c) for s, c in items))
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            lo, hi = sorted((a, b))
            store().execute(
                "INSERT INTO mem_assoc(a,b,weight) VALUES(?,?,1) "
                "ON CONFLICT(a,b) DO UPDATE SET weight=weight+1", (lo, hi))
    store().commit()


def consolidate(entries, now=None) -> list:
    return [e for e in entries
            if retrievability(e.scope, e.content, now) < FLOOR
            and getattr(e, "confidence", 1.0) < 0.5]
