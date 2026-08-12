"""Skill-health: use_count (učitavanje kroz ucitaj_vjestinu) + izvještaj o katalogu
(mrtve/duplikati/manjkave). SAMO uvid, NIKAD auto-brisanje (ljudske procedure)."""
from atlas.knowledge import skills as sk


def _mk(spine, name, desc="", steps="korak jedan i dva tri", status="active", org=1):
    sid = sk.create_skill(spine, org, name, description=desc, steps=steps, visibility="org")
    if status != "draft":
        sk.set_status(spine, sid, status)
    return sid


def test_mark_used_increments(spine):
    sid = _mk(spine, "Zatvaranje mjeseca")
    sk.mark_used(spine, sid)
    sk.mark_used(spine, sid)
    r = spine.read().execute("SELECT use_count FROM skills WHERE id=?", (sid,)).fetchone()
    assert r["use_count"] == 2


def test_health_flags_dead_dup_malformed(spine):
    used = _mk(spine, "PDV prijava", desc="mjesecna prijava pdv-a", steps="detaljni koraci ovdje")
    sk.mark_used(spine, used)                                   # živa
    _mk(spine, "Neiskoristena", desc="nikad ucitana", steps="ima korake ovdje")  # mrtva
    _mk(spine, "Prazna", desc="bez koraka", steps="x")          # manjkava (steps<10)
    # skoro-duplikat para (isti tokeni u nazivu+opisu)
    _mk(spine, "Godisnji odmor zahtjev", desc="obrada zahtjeva za godisnji odmor", steps="koraci ok tu")
    _mk(spine, "Godisnji odmor zahtjev obrada", desc="obrada zahtjeva za godisnji odmor", steps="koraci ok tu")

    h = sk.health(spine, 1)
    dead_names = {d["name"] for d in h["mrtve"]}
    assert "Neiskoristena" in dead_names
    assert "PDV prijava" not in dead_names                      # korištena nije mrtva
    assert any(m["name"] == "Prazna" for m in h["manjkave"])
    assert h["duplikati"], "skoro-duplikat mora biti flagiran"
    assert h["duplikati"][0]["slicnost"] >= 0.7


def test_health_ignores_non_active(spine):
    d = _mk(spine, "Draft skill", status="draft")
    h = sk.health(spine, 1)
    assert all(x["id"] != d for x in h["mrtve"])                # draft nije aktivan -> ne broji


def test_ucitaj_vjestinu_marks_used(spine):
    from atlas.business import acl, tenancy
    from atlas.rag import agent_tools
    from atlas.web.deps import add_user
    add_user(spine, "ana", "pw", "member")
    actor = acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine),
                      role="member", username="ana")
    sid = _mk(spine, "Blagajna", desc="dnevni izvjestaj", steps="otvori zatvori blagajnu")
    agent_tools.run_tool(spine, None, actor, "ucitaj_vjestinu", {"ime": "Blagajna"})
    r = spine.read().execute("SELECT use_count FROM skills WHERE id=?", (sid,)).fetchone()
    assert r["use_count"] == 1
