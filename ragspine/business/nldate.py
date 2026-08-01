# Croatian natural-language date parsing for reminders ("za 10 dana", "sutra",
# "u petak", "do 15.9.") — reimplemented from scratch, no external date libs.
import re
import unicodedata
from datetime import date, timedelta

_WEEKDAYS = {
    "ponedjeljak": 0, "utorak": 1, "srijedu": 2, "cetvrtak": 3,
    "petak": 4, "subotu": 5, "nedjelju": 6,
}
_WEEKDAY_RE = "|".join(_WEEKDAYS)


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def parse_date(text: str, now_fn=None) -> str | None:
    today = (now_fn or date.today)()
    t = _strip_diacritics(text.strip().lower())

    m = re.search(r"\bza\s+(\d+)\s+dan\w*", t)
    if m:
        return (today + timedelta(days=int(m.group(1)))).isoformat()

    if re.search(r"\bprekosutra\b", t):
        return (today + timedelta(days=2)).isoformat()
    if re.search(r"\bsutra\b", t):
        return (today + timedelta(days=1)).isoformat()
    if re.search(r"\bdanas\b", t):
        return today.isoformat()

    m = re.search(rf"\bu\s+({_WEEKDAY_RE})\b", t)
    if m:
        target = _WEEKDAYS[m.group(1)]
        delta = (target - today.weekday()) % 7
        if delta == 0:
            delta = 7  # today IS that weekday -> next week, not today
        return (today + timedelta(days=delta)).isoformat()

    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", t)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if not m.group(3) and d < today:
            d = date(year + 1, month, day)  # year omitted + already passed -> next year
        return d.isoformat()

    return None


def set_reminder_nl(spine, user: str, body: str, when_text: str, now_fn=None) -> dict:
    due = parse_date(when_text, now_fn=now_fn)
    if due is None:
        return {"error": "Ne razumijem datum"}
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO reminders(user, body, due, done) VALUES(?,?,?,0)",
            (user, body, due),
        )
        return {"id": cur.lastrowid, "due": due}
