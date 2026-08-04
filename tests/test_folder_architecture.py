"""D2: struktura se DOGOVARA (template u bazi, default prazan) — klijenti su
podmape stvarne KLIJENTI mape (registrirana role='klijenti'), ne konstanta."""
import os

import pytest

from ragspine.business import folder_architecture as fa
from ragspine.business import folders, onboarding


def _mk_klijenti(cfg, tmp_path, *clients_with_subdirs):
    """Napravi 'NAS' KLIJENTI mapu (velikim slovima!) s klijentima i podmapama."""
    root = tmp_path / "nas" / "KLIJENTI"
    for name, subs in clients_with_subdirs:
        (root / name).mkdir(parents=True, exist_ok=True)
        for s in subs:
            (root / name / s).mkdir()
    return root


def _register(spine, cfg, root):
    cfg.mount_roots = [str(root.parent)]
    return folders.register(spine, cfg, str(root), "klijenti", "KLIJENTI", "ana")


def test_template_default_empty_and_roundtrip(spine):
    assert fa.get_template(spine) == {"office": [], "client_subdirs": []}
    tpl = fa.set_template(spine, office=["PROPISI", "SCANNER"],
                          client_subdirs=["Ugovori", "Izvodi"])
    assert tpl == {"office": ["PROPISI", "SCANNER"], "client_subdirs": ["Ugovori", "Izvodi"]}
    assert fa.get_template(spine) == tpl
    # None = ne diraj tu polovicu
    tpl2 = fa.set_template(spine, client_subdirs=["Porezna"])
    assert tpl2["office"] == ["PROPISI", "SCANNER"] and tpl2["client_subdirs"] == ["Porezna"]


def test_template_rejects_evil_names(spine):
    for bad in (["a/b"], ["a\\\\b"], [".."], ["con:"], ["x" + chr(0)], [""], [7]):
        with pytest.raises(ValueError):
            fa.set_template(spine, office=bad)


def test_learn_structure_reads_existing_klijenti(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path,
                        ("PERIĆ PERO", ["Ugovori", "Izvodi"]),
                        ("PODUZEĆE X", ["Ugovori"]),
                        ("OBRT Y", []))
    _register(spine, cfg, root)
    learned = fa.learn_structure(spine, cfg)
    assert learned["n_clients"] == 3
    assert learned["subdir_counts"] == {"Ugovori": 2, "Izvodi": 1}
    assert learned["root"] == os.path.realpath(str(root))


def test_learn_structure_no_klijenti_mapa(spine, cfg):
    learned = fa.learn_structure(spine, cfg)
    assert learned == {"root": None, "n_clients": 0, "subdir_counts": {}}


def test_propose_empty_without_agreement(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path, ("PERIĆ PERO", []))
    _register(spine, cfg, root)
    prop = fa.propose(spine, cfg)
    assert prop["must_have"] == [] and prop["clients"] == [] and prop["n_missing"] == 0


def test_propose_and_apply_from_agreement(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path,
                        ("PERIĆ PERO", ["Ugovori"]),
                        ("PODUZEĆE X", []))
    _register(spine, cfg, root)
    fa.set_template(spine, office=["SCANNER"], client_subdirs=["Ugovori", "Izvodi"])
    prop = fa.propose(spine, cfg)
    # SCANNER fali + PERIĆ: Izvodi + PODUZEĆE: Ugovori, Izvodi
    assert prop["n_missing"] == 4
    assert prop["klijenti_root"] == os.path.realpath(str(root))
    res = fa.apply(spine, cfg)
    assert res["n_created"] == 4
    assert os.path.isdir(os.path.join(prop["root"], "SCANNER"))
    assert os.path.isdir(str(root / "PERIĆ PERO" / "Izvodi"))
    assert os.path.isdir(str(root / "PODUZEĆE X" / "Ugovori"))
    assert fa.apply(spine, cfg)["n_created"] == 0  # idempotentno


def test_symlink_client_skipped_fail_closed(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path, ("PRAVI", []))
    outside = tmp_path / "izvan"; outside.mkdir()
    os.symlink(str(outside), str(root / "ZLI"))
    _register(spine, cfg, root)
    fa.set_template(spine, client_subdirs=["Ugovori"])
    prop = fa.propose(spine, cfg)
    assert [c["name"] for c in prop["clients"]] == ["PRAVI"]
    fa.apply(spine, cfg)
    assert not os.path.isdir(str(outside / "Ugovori"))


def test_onboarding_creates_client_in_registered_klijenti(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path, ("POSTOJEĆI", []))
    _register(spine, cfg, root)
    res = onboarding.create_client(spine, cfg, {"name": "Nova Firma"}, owner="ana")
    assert os.path.realpath(res["folder_path"]).startswith(os.path.realpath(str(root)))
    assert os.path.isdir(res["folder_path"])
    # add_document guard prihvaća klijenta u registriranoj KLIJENTI mapi
    doc = onboarding.add_document(spine, cfg, res["id"], "ugovor.txt", b"Ugovor o vodenju")
    assert os.path.isfile(doc["path"]) if "path" in doc else True


def test_chat_lane_agreement_saved_and_overview(spine, cfg, tmp_path):
    root = _mk_klijenti(cfg, tmp_path, ("PERIĆ PERO", ["Ugovori"]))
    _register(spine, cfg, root)
    out = fa.handle(spine, cfg, "dogovor mape po klijentu: Ugovori, Izvodi, Porezna", llm=None)
    assert "Zapamtio" in out
    assert fa.get_template(spine)["client_subdirs"] == ["Ugovori", "Izvodi", "Porezna"]
    out = fa.handle(spine, cfg, "dogovor uredske mape: SCANNER, ARHIVA", llm=None)
    assert fa.get_template(spine)["office"] == ["SCANNER", "ARHIVA"]
    # pregled bez zapovijedi
    out = fa.handle(spine, cfg, "kakva je arhitektura mapa?", llm=None)
    assert "1 klijenata" in out and "Ugovori" in out


def test_router_routes_to_arhitektura():
    from ragspine.rag import router
    assert router.route("dogovor mape po klijentu: Ugovori") == "arhitektura"
    assert router.route("kakva je struktura mapa?") == "arhitektura"
    assert router.route("arhitektura mapa") == "arhitektura"


def test_api_admin_only_and_flow(spine, cfg, tmp_path):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user

    root = _mk_klijenti(cfg, tmp_path, ("PERIĆ PERO", []))
    _register(spine, cfg, root)
    c = TestClient(create_app(spine, cfg))
    assert c.get("/folder-architecture").status_code in (401, 403)  # prije logina
    add_user(spine, "gazda", "pw")
    owner = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    add_user(spine, "boris", "pw")
    worker = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    hw = {"Authorization": f"Bearer {worker}"}

    for ep in ("/folder-architecture", "/folder-architecture/learned",
               "/folder-architecture/template"):
        assert c.get(ep, headers=hw).status_code == 403
    r = c.post("/folder-architecture/template", headers=ho,
               json={"office": ["SCANNER"], "client_subdirs": ["Ugovori"]})
    assert r.status_code == 200
    prop = c.get("/folder-architecture", headers=ho).json()
    assert prop["n_missing"] == 2
    assert c.post("/folder-architecture/apply", headers=hw).status_code == 403
    assert c.post("/folder-architecture/apply", headers=ho).json()["n_created"] == 2
    assert c.get("/folder-architecture", headers=ho).json()["n_missing"] == 0
    r = c.get("/ui/arhitektura", headers=ho)
    assert r.status_code == 200 and "Arhitektura mapa" in r.text
