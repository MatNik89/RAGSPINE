import time, pytest
from ragspine.core import security as sec

def test_jwt_roundtrip():
    t = sec.jwt_encode({"sub": "ana"}, "s3cret")
    assert sec.jwt_decode(t, "s3cret")["sub"] == "ana"

def test_jwt_bad_sig():
    t = sec.jwt_encode({"sub": "ana"}, "s3cret")
    with pytest.raises(sec.AuthError): sec.jwt_decode(t, "drugi")

def test_jwt_expired():
    t = sec.jwt_encode({"sub": "ana"}, "s", ttl_s=-1)
    with pytest.raises(sec.AuthError): sec.jwt_decode(t, "s")

def test_password():
    h = sec.hash_password("lozinka1")
    assert sec.verify_password("lozinka1", h) and not sec.verify_password("x", h)

def test_password_malformed_stored():
    assert not sec.verify_password("x", "not-a-valid-format")
    assert not sec.verify_password("x", "")

def test_oib():
    assert sec.oib_valid("69435151530")      # validan testni OIB
    assert not sec.oib_valid("69435151531")
    assert not sec.oib_valid("123")

def test_redact():
    s = sec.redact_pii("OIB 69435151530, mail ana@x.hr, IBAN HR1210010051863000160, tel +385 91 123 4567")
    for tok in ["[OIB]", "[EMAIL]", "[IBAN]", "[TEL]"]: assert tok in s
    assert "69435151530" not in s

def test_chain(spine):
    sec.chain_append(spine, "e1"); sec.chain_append(spine, "e2")
    assert sec.chain_verify(spine)
    with spine.write() as c: c.execute("UPDATE hash_chain SET event='tamper' WHERE id=1")
    assert not sec.chain_verify(spine)
