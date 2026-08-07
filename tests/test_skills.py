import pytest

from atlas.knowledge import skills


def test_create_and_get(spine):
    sid = skills.create_skill(spine, 1, "Obračun plaće", description="kako se radi plaća",
                              trigger="plaća, bruto, neto", steps="1. ...", validation="provjeri JOPPD")
    s = skills.get_skill(spine, sid)
    assert s["name"] == "Obračun plaće" and s["version"] == 1 and s["status"] == "draft"
    assert s["steps"] == "1. ..." and s["validation"] == "provjeri JOPPD"


def test_create_requires_name(spine):
    with pytest.raises(ValueError):
        skills.create_skill(spine, 1, "  ")


def test_update_bumps_version(spine):
    sid = skills.create_skill(spine, 1, "X", trigger="a")
    skills.update_skill(spine, sid, steps="novi koraci", trigger="a b")
    s = skills.get_skill(spine, sid)
    assert s["version"] == 2 and s["steps"] == "novi koraci"


def test_status_lifecycle(spine):
    sid = skills.create_skill(spine, 1, "X")
    skills.set_status(spine, sid, "active")
    assert skills.get_skill(spine, sid)["status"] == "active"
    with pytest.raises(ValueError):
        skills.set_status(spine, sid, "bogus")


def test_list_org_scoped_and_by_status(spine):
    skills.create_skill(spine, 1, "A")
    sid = skills.create_skill(spine, 1, "B"); skills.set_status(spine, sid, "active")
    skills.create_skill(spine, 2, "DrugaOrg")  # druga org
    assert {s["name"] for s in skills.list_skills(spine, 1)} == {"A", "B"}
    assert [s["name"] for s in skills.list_skills(spine, 1, status="active")] == ["B"]


def test_match_active_by_trigger_overlap(spine):
    sid = skills.create_skill(spine, 1, "Obračun plaće", trigger="plaća bruto neto JOPPD")
    skills.set_status(spine, sid, "active")
    draft = skills.create_skill(spine, 1, "Nacrt", trigger="plaća")  # draft → ne matcha
    hits = skills.match(spine, 1, "kako se radi plaća za zaposlenika?")
    assert [h["name"] for h in hits] == ["Obračun plaće"]
    assert skills.match(spine, 1, "vremenska prognoza") == []
    # org izolacija
    assert skills.match(spine, 2, "plaća") == []
