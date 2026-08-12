"""Telegram gateway: office staff write to the Telegram BOT -> ATLAS runs the
query through the RAG pipeline -> the bot returns an answer. Official Bot API
(getUpdates long-poll + sendMessage), plain HTTP (urllib) — no heavy async
dependency. NOT a linked device.

Auth: /start <token> pairs chat_id to an ATLAS user (the admin generates the
token); an unpaired chat_id gets no access to office data. The poll loop runs
in a background thread (ATLAS routes are sync)."""
import json
import secrets
import time
import urllib.parse
import urllib.request

_API = "https://api.telegram.org/bot"
_MAX = 4000  # Telegram limit 4096; leave some room


class TelegramClient:
    """Minimal Bot API client (getMe/getUpdates/sendMessage) over urllib."""

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
            if reply_markup and i == len(parts) - 1:  # buttons only on the last part
                payload["reply_markup"] = json.dumps(reply_markup)
            self._call("sendMessage", payload, timeout=15)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text},
                   timeout=10)


def split_message(text: str, limit: int = _MAX) -> list[str]:
    """Split a long reply into parts <= limit, on line boundaries where possible."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else [""]
    out, buf = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single overly long line
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

_TOKEN_TTL_S = 600  # token valid for 10 min (Codex: no expiry is a risk)


def create_pairing_token(spine, user_id: int, org_id: int) -> str:
    """The token binds Telegram to THIS user (self-service — not an admin acting
    for someone else, otherwise a worker would become an admin). One-time, with
    an expiry."""
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
    """Text for Telegram from the agent's result. A WRITE proposal (pending) is
    NOT executed silently — it requires an explicit confirmation (inline buttons,
    see _confirm_keyboard)."""
    text = res.get("text") or res.get("answer") or ""
    pending = res.get("pending")
    if pending:
        warn = ("\n\n🔴 VISOK RIZIK — vanjska radnja. Potvrdi izvršenje:"
                if pending.get("risk") == "high" else "\n\n⚠ Potvrdi izvršenje:")
        text = pending.get("summary", text) + warn
    return text or "(prazan odgovor)"


def _confirm_keyboard(token: str) -> dict:
    """Inline Confirm/Cancel buttons. callback_data carries the token (<=64 B);
    token ownership is re-checked on confirmation (confirm_pending scopes by
    user)."""
    return {"inline_keyboard": [[
        {"text": "✓ Potvrdi", "callback_data": f"ok:{token}"},
        {"text": "✕ Odustani", "callback_data": f"no:{token}"},
    ]]}


def _handle_callback(spine, cfg, cbq: dict, tg: "TelegramClient") -> None:
    """Handle a click on an inline button (confirm/cancel of an agent write).
    Works ONLY from a paired private chat; execution goes through confirm_pending
    which re-checks permissions + token ownership."""
    from atlas.rag import agent

    cb_id = cbq.get("id")
    data = cbq.get("data")
    msg = cbq.get("message") if isinstance(cbq.get("message"), dict) else {}
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    frm = cbq.get("from") if isinstance(cbq.get("from"), dict) else {}
    chat_id = chat.get("id")
    if not isinstance(cb_id, str) or not isinstance(data, str) or not isinstance(chat_id, int):
        return
    # only a private chat and a click from the chat owner (as with messages)
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
    """Handle a single update: /start pairing or a query through answer_fn.
    answer_fn is (query, actor) -> dict with 'answer'. All errors are isolated
    (the bot does not crash)."""
    if not isinstance(update, dict):
        return
    cbq = update.get("callback_query")
    if isinstance(cbq, dict):  # click on an inline button (confirm/cancel)
        try:
            _handle_callback(spine, cfg, cbq, tg)
        except Exception:
            pass  # the bot does not crash on a single update
        return
    msg = update.get("message")
    if not isinstance(msg, dict):
        return  # ignore edited/channel_post etc.
    chat = msg.get("chat") if isinstance(msg.get("chat"), dict) else {}
    frm = msg.get("from") if isinstance(msg.get("from"), dict) else {}
    chat_id = chat.get("id")
    text = msg.get("text")
    if not isinstance(chat_id, int) or not isinstance(text, str):
        return
    text = text.strip()
    if not text:
        return
    # ONLY a private chat, and sender == chat (Codex: /start in a group would give
    # all members access under someone else's account)
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
    # rate limit per ATLAS user (not chat_id — the same user can bind multiple
    # chats); the LLM is expensive. A daily token budget is a ponytail upgrade.
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
    """Long-poll getUpdates until stop_event is set. The offset is persisted so
    messages are not processed twice. An error in a cycle is swallowed (the bot
    stays alive)."""
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
            # commit the offset AFTER EACH update (Codex: an offset after the batch ->
            # duplicates on a crash). Advance the offset even if handle raises — the
            # message is not repeated.
            if isinstance(uid, int):
                offset = max(offset, uid + 1)
                _set_offset(spine, key, offset)
