"""Telegram gateway: osoblje ureda piše Telegram BOTU → RAGSPINE vrti upit kroz
RAG pipeline → bot vraća odgovor. Službeni Bot API (getUpdates long-poll +
sendMessage), čisti HTTP (urllib) — bez teške async ovisnosti. NIJE linked-device.

Auth: /start <token> upari chat_id na RAGSPINE korisnika (token generira admin);
neuparen chat_id ne dobiva pristup uredskim podacima. Poll-petlja radi u
background threadu (RAGSPINE rute su sync)."""
import json
import secrets
import time
import urllib.parse
import urllib.request

_API = "https://api.telegram.org/bot"
_MAX = 4000  # Telegram limit 4096; ostavi zraka


class TelegramClient:
    """Minimalan Bot API klijent (getMe/getUpdates/sendMessage) preko urllib."""

    def __init__(self, token: str, timeout: int = 35):
        self._base = _API + token
        self.timeout = timeout

    def _call(self, method: str, params: dict, timeout: int | None = None) -> dict:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"{self._base}/{method}", data=data)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            return json.loads(resp.read())

    def get_me(self) -> dict:
        return self._call("getMe", {}, timeout=10)

    def get_updates(self, offset: int, timeout: int = 30) -> list:
        r = self._call("getUpdates", {"offset": offset, "timeout": timeout},
                       timeout=timeout + 5)
        return r.get("result", []) if r.get("ok") else []

    def send_message(self, chat_id: int, text: str) -> None:
        for part in split_message(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": part}, timeout=15)


def split_message(text: str, limit: int = _MAX) -> list[str]:
    """Razbij dugi odgovor na dijelove ≤ limit, po granicama redaka gdje ide."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else [""]
    out, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # jedan predugačak redak
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:limit])
            line = line[limit:]
        if not line:
            continue
        if buf and len(buf) + len(line) + 1 > limit:
            out.append(buf)
            buf = line
        elif buf:
            buf = f"{buf}\n{line}"
        else:
            buf = line
    if buf:
        out.append(buf)
    return out


# --- pairing / auth ---

_TOKEN_TTL_S = 600  # token vrijedi 10 min (Codex: bez roka je rizik)


def create_pairing_token(spine, user_id: int, org_id: int) -> str:
    """Token veže Telegram na OVOG korisnika (self-service — ne admin za drugoga,
    inače radnik postane admin). Jednokratan, s rokom."""
    token = secrets.token_urlsafe(16)
    with spine.write() as c:
        c.execute("INSERT INTO telegram_pairing(token, user_id, org_id) VALUES(?,?,?)",
                  (token, user_id, org_id))
    return token


def _link_for(spine, chat_id: int):
    return spine.read().execute(
        "SELECT user_id, org_id, username FROM telegram_links WHERE chat_id=?", (chat_id,)).fetchone()


def _consume_pairing(spine, token: str, chat_id: int, username: str) -> bool:
    with spine.write() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute(
                "SELECT user_id, org_id FROM telegram_pairing WHERE token=? AND used=0 "
                "AND (strftime('%s','now') - strftime('%s', created_at)) < ?",
                (token, _TOKEN_TTL_S)).fetchone()
            if row is None:
                c.execute("ROLLBACK")
                return False
            c.execute("UPDATE telegram_pairing SET used=1 WHERE token=?", (token,))
            c.execute("INSERT OR REPLACE INTO telegram_links(chat_id, user_id, org_id, username) VALUES(?,?,?,?)",
                      (chat_id, row["user_id"], row["org_id"], username))
        except Exception:
            c.execute("ROLLBACK")
            raise
        else:
            c.execute("COMMIT")
    return True


def _resolve_actor(spine, link):
    from ragspine.business import tenancy
    actor = tenancy.actor_for(spine, link["org_id"], link["user_id"])
    if actor is not None:
        row = spine.read().execute("SELECT username FROM users WHERE id=?", (link["user_id"],)).fetchone()
        actor.username = row["username"] if row else "telegram"
    return actor


def handle_update(spine, cfg, update: dict, answer_fn, tg: "TelegramClient",
                  limiter=None) -> None:
    """Obradi jedan update: /start uparivanje ili upit kroz answer_fn. answer_fn
    je (query, actor) -> dict s 'answer'. Sve greške izolirane (bot ne pada)."""
    if not isinstance(update, dict):
        return
    msg = update.get("message")
    if not isinstance(msg, dict):
        return  # ignoriraj edited/callback/channel_post itd.
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    chat_id = chat.get("id")
    text = msg.get("text")
    if not isinstance(chat_id, int) or not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    # SAMO privatni chat, i pošiljatelj == chat (Codex: /start u grupi bi dao
    # pristup svim članovima pod tuđim računom)
    if chat.get("type") != "private" or frm.get("id") != chat_id:
        return
    username = chat.get("username") or chat.get("first_name") or ""

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        if token and _consume_pairing(spine, token, chat_id, username):
            tg.send_message(chat_id, "✓ Uparen s RAGSPINE-om. Piši pitanje.")
        else:
            tg.send_message(chat_id, "Za pristup treba token: u RAGSPINE-u (Postavke → "
                                     "Telegram) generiraj token pa pošalji: /start <token>")
        return

    link = _link_for(spine, chat_id)
    if link is None:
        tg.send_message(chat_id, "Nisi uparen. Zatraži token u uredu pa: /start <token>")
        return
    # limit po RAGSPINE korisniku (ne chat_id — isti korisnik može vezati više
    # chatova); LLM je skup. Dnevni token-budžet je ponytail upgrade.
    if limiter is not None and not limiter.allow(f"tg:u{link['user_id']}", limit=10, window_s=60.0):
        tg.send_message(chat_id, "Previše upita — pričekaj minutu.")
        return
    actor = _resolve_actor(spine, link)
    if actor is None:
        tg.send_message(chat_id, "Korisnički račun više nije aktivan.")
        return
    try:
        res = answer_fn(text, actor)
        tg.send_message(chat_id, res.get("answer") or "(prazan odgovor)")
    except Exception as e:
        tg.send_message(chat_id, f"Greška: {type(e).__name__}")


# --- offset persistence + poll loop ---

def _get_offset(spine, key: str) -> int:
    r = spine.read().execute("SELECT v FROM telegram_state WHERE k=?", (f"offset:{key}",)).fetchone()
    return int(r["v"]) if r and r["v"] else 0


def _set_offset(spine, key: str, offset: int) -> None:
    with spine.write() as c:
        c.execute("INSERT OR REPLACE INTO telegram_state(k, v) VALUES(?,?)", (f"offset:{key}", str(offset)))


def poll_loop(spine, cfg, token: str, answer_fn, stop_event, limiter=None, key: str = "default") -> None:
    """Long-poll getUpdates dok stop_event nije postavljen. Offset se pamti da se
    poruke ne obrađuju dvaput. Greška u ciklusu se proguta (bot ostaje živ)."""
    tg = TelegramClient(token)
    offset = _get_offset(spine, key)
    while not stop_event.is_set():
        try:
            updates = tg.get_updates(offset, timeout=30)
        except Exception:
            stop_event.wait(3)
            continue
        for u in updates:
            uid = u.get("update_id") if isinstance(u, dict) else None
            try:
                handle_update(spine, cfg, u, answer_fn, tg, limiter=limiter)
            except Exception:
                pass
            # commit offset PO SVAKOM updateu (Codex: offset nakon batcha → duplikati
            # pri crashu). Offset naprijed i ako handle baci — poruka se ne ponavlja.
            if isinstance(uid, int):
                offset = max(offset, uid + 1)
                _set_offset(spine, key, offset)
