import pytest

from ragspine.business import acl, tenancy
from ragspine.business.acl import Actor, Asset


def _asset(org=1, owner=10, vis="private", team=None, at="doc", aid=1):
    return Asset(asset_type=at, asset_id=aid, org_id=org, owner_user_id=owner,
                 visibility=vis, team_id=team)


# ---------- pure check() ----------

def test_tenant_isolation_hard_deny():
    a = Actor(user_id=10, org_id=2, role="owner")  # owner ali DRUGE org
    assert acl.check(a, _asset(org=1, owner=10), "read") is False
    assert acl.check(a, _asset(org=1, owner=10), "read", acl_rows=[
        {"subject_type": "user", "subject_id": "10", "permission": "read"}]) is False


def test_owner_always_allowed():
    a = Actor(user_id=10, org_id=1, role="viewer")
    for act in acl.ACTIONS:
        assert acl.check(a, _asset(org=1, owner=10, vis="private"), act) is True


def test_admin_scope_in_org():
    a = Actor(user_id=99, org_id=1, role="admin")
    assert acl.check(a, _asset(owner=10, vis="private"), "delete") is True
    b = Actor(user_id=99, org_id=1, role="owner")
    assert acl.check(b, _asset(owner=10, vis="private"), "manage") is True


def test_member_read_by_visibility():
    m = Actor(user_id=5, org_id=1, role="member", team_ids={7})
    assert acl.check(m, _asset(owner=10, vis="org"), "read") is True
    assert acl.check(m, _asset(owner=10, vis="team", team=7), "read") is True
    assert acl.check(m, _asset(owner=10, vis="team", team=8), "read") is False  # drugi tim
    assert acl.check(m, _asset(owner=10, vis="private"), "read") is False
    assert acl.check(m, _asset(owner=10, vis="restricted"), "read") is False
    # čitanje org-vidljivog ne daje write
    assert acl.check(m, _asset(owner=10, vis="org"), "write") is False


def test_acl_grant_enables_write():
    m = Actor(user_id=5, org_id=1, role="member", team_ids={7})
    rows_user = [{"subject_type": "user", "subject_id": "5", "permission": "write"}]
    assert acl.check(m, _asset(owner=10, vis="restricted"), "write", rows_user) is True
    rows_team = [{"subject_type": "team", "subject_id": "7", "permission": "read"}]
    assert acl.check(m, _asset(owner=10, vis="restricted"), "read", rows_team) is True
    rows_role = [{"subject_type": "role", "subject_id": "member", "permission": "manage"}]
    # manage pokriva sve akcije
    assert acl.check(m, _asset(owner=10, vis="restricted"), "delete", rows_role) is True


# ---------- can() fast path + DB ----------

def test_can_fast_path_skips_acl(spine, monkeypatch):
    calls = {"n": 0}
    real = acl.load_acl
    monkeypatch.setattr(acl, "load_acl", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), real(*a, **k))[1])
    owner = Actor(user_id=10, org_id=1, role="member")
    # owner read → fast path true, ACL se NE čita
    assert acl.can(spine, owner, _asset(owner=10, vis="private"), "read") is True
    assert calls["n"] == 0


def test_can_lazy_loads_acl_for_restricted(spine):
    acl.grant(spine, "doc", 1, "user", 5, "read")
    m = Actor(user_id=5, org_id=1, role="member")
    assert acl.can(spine, m, _asset(owner=10, vis="restricted", aid=1), "read") is True
    other = Actor(user_id=6, org_id=1, role="member")
    assert acl.can(spine, other, _asset(owner=10, vis="restricted", aid=1), "read") is False


def test_grant_revoke_roundtrip(spine):
    acl.grant(spine, "doc", 2, "user", 5, "write")
    assert len(acl.load_acl(spine, "doc", 2)) == 1
    acl.revoke(spine, "doc", 2, "user", 5)
    assert acl.load_acl(spine, "doc", 2) == []


def test_grant_validates(spine):
    with pytest.raises(ValueError):
        acl.grant(spine, "doc", 1, "bogus", 5, "read")
    with pytest.raises(ValueError):
        acl.grant(spine, "doc", 1, "user", 5, "bogus")


# ---------- tenancy ----------

def test_create_org_makes_owner(spine):
    with spine.write() as c:
        uid = c.execute("INSERT INTO users(username, pw_hash) VALUES('ana','x')").lastrowid
    org = tenancy.create_org(spine, "Ured Alfa", uid)
    assert tenancy.role_of(spine, org, uid) == "owner"


def test_add_member_and_actor(spine):
    with spine.write() as c:
        owner = c.execute("INSERT INTO users(username, pw_hash) VALUES('o','x')").lastrowid
        u = c.execute("INSERT INTO users(username, pw_hash) VALUES('m','x')").lastrowid
    org = tenancy.create_org(spine, "Org", owner)
    tenancy.add_member(spine, org, u, "member")
    team = tenancy.create_team(spine, org, "Knjigovodstvo")
    tenancy.add_to_team(spine, team, u)
    actor = tenancy.actor_for(spine, org, u)
    assert actor.role == "member" and team in actor.team_ids
    assert tenancy.actor_for(spine, org, 99999) is None  # nije član


def test_add_member_bad_role(spine):
    with pytest.raises(ValueError):
        tenancy.add_member(spine, 1, 1, "kralj")


def test_cross_org_isolation_via_can(spine):
    # sredstvo u org 1; korisnik član org 2 → nikad pristup, čak ni s ACL grantom
    acl.grant(spine, "doc", 9, "user", 5, "read")
    outsider = Actor(user_id=5, org_id=2, role="admin")
    assert acl.can(spine, outsider, _asset(org=1, owner=10, vis="org", aid=9), "read") is False
