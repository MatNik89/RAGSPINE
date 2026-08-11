"""Zaštite agentske petlje: StructuredTruncator, OIB-evidence guard, loop-guard."""
from atlas.rag import agent_guards as g

VALID = "10000000000"    # valjan OIB (checksum)
VALID2 = "69435151530"


def test_truncate_snaps_to_json_boundary():
    text = '{"a":1,"b":2,"c":3,"d":4}' * 500
    out = g.truncate_structured(text, limit=100)
    assert len(out) <= 200 and "skraćeno" in out
    # ne završava usred tokena (zadnji nije-fidelity znak je struktura)
    body = out.split("…")[0]
    assert body.rstrip().endswith((",", "}", "]"))


def test_truncate_short_unchanged():
    assert g.truncate_structured('{"a":1}', limit=100) == '{"a":1}'


def test_unverified_oib_flagged_when_not_observed():
    assert g.unverified_oibs(f"Klijent OIB {VALID}", "nema oiba ovdje") == [VALID]


def test_verified_oib_not_flagged():
    assert g.unverified_oibs(f"OIB {VALID}", f"rezultat: {VALID} pekara") == []


def test_invalid_oib_ignored():
    # 11 znamenki ali kriv checksum -> nije OIB -> ne flag
    assert g.unverified_oibs("broj 12345678901", "") == []


def test_multiple_oibs_partial():
    ans = f"{VALID} i {VALID2}"
    assert g.unverified_oibs(ans, f"vidio sam {VALID2}") == [VALID]


def test_append_caution():
    out = g.append_evidence_caution("odgovor", [VALID])
    assert VALID in out and "provjerite" in out.lower()
    assert g.append_evidence_caution("čist", []) == "čist"


def test_loop_key_stable_and_distinct():
    assert g.loop_key("t", {"a": 1, "b": 2}) == g.loop_key("t", {"b": 2, "a": 1})
    assert g.loop_key("t", {"a": 1}) != g.loop_key("t", {"a": 2})


def test_unverified_accepts_set_and_string():
    assert g.unverified_oibs(f"OIB {VALID}", {VALID}) == []          # set: viđen
    assert g.unverified_oibs(f"OIB {VALID}", set()) == [VALID]       # set: nije viđen
    assert g.unverified_oibs(f"OIB {VALID}", f"tekst {VALID}") == []  # string back-compat


def test_observed_oibs_extracts():
    assert g.observed_oibs(f"a {VALID} b {VALID2}") == {VALID, VALID2}


def test_loop_key_none_name_safe():
    assert g.loop_key(None, {"a": 1}).startswith("|")  # ne baca TypeError


def test_truncate_total_within_limit():
    out = g.truncate_structured("x" * 5000, limit=200)
    assert len(out) <= 200 and "skraćeno" in out
