"""Telegram kroz agenta: read-akcije prolaze; write prijedlog se bounce-a na
potvrdu u aplikaciji (bez tihog write-a s telefona)."""
from atlas.business import telegram_gateway as tgw


def test_read_reply_passthrough():
    res = {"text": "PDV je 25%.", "sources": [], "pending": None}
    assert tgw.format_agent_reply(res) == "PDV je 25%."


def test_write_pending_bounced_to_app():
    res = {"text": "Dodat ću klijenta X.", "sources": [],
           "pending": {"tool": "dodaj_klijenta", "summary": "Dodat ću klijenta X."}}
    out = tgw.format_agent_reply(res)
    assert "Dodat ću klijenta X." in out
    assert "aplikaciji" in out  # bounce na potvrdu

def test_empty_reply_safe():
    assert tgw.format_agent_reply({}) == "(prazan odgovor)"
