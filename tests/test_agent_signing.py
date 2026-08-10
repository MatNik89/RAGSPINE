"""C: potpisane naredbe — PER-UREĐAJ HMAC (veže device_id) + anti-replay.
Server potpiše, agent provjeri prije izvršenja; lažan/replay/tuđi potpis se odbija."""
from atlas.agent import atlas_agent as aa
from atlas.business import fleet


class FakeTransport:
    def __init__(self, command):
        self._command = command
        self.reported = []

    def poll(self):
        return self._command

    def report(self, cmd_id, ok, detail):
        self.reported.append({"id": cmd_id, "ok": ok, "detail": detail})


class FakeExecutor:
    def __init__(self):
        self.ran = []

    def run_program(self, argv): self.ran.append(argv)
    def shutdown(self): self.ran.append("shutdown")
    def enable_wol(self): pass
    def status(self): return "ok"


class MemSeq:
    def __init__(self): self._v = 0
    def last(self): return self._v
    def set(self, v): self._v = v; return True


class BadSeq:
    """set() ne uspije (npr. read-only disk) -> naredba se NE smije izvršiti."""
    def last(self): return 0
    def set(self, v): return False


# --- server strana --------------------------------------------------------

def test_master_key_atomic_and_stable(spine, cfg):
    k1 = fleet.device_sign_key(spine, 1, cfg)
    assert k1 and fleet.device_sign_key(spine, 1, cfg) == k1


def test_per_device_keys_differ(spine, cfg):
    assert fleet.device_sign_key(spine, 1, cfg) != fleet.device_sign_key(spine, 2, cfg)


def test_master_key_fail_closed_on_bad_decrypt(spine, cfg):
    import pytest
    fleet.device_sign_key(spine, 1, cfg)  # stvori šifrirani master
    from atlas.business import secretbox
    bad = secretbox.encrypt("x", type("C", (), {"jwt_secret": "DRUGI-SECRET"})())  # drugi ključ
    spine.set_override("fleet", "signing_key", bad)
    with pytest.raises(RuntimeError):  # ne potpisuj praznim ključem
        fleet.device_sign_key(spine, 1, cfg)


def test_master_key_encrypted_at_rest(spine, cfg):
    # ukradeni DB backup ne smije otkriti master ključ za potpis (Codex fold)
    fleet.device_sign_key(spine, 1, cfg)  # okine stvaranje
    raw = spine.get_override("fleet", "signing_key", "")
    assert raw.startswith("enc:")  # šifriran u bazi
    from atlas.business import secretbox
    assert secretbox.decrypt(raw, cfg)  # ispravnim ključem se dešifrira


def test_sign_verify_roundtrip_device_bound(spine, cfg):
    cmd = {"id": 5, "action": "run_program", "program_key": "preglednik"}
    cmd["sig"] = fleet.sign_command(spine, 1, cmd, cfg)
    key1 = fleet.device_sign_key(spine, 1, cfg)
    assert aa._valid_signature(key1, 1, cmd) is True
    # isti potpis za DRUGI uređaj ne valja (device binding)
    key2 = fleet.device_sign_key(spine, 2, cfg)
    assert aa._valid_signature(key2, 2, cmd) is False
    # tampering program_keya poništi potpis
    assert aa._valid_signature(key1, 1, dict(cmd, program_key="x")) is False


def _cfg(sign_key="", token="1.tajna"):
    return aa.AgentConfig(server_url="https://s.lan:8443", token=token,
                          program_map={"preglednik": ["firefox"]}, sign_key=sign_key)


# --- agent strana ---------------------------------------------------------

def test_agent_executes_valid_signature(spine, cfg):
    cmd = {"id": 5, "action": "run_program", "program_key": "preglednik"}
    cmd["sig"] = fleet.sign_command(spine, 1, cmd, cfg)
    ex = FakeExecutor()
    aa.run_once(_cfg(sign_key=fleet.device_sign_key(spine, 1, cfg), token="1.x"),
                FakeTransport(cmd), ex, seq=MemSeq())
    assert ex.ran == [["firefox"]]


def test_agent_refuses_bad_signature(spine, cfg):
    cmd = {"id": 6, "action": "run_program", "program_key": "preglednik", "sig": "krivo"}
    ex = FakeExecutor(); tr = FakeTransport(cmd)
    aa.run_once(_cfg(sign_key=fleet.device_sign_key(spine, 1, cfg), token="1.x"), tr, ex, seq=MemSeq())
    assert ex.ran == [] and tr.reported[0]["ok"] is False and "potpis" in tr.reported[0]["detail"].lower()


def test_agent_refuses_replay(spine, cfg):
    cmd = {"id": 5, "action": "run_program", "program_key": "preglednik"}
    cmd["sig"] = fleet.sign_command(spine, 1, cmd, cfg)
    key = fleet.device_sign_key(spine, 1, cfg)
    seq = MemSeq()
    ex1 = FakeExecutor()
    aa.run_once(_cfg(sign_key=key, token="1.x"), FakeTransport(cmd), ex1, seq=seq)
    assert ex1.ran == [["firefox"]]  # prvi put prolazi
    ex2 = FakeExecutor(); tr2 = FakeTransport(cmd)  # isti (replay)
    aa.run_once(_cfg(sign_key=key, token="1.x"), tr2, ex2, seq=seq)
    assert ex2.ran == [] and "replay" in tr2.reported[0]["detail"].lower()


def test_agent_fail_closed_when_seq_write_fails(spine, cfg):
    # Codex: ako trajni anti-replay zapis ne uspije, naredba se NE izvršava
    cmd = {"id": 5, "action": "run_program", "program_key": "preglednik"}
    cmd["sig"] = fleet.sign_command(spine, 1, cmd, cfg)
    ex = FakeExecutor(); tr = FakeTransport(cmd)
    aa.run_once(_cfg(sign_key=fleet.device_sign_key(spine, 1, cfg), token="1.x"), tr, ex, seq=BadSeq())
    assert ex.ran == [] and "zapis" in tr.reported[0]["detail"].lower()


def test_seqfile_atomic_and_fail_closed_on_corruption(tmp_path):
    p = str(tmp_path / "a.seq")
    s = aa._SeqFile(p)
    assert s.last() == 0  # nema datoteke = svjež
    assert s.set(7) is True and s.last() == 7
    import pathlib
    pathlib.Path(p).write_text("nije-broj", encoding="utf-8")  # korupcija (ne-numericki)
    import pytest
    with pytest.raises(ValueError):
        s.last()  # nečitljivo -> raise (pozivatelj fail-closed)


def test_no_key_backcompat(spine):
    cmd = {"id": 8, "action": "run_program", "program_key": "preglednik"}  # bez sig
    ex = FakeExecutor()
    aa.run_once(_cfg(sign_key=""), FakeTransport(cmd), ex)
    assert ex.ran == [["firefox"]]
