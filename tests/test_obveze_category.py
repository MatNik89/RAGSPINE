"""JOPPD -> Plaće + `category` marker na vrsti obveze (pravi stupac, način A)."""
from atlas.business import obveze


def test_new_install_joppd_is_place(spine):
    t = obveze.get_type(spine, "JOPPD")
    assert t["label"] == "Plaće" and t["category"] == "place"


def test_other_types_have_empty_category(spine):
    assert obveze.get_type(spine, "PDV")["category"] == ""


def test_upsert_type_persists_category(spine):
    obveze.upsert_type(spine, "NAJAM", "Najam", "monthly:10", "monthly", "manual",
                       category="najam", user="a")
    assert obveze.get_type(spine, "NAJAM")["category"] == "najam"


def test_list_types_includes_category(spine):
    types = {t["kind"]: t for t in obveze.list_types(spine)}
    assert types["JOPPD"]["category"] == "place"


def test_upsert_without_category_preserves_existing(spine):
    # Codex: upsert bez category NE smije obrisati postojeću (JOPPD 'place')
    obveze.upsert_type(spine, "JOPPD", "Plaće", "monthly:15", "monthly", "employees")
    assert obveze.get_type(spine, "JOPPD")["category"] == "place"
    # eksplicitan prazan string ipak briše
    obveze.upsert_type(spine, "JOPPD", "Plaće", "monthly:15", "monthly", "employees", category="")
    assert obveze.get_type(spine, "JOPPD")["category"] == ""


def test_upsert_rejects_invalid_rule(spine):
    import pytest
    for bad in [("monthly", "monthly:garbage"), ("yearly", "yearly:99-99"),
                ("quarterly", "quarterly:-1"), ("monthly", "monthly:40")]:
        with pytest.raises(ValueError):
            obveze.upsert_type(spine, "X", "X", bad[1], bad[0], "all_active")
    # valjana prolaze
    obveze.upsert_type(spine, "Y", "Y", "yearly:02-29", "yearly", "all_active")
    assert obveze.get_type(spine, "Y")["rule"] == "yearly:02-29"


def test_existing_install_migrated(spine):
    # simuliraj staru bazu: JOPPD upisan s praznom kategorijom i starim labelom
    with spine.write() as c:
        c.execute("INSERT OR REPLACE INTO obligation_types(kind,label,rule,frequency,"
                  "applies_to,active,sort,category) VALUES('JOPPD','JOPPD','monthly:15',"
                  "'monthly','employees',1,20,'')")
    from atlas.core.spine import Spine
    Spine(spine.db_path)  # ponovni init pokreće migraciju
    t = obveze.get_type(spine, "JOPPD")
    assert t["category"] == "place" and t["label"] == "Plaće"
