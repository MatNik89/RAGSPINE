"""Telegram gateway: osoblje ureda piše Telegram BOTU → ATLAS vrti upit kroz
RAG pipeline → bot vraća odgovor. Službeni Bot API (getUpdates long-poll +
sendMessage), čisti HTTP (urllib) — bez teške async ovisnosti. NIJE linked-device.

Auth: /start <token> upari chat_id na ATLAS korisnika (token generira admin);
neuparen chat_id ne dobiva pristup uredskim podacima. Poll-petlja radi u
background threadu (ATLAS rute su sync)."""
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

    def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        parts = split_message(text)
        for i, part in enumerate(parts):
            payload = {"chat_id": chat_id, "text": part}
            if reply_markup and i == len(parts) - 1:  # tipke samo uz zadnji dio
                payload["reply_markup"] = json.dumps(reply_markup)
            self._call("sendMessage", payload, timeout=15)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text},
                   timeout=10)


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


def format_agent_reply(res: dict) -> str:
    """Tekst za Telegram iz rezultata agenta. WRITE prijedlog (pending) se NE
    izvršava tiho — traži izričitu potvrdu (inline tipke, vidi _confirm_keyboard)."""
    text = res.get("text") or res.get("answer") or ""
    pending = res.get("pending")
    if pending:
        text = pending.get("summary", text) + "\n\n⚠ Potvrdi izvršenje:"
    return text or "(prazan odgovor)"


def _confirm_keyboard(token: str) -> dict:
    """Inline tipke Potvrdi/Odustani. callback_data nosi token (≤64 B); vlasništvo
    tokena se PONOVO provjeri pri potvrdi (confirm_pending scope-a po korisniku)."""
    return {"inline_keyboard": [[
        {"text": "✓ Potvrdi", "callback_data": f"ok:{token}"},
        {"text": "✕ Odustani", "callback_data": f"no:{token}"},
    ]]}


def _handle_callback(spine, cfg, cbq: dict, tg: "TelegramClient") -> None:
    """Obradi klik na inline tipku (potvrda/odustajanje agentskog write-a). Radi
    SAMO iz uparenog privatnog chata; izvršenje ide kroz confirm_pending koji
    ponovo provjeri ovlasti + vlasništvo tokena."""
    from atlas.rag import agent

    cb_id = cbq.get("id")
    data = cbq.get("data")
    msg = cbq.get("message") if isinstance(cbq.get("message"), dict) else {}
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    frm = cbq.get("from") if isinstance(cbq.get("from"), dict) else {}
    chat_id = chat.get("id")
    if not isinstance(cb_id, str) or not isinstance(data, str) or not isinstance(chat_id, int):
        return
    # samo privatni chat i klik od vlasnika chata (kao kod poruka)
    if chat.get("type") != "private" or frm.get("id") != chat_id:
        return
    link = _link_for(spine, chat_id)
    if link is None:
        return
    actor = _resolve_actor(spine, link)
    if actor is None:
        tg.answer_callback(cb_id, "Račun nije aktivan.")
        return
    action, _, token = data.partition(":")
    if not token:
        tg.answer_callback(cb_id, "")
        return
    try:
        if action == "ok":
            agent.confirm_pending(spine, cfg, token, actor)
            tg.answer_callback(cb_id, "✓ Izvršeno")
            tg.send_message(chat_id, "✓ Radnja izvršena.")
        else:
            agent.cancel_pending(spine, token, actor)
            tg.answer_callback(cb_id, "Odustao")
            tg.send_message(chat_id, "Odustao od radnje.")
    except ValueError as e:
        tg.answer_callback(cb_id, str(e))
    except Exception as e:
        tg.answer_callback(cb_id, f"Greška: {type(e).__name__}")


def _resolve_actor(spine, link):
    from atlas.business import tenancy
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
    cbq = update.get("callback_query")
    if isinstance(cbq, dict):  # klik na inline tipku (potvrdi/odustani)
        try:
            _handle_callback(spine, cfg, cbq, tg)
        except Exception:
            pass  # bot ne pada na jednom updateu
        return
    msg = update.get("message")
    if not isinstance(msg, dict):
        return  # ignoriraj edited/channel_post itd.
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
            tg.send_message(chat_id, "✓ Uparen s ATLAS-om. Piši pitanje.")
        else:
            tg.send_message(chat_id, "Za pristup treba token: u ATLAS-u (Postavke → "
                                     "Telegram) generiraj token pa pošalji: /start <token>")
        return

    link = _link_for(spine, chat_id)
    if link is None:
        tg.send_message(chat_id, "Nisi uparen. Zatraži token u uredu pa: /start <token>")
        return
    # limit po ATLAS korisniku (ne chat_id — isti korisnik može vezati više
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
        token = res.get("pending_token")
        markup = _confirm_keyboard(token) if token else None
        tg.send_message(chat_id, res.get("answer") or "(prazan odgovor)", reply_markup=markup)
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
