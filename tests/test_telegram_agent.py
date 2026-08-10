"""Telegram kroz agenta: read-akcije prolaze; write prijedlog traži izričitu
potvrdu (inline tipke), izvršenje ide kroz confirm_pending."""
from atlas.business import telegram_gateway as tgw


def test_read_reply_passthrough():
    res = {"text": "PDV je 25%.", "sources": [], "pending": None}
    assert tgw.format_agent_reply(res) == "PDV je 25%."


def test_write_pending_asks_confirmation():
    res = {"text": "Dodat ću klijenta X.", "sources": [],
           "pending": {"tool": "dodaj_klijenta", "summary": "Dodat ću klijenta X."}}
    out = tgw.format_agent_reply(res)
    assert "Dodat ću klijenta X." in out
    assert "Potvrdi" in out  # traži potvrdu


def test_confirm_keyboard_carries_token():
    kb = tgw._confirm_keyboard("TKN123")
    btns = kb["inline_keyboard"][0]
    assert btns[0]["callback_data"] == "ok:TKN123"
    assert btns[1]["callback_data"] == "no:TKN123"
    assert all(len(b["callback_data"]) <= 64 for b in btns)  # Telegram limit


def test_empty_reply_safe():
    assert tgw.format_agent_reply({}) == "(prazan odgovor)"
