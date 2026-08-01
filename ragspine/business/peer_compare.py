# Peer (inter-staff) booking-disagreement detection: internal-consistency QA
# signal for a multi-bookkeeper office. Flags when two different staff members
# book the SAME kind of transaction to DIFFERENT konto codes — a learning
# opportunity, not surveillance. Never auto-resolves, only flags.
import re

from ragspine.business.feedback_learn import _norm as _base_norm

# Variable-token normalization so "Račun 55 od 1.7." and "Račun 88 od 3.8."
# collapse to the same signature ("racun # od DATE") — order matters: dates
# first (before generic digits eat the day/month pieces), then OIB (11
# digits), then any remaining numbers.
_DATE_RE = re.compile(r"\b\d{1,2}\.\d{1,2}\.(?:\d{2,4})?")
_OIB_RE = re.compile(r"\b\d{11}\b")
_NUM_RE = re.compile(r"\d+")


def _norm(description: str) -> str:
    s = _base_norm(description)
    s = _DATE_RE.sub("DATE", s)
    s = _OIB_RE.sub("OIB", s)
    s = _NUM_RE.sub("#", s)
    return s


def record_booking(spine, user: str, description: str, konto: str, amount: float = 0) -> int:
    norm = _norm(description)
    with spine.write() as c:
        cur = c.execute(
            """INSERT INTO peer_bookings(user, description, description_norm, konto, amount)
               VALUES(?,?,?,?,?)""",
            (user, description, norm, konto, amount),
        )
        booking_id = cur.lastrowid
    spine.audit(user, "peer_booking", konto, description[:80])
    return booking_id


def find_disagreements(spine, days: int = 30) -> list[dict]:
    rows = spine.read().execute(
        """SELECT description, description_norm, user, konto FROM peer_bookings
           WHERE at >= datetime('now', ?) ORDER BY at DESC""",
        (f"-{days} days",),
    ).fetchall()

    groups: dict[str, dict] = {}
    for row in rows:
        norm = row["description_norm"]
        g = groups.setdefault(norm, {"kontos": {}, "sample_description": row["description"]})
        users = g["kontos"].setdefault(row["konto"], [])
        if row["user"] not in users:
            users.append(row["user"])

    result = []
    for norm, g in groups.items():
        kontos = g["kontos"]
        distinct_users = {u for users in kontos.values() for u in users}
        if len(kontos) >= 2 and len(distinct_users) >= 2:
            result.append({"description_norm": norm, "kontos": kontos,
                            "sample_description": g["sample_description"]})

    # most-conflicting first; ties keep insertion order, which is
    # most-recent-first since the source query is ORDER BY at DESC.
    result.sort(key=lambda d: len(d["kontos"]), reverse=True)
    return result


def peer_summary(spine, days: int = 30) -> str:
    disagreements = find_disagreements(spine, days=days)
    if not disagreements:
        return "Nema neslaganja u knjiženju."

    lines = []
    for d in disagreements:
        parts = []
        for konto, users in d["kontos"].items():
            for u in users:
                parts.append(f"{u} knjižila {konto}" if not parts else f"{u} {konto}")
        lines.append(f"Neslaganje u knjiženju: '{d['description_norm']}' — "
                      f"{', '.join(parts)}. Uskladiti.")
    return " ".join(lines)
