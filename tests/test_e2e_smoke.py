"""END-TO-END smoke: boota cijeli ATLAS (create_app) i prolazi PRAVE tokove kroz
HTTP (TestClient) + cross-feature lanac chat->confirm->audit->replay. Dokazuje da
sustav radi KAO CJELINA, ne samo dijelovi u izolaciji. Stub LLM na stvarnom
injection-pointu (model_settings.build_llm); bez mreže/embed-modela (deterministički)."""
from fastapi.testclient import TestClient

from atlas.core.llm import LLMResult
from atlas.web.api import create_app
from atlas.web.deps import add_user

from .conftest import complete_setup


class _StubLLM:
    """Skriptirani LLM: vrati zadane rezultate redom, pa prazan odgovor."""
    def __init__(self, script=None):
        self.script = list(script or [])

    def complete(self, messages, system=None, tools=None, **k):
        if self.script:
            return self.script.pop(0)
        return LLMResult(text="Gotovo.", model="stub", usage={"total_tokens": 5}, tool_calls=[])


def _write_call(tekst):
    return LLMResult(text="Radim.", model="stub", usage={"total_tokens": 5},
                     tool_calls=[{"name": "zapisi_belesku",
                                  "args": {"klijent": "Pekara Test", "tekst": tekst}}])


def _h(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_e2e_core_flows(spine, cfg, monkeypatch):
    complete_setup(spine)
    stub = _StubLLM()
    monkeypatch.setattr("atlas.business.model_settings.build_llm", lambda *a, **k: stub)
    app = create_app(spine, cfg)
    c = TestClient(app)

    # boot: rute + tablice postoje
    assert len([r for r in app.routes if getattr(r, "path", "")]) > 100
    assert spine.read().execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table'").fetchone()["n"] > 50

    # auth: prvi login = owner
    add_user(spine, "ana", "pw", "owner")
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    assert c.get("/org", headers=_h(tok)).status_code == 200

    # klijent CRUD
    r = c.post("/clients", json={"name": "Pekara Test", "oib": "12345678903"}, headers=_h(tok))
    assert r.status_code == 200, r.text
    assert spine.read().execute("SELECT count(*) AS n FROM clients").fetchone()["n"] == 1

    # PUNI AGENT-LANAC: chat propose -> pending token -> confirm IZVRŠI
    stub.script = [_write_call("e2e bilješka")]
    r = c.post("/chat", json={"q": "Zapiši bilješku Pekari"}, headers=_h(tok))
    assert r.status_code == 200, r.text
    pend = r.json().get("pending")
    assert pend and pend.get("token"), f"nema pending tokena: {r.json()}"
    assert c.post("/chat/potvrdi", json={"token": pend["token"]}, headers=_h(tok)).status_code == 200

    # CROSS-FEATURE: izvršena radnja je audit-irana -> replay ju vidi
    rr = c.get("/replay", headers=_h(tok))
    assert rr.status_code == 200
    assert len(rr.json().get("radnje", [])) >= 1, "confirmana radnja mora biti ponovljiva"

    # autonomija: nenadzirani run PARKIRA write za odobrenje
    from atlas.business import acl, tenancy
    from atlas.rag import agent
    actor = acl.Actor(user_id=1, org_id=tenancy.default_org_id(spine), role="owner", username="ana")
    out = agent.run_unattended(spine, cfg, "pripremi", actor, _StubLLM([_write_call("auto")]),
                               source="e2e")
    assert len(out.get("parkirano", [])) == 1 and out.get("izvrseno") == []
    assert c.get("/parkirano", headers=_h(tok)).status_code == 200

    # ostale žive rute vraćaju 200 s ispravnim oblikom
    assert set(c.get("/budget", headers=_h(tok)).json()["budget"]) == {"llm", "tokens", "writes"}
    assert c.get("/grants", headers=_h(tok)).status_code == 200
    assert set(c.get("/skills/health", headers=_h(tok)).json()) == {
        "aktivnih", "mrtve", "duplikati", "manjkave"}

    # scheduler tick ne baca (nema zadataka -> 0)
    from atlas.business import scheduler_tasks
    assert scheduler_tasks.run_due(spine, cfg) == []
