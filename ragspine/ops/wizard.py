"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
P2: Stranice 1 (preduvjeti) + 2 (operater) + 3 (model).
P3: Stranica 4 (mreža/HTTPS/servis). Stranice 5-6 stižu u P4."""
import dataclasses
import ipaddress
import os
import re
import shutil
import sys
import time
import types
from pathlib import Path

from ragspine.core.llm import LLMError, LLMUnavailable
from ragspine.ops import backup, certs, preflight, tui, winsvc, wizard_state
from ragspine.web import firstrun

_MIN_PW = 8
_BGE_M3 = "BAAI/bge-m3"
_BGE_M3_GB = 1.2   # fp16, približno (ručno kurirano)


def render_preflight(reqs, *, out=print) -> bool:
    """Ispiši preduvjete s glyph+detalj; fix samo za warn/fail. Vrati True kad nema 'fail'."""
    has_fail = False
    for r in reqs:
        g = tui.status_glyph(r["status"])
        out(f"  {g} {r['naziv']} — {r['detalj']}")
        if r["status"] in ("warn", "fail") and r.get("fix"):
            out(f"      → {r['fix']}")
        if r["status"] == "fail":
            has_fail = True
    return not has_fail


def page_preduvjeti(spine, cfg, *, input_fn=input, out=print) -> bool:
    tui.print_header("1/6  Preduvjeti", out=out)
    while True:
        reqs = preflight.requirements(cfg)
        ok = render_preflight(reqs, out=out)
        # winget auto-install je Windows-only (drugdje ga wizard ne nudi — brief).
        tried_install = False
        if os.name == "nt":
            for r in reqs:
                if r["key"] in preflight.WINGET_IDS and r["status"] in ("fail", "warn"):
                    if tui.prompt_yes_no(f"Pokušaj auto-instalaciju ({r['naziv']})?",
                                         default=False, input_fn=input_fn, out=out):
                        preflight.install_via_winget(r["key"], out=out)
                        tried_install = True
        if tried_install:
            continue   # ponovno provjeri preduvjete nakon (mogućeg) installa
        if ok:
            return True
        out("")
        out("Neki obavezni preduvjeti nedostaju (✗). Popravi ih pa ponovi.")
        if not tui.prompt_yes_no("Provjeri ponovno?", default=True, input_fn=input_fn, out=out):
            return False


def page_operater(spine, *, input_fn=input, out=print) -> bool:
    """Stranica 2: kreira prvog admina (operatera). Vrati True na uspjeh.
    Ako admin već postoji (npr. kreiran preko web /setup/owner puta, ili je
    prošli pokušaj pao između create_first_owner i set_stage), preskoči
    prompt — inače resume nema izlaza (create_first_owner uvijek baca)."""
    if not firstrun.needs_onboarding(spine):
        out("Administrator već postoji — preskačem.")
        return True
    tui.print_header("2/6  Operater (administrator)", out=out)
    while True:
        username = tui.prompt_text("Korisničko ime", input_fn=input_fn, out=out)
        if username:
            break
        out("Korisničko ime ne smije biti prazno.")
    while True:
        pw = tui.prompt_text("Lozinka (min 8)", input_fn=input_fn, out=out)
        if len(pw) < _MIN_PW:
            out(f"Lozinka mora imati barem {_MIN_PW} znakova.")
            continue
        pw2 = tui.prompt_text("Ponovi lozinku", input_fn=input_fn, out=out)
        if pw != pw2:
            out("Lozinke se ne podudaraju.")
            continue
        break
    try:
        firstrun.create_first_owner(spine, username, pw)
    except ValueError as e:
        out(f"Greška: {e}")
        return False
    out(f"Administrator '{username}' kreiran.")
    return True


def _download_embed(cfg):
    """Indirekcija radi testabilnosti (embed vuče fastembed tek pri pozivu)."""
    from ragspine.rag import embed
    return embed.download_model(cfg)


_SELF_TEST_PROMPT = "Odgovori točno: OK RAGSPINE"


def _llm_complete(spine, cfg, prompt: str):
    """Indirekcija radi testabilnosti; LLMClient ima vlastiti timeout (120 s)
    koji pokriva i cold-load većih modela."""
    from ragspine.business import model_settings
    from ragspine.core.llm import LLMClient
    return LLMClient(model_settings.apply(spine, cfg)).complete(
        [{"role": "user", "content": prompt}], max_tokens=20)


def self_test(spine, cfg, *, input_fn=input, out=print, retries: int = 3) -> bool:
    """Kratki test odabranog modela. Uspjeh = ne-prazan odgovor unutar timeouta.
    Regex "OK RAGSPINE" = soft-check (upozorenje). Kvar ne ruši setup."""
    for attempt in range(1, retries + 1):
        out(f"Self-test modela (pokušaj {attempt}/{retries}; prvi odziv zna trajati i minutu)...")
        t0 = time.monotonic()
        try:
            res = _llm_complete(spine, cfg, _SELF_TEST_PROMPT)
        except Exception as e:
            out(f"  ✗ {e}")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokušaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            out("  ✗ prazan odgovor")
            if attempt < retries and tui.prompt_yes_no(
                    "Pokušaj ponovno?", default=True, input_fn=input_fn, out=out):
                continue
            return False
        elapsed = time.monotonic() - t0
        out(f"  ✓ model odgovara ({elapsed:.1f} s)")
        if not re.search(r"OK RAGSPINE", text, re.IGNORECASE):
            out("  ⚠ upozorenje: odgovor ne sadrži 'OK RAGSPINE' — model radi, ali slabo slijedi upute.")
        return True
    return False


def choose_embed_model(state: dict, default_model: str) -> str:
    """bge-m3 kad KOMOTNO stane u ukupni RAM; inače ostavi default (mali)."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits":
        return _BGE_M3
    return default_model


def setup_embedding(spine, cfg, *, out=print) -> str | None:
    """Odaberi embedding po RAM-u, skini i VERIFICIRAJ; na grešku fallback na
    cfg.embed_model. Vrati ime verificiranog modela ili None (ne blokira setup)."""
    chosen = choose_embed_model(preflight.system_state(cfg), cfg.embed_model)
    for candidate in dict.fromkeys([chosen, cfg.embed_model]):   # bez duplikata
        out(f"Embedding model: {candidate} — skidam i provjeravam...")
        res = _download_embed(dataclasses.replace(cfg, embed_model=candidate))
        if res.get("ok"):
            out(f"  ✓ {candidate} (dim {res.get('dim')})")
            return candidate
        out(f"  ⚠ {candidate}: {res.get('error', 'nepoznata greška')}")
    out("Embedding nije skinut — RAG indeksiranje neće raditi dok se ne skine u Postavkama.")
    return None


_PILL_GLYPH = {"Good": "🟢", "Marginal": "🟡", "Too Tight": "🔴"}


def render_llmfit_models(rows, *, out=print) -> list[str]:
    """Ispiši llmfit retke (već filtrirane i sortirane po score-u); vrati
    ollama imena u prikazanom redoslijedu. Prvi = ⭐ preporuka."""
    names = []
    for i, r in enumerate(rows):
        pill = _PILL_GLYPH.get(r["fit_label"], "?")
        star = "  ⭐ PREPORUKA" if i == 0 else ""
        out(f"  {pill} {r['ollama_name']} ({r['params']}, {r['best_quant']} "
            f"~{r['memory_gb']:.1f} GB, ~{r['tps']:.0f} tok/s) — {r['use_case']}{star}")
        names.append(r["ollama_name"])
    return names


def page_model(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 3: Ollama spremnost -> llmfit lista -> JEDAN model -> pull -> spremi
    -> embedding -> self-test. Skip-grana vraća True (spec: ne zaglavi)."""
    tui.print_header("3/6  Model (LLM)", out=out)
    url = getattr(cfg, "ollama_url", "http://127.0.0.1:11434")

    ok, detail = preflight.ollama_ready(url)
    if not ok:
        out(f"Ollama: {detail} — pokušavam pokrenuti servis...")
        ok = preflight.start_ollama(url=url)
    if not ok:
        out("Ollama nije dostupna. Model možeš postaviti kasnije u Postavkama.")
        if tui.prompt_yes_no("Preskoči stranicu modela?", default=True,
                             input_fn=input_fn, out=out):
            return True
        return False

    ver = preflight.ollama_version(url)
    if not preflight.ollama_floor_ok(ver):
        out(f"⚠ Ollama verzija {ver or 'nepoznata'} < {preflight._OLLAMA_FLOOR} — preporučen upgrade "
            "(winget upgrade Ollama.Ollama). Nastavljam.")

    rows = preflight.llmfit_models(cfg)
    if not rows and not shutil.which("llmfit") and tui.prompt_yes_no(
            "llmfit nije pronađen (preporuke modela po hardveru). Pokušaj auto-instalaciju (pip)?",
            default=False, input_fn=input_fn, out=out):
        preflight.install_llmfit(out=out)   # pip je cross-platform — bez os.name provjere
        rows = preflight.llmfit_models(cfg)
    if not rows:
        out("llmfit nije dostupan ili nema modela koji stanu — model postavi kasnije u Postavkama.")
        return True
    out("Modeli za ovaj hardver (llmfit — kvantizacija izračunata po stroju):")
    names = render_llmfit_models(rows, out=out)
    choices = names + ["Preskoči — postavi kasnije"]
    idx = tui.prompt_choice("Odaberi JEDAN model:", choices, default=0,
                            input_fn=input_fn, out=out)
    if idx == len(names):
        return True
    model = names[idx]

    out(f"Skidam {model} (prekid je siguran — nastavlja gdje je stalo)...")
    if not preflight.ollama_pull(model, url, out=out):
        out("Model nije skinut. Pokreni setup ponovno ili postavi kasnije u Postavkama.")
        return tui.prompt_yes_no("Nastavi setup bez modela?", default=True,
                                 input_fn=input_fn, out=out)

    emb = setup_embedding(spine, cfg, out=out)
    from ragspine.business import model_settings
    model_settings.save(spine, "ollama", model=model, ollama_url=url,
                        embed_model=emb or "", user="setup")
    if not self_test(spine, cfg, input_fn=input_fn, out=out):
        out("⚠ Self-test nije prošao — model je spremljen, provjeri ga kasnije u Postavkama.")
    return True


def page_mreza(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 4: bind IP + port, cert/HTTPS, proxy, servis (preskočivo)."""
    tui.print_header("4/6  Mreža + HTTPS + servis", out=out)
    lan = preflight.local_ip()

    # 1) bind IP
    choices = [f"{lan} (detektirani LAN IP)", "0.0.0.0 (sve mreže)",
               "127.0.0.1 (samo ovo računalo)", "Ručni unos"]
    idx = tui.prompt_choice("Na kojoj adresi server sluša?", choices,
                            default=0, input_fn=input_fn, out=out)
    if idx == 0:
        bind = lan
    elif idx == 1:
        bind = "0.0.0.0"
    elif idx == 2:
        bind = "127.0.0.1"
    else:
        while True:
            bind = tui.prompt_text("IP adresa", input_fn=input_fn, out=out)
            try:
                ipaddress.ip_address(bind)
                break
            except ValueError:
                out("Neispravna IP adresa.")

    # 2) port
    while True:
        raw = tui.prompt_text("Port", default="8443", input_fn=input_fn, out=out)
        try:
            port = int(raw)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            out("Port mora biti broj 1-65535.")
            continue
        if not preflight.port_free(bind if bind != "0.0.0.0" else "127.0.0.1", port):
            out(f"Port {port} je zauzet — odaberi drugi.")
            continue
        break

    # 3) statička adresa (upozorenje, ne izvršavamo netsh set)
    if preflight.system_state(cfg).get("ip_mode") == "dhcp":
        out("⚠ Računalo je na DHCP-u — adresa se može promijeniti i klijenti gube vezu.")
        out("  Postavi statičku: netsh interface ip set address (ili rezervacija na routeru).")

    # 4) proxy
    proxy = tui.prompt_text("HTTP proxy (prazno = bez proxyja)",
                            input_fn=input_fn, out=out)
    preflight.set_proxy(spine, proxy)
    if proxy:
        out(f"  Za Ollama servis postavi env: HTTPS_PROXY={proxy}")

    # 5) cert
    cert_ip = lan if bind == "0.0.0.0" else bind
    cert_dir = str(Path(getattr(cfg, "data_dir", ".")) / "certs")
    cert, key = certs.generate_self_signed(cert_dir, ips=[cert_ip],
                                           hostnames=["ragspine.local"])
    out(f"HTTPS certifikat: {cert}")
    out(f"  SHA256: {certs.fingerprint_sha256(cert)}")
    out("  Na klijente ga instaliraj naredbom: ragspine trust")

    # 6) spremi net postavke
    spine.set_override("net", "host", bind)
    spine.set_override("net", "port", str(port))
    spine.set_override("net", "cert_path", cert)
    spine.set_override("net", "key_path", key)
    out(f"✓ Server će služiti na https://{cert_ip}:{port}")

    # 7) servis (preskočivo; neuspjeh ne ruši stranicu)
    if tui.prompt_yes_no("Instaliraj kao servis (autostart)?", default=False,
                         input_fn=input_fn, out=out):
        exe = shutil.which("ragspine") or f"{sys.executable} -m ragspine"
        if not winsvc.install_service(exe, getattr(cfg, "data_dir", "."), port, out=out):
            out("⚠ Servis nije instaliran — možeš ponoviti kasnije (admin konzola).")
    return True


# Stranica 5 (spec): uloga (folders.role string) → naziv za prompt.
_MAPE_ULOGE = [
    ("klijenti", "Klijenti"),
    ("propisi", "Propisi"),
    ("skener", "Zajednički skenovi"),
    ("program", "Knjigovodstveni program"),
]


def _net_use_hint(unc: str) -> str:
    """`net use` komanda za SMB prijavu; `*` = net.exe sam pita lozinku (ne
    završi u argv/process-listi), /persistent:no = bez naslijeđenih mapiranja.
    ponytail: DPAPI spremanje SMB kredencijala = backlog; do tada admin
    komandu pokreće ručno, wizard lozinke ne dira."""
    share = "\\".join(unc.split("\\")[:4])   # \\server\share (bez podmapa)
    return f"net use {share} /user:DOMENA\\korisnik * /persistent:no"


def page_mape(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 5: mrežne mape po ulogama (UNC putanje), preskočivo.
    Test pristupa ide kao trenutni korisnik (servisni račun ne postoji —
    wrapper backlog, v. winsvc); registracija ide u istu folders tablicu
    koju koristi web Postavke → Mrežne mape."""
    from ragspine.business import folders
    tui.print_header("5/6  Mape / mrežni pogoni", out=out)
    if not tui.prompt_yes_no("Poveži mrežne mape sada? (kasnije: Postavke → Mrežne mape)",
                             default=True, input_fn=input_fn, out=out):
        return True
    registered = []
    for role, naziv in _MAPE_ULOGE:
        while True:
            path = tui.prompt_text(
                f"{naziv} — UNC putanja (\\\\server\\share\\...; prazno = preskoči)",
                input_fn=input_fn, out=out)
            if not path:
                break
            if re.match(r"^[A-Za-z]:", path):
                out("  ⚠ Slovo pogona (npr. Z:) servisni račun ne vidi — koristi UNC putanju.")
            if not os.path.isdir(path):
                out(f"  ✗ Nedostupno: {path}")
                out(f"    Ako share traži prijavu, u drugom prozoru pokreni: {_net_use_hint(path)}")
                if tui.prompt_yes_no("  Pokušaj ponovno?", default=True,
                                     input_fn=input_fn, out=out):
                    continue
                break
            root = os.path.realpath(path)
            try:
                folders.register(spine, types.SimpleNamespace(mount_roots=[root]),
                                 path, role, label=naziv, user="setup")
            except ValueError as e:
                out(f"  ✗ {e}")
                break
            out(f"  ✓ {naziv}: {path}")
            registered.append(root)
            break
    if registered:
        out("")
        out("Da web sučelje (i budući servis) vidi ove mape, postavi env varijablu:")
        out(f"  RAGSPINE_MOUNT_ROOTS={','.join(registered)}")
    return True


def render_summary(spine, cfg, *, out=print) -> None:
    """Sažetak stranica 1-5: konfigurirano / preskočeno + gdje dodati kasnije."""
    from ragspine.business import folders
    admin = "✓ kreiran" if not firstrun.needs_onboarding(spine) else "✗ nije kreiran"
    model = spine.get_override("model", "model") or ""
    emb = spine.get_override("model", "embed_model") or ""
    host = spine.get_override("net", "host") or ""
    port = spine.get_override("net", "port") or ""
    cert = spine.get_override("net", "cert_path") or ""
    out("Sažetak:")
    out(f"  Administrator: {admin}")
    out(f"  LLM model: {model or '— preskočeno (Postavke → Model)'}")
    out(f"  Embedding: {emb or '— preskočeno (Postavke → Model)'}")
    out(f"  Mreža: {f'https://{host}:{port}' if host else '— zadano (Postavke → Mreža)'}")
    out(f"  HTTPS cert: {cert or '— nema (ragspine setup, stranica 4)'}")
    mape = folders.list_folders(spine)
    if mape:
        for m in mape:
            out(f"  Mapa [{m['role']}]: {m['path']}")
    else:
        out("  Mape: — preskočeno (Postavke → Mrežne mape)")


def page_gotovo(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 6: sažetak + konkretan backup/restore. Uvijek vraća True —
    zadnja stranica ne smije blokirati dovršetak setupa."""
    tui.print_header("6/6  Gotovo — sažetak i sigurnosne kopije", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    out("Sigurnosne kopije (spremi na vanjski medij/NAS — bez ovoga restore ne vraća sustav):")
    out(f"  • Baza: {cfg.db_path} — snapshot naredbom: ragspine backup")
    out(f"  • Tajni ključ (JWT): {Path(cfg.data_dir) / 'secret'} — bez njega prijave "
        "nakon restorea ne rade; kopiraj ga UZ bazu")
    out(r"  • Ollama modeli: %USERPROFILE%\.ollama\models — bez njih RAG ne radi "
        "ni uz vraćenu bazu i ključ")
    out("  • Restore provjeri na drugom stroju: ragspine restore <ime> (server zaustavljen)")
    if tui.prompt_yes_no("Napravi verificirani snapshot baze sada?", default=True,
                         input_fn=input_fn, out=out):
        try:
            res = backup.create_backup(cfg)
            out(f"  ✓ {res['path']} ({res['size']} B, verificiran quick_checkom)")
        except Exception as e:
            out(f"  ⚠ Snapshot nije uspio: {e} — pokreni `ragspine backup` ručno.")
    return True


def run(spine, cfg, *, input_fn=input, out=print) -> None:
    if wizard_state.is_complete(spine):
        out("Setup je već dovršen. Za ponovno: `ragspine setup --reset`.")
        return
    stage = wizard_state.get_stage(spine)
    out(f"RAGSPINE setup (nastavak od koraka {stage + 1}).")
    try:
        if stage < 1:
            if not page_preduvjeti(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na preduvjetima. Pokreni ponovno kad popraviš.")
                return
            wizard_state.set_stage(spine, 1)
        if stage < 2:
            if not page_operater(spine, input_fn=input_fn, out=out):
                out("Setup prekinut na operateru.")
                return
            wizard_state.set_stage(spine, 2)
        if stage < 3:
            if not page_model(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na modelu. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 3)
        if stage < 4:
            if not page_mreza(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na mreži. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 4)
        if stage < 5:
            if not page_mape(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na mapama. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 5)
        if stage < 6:
            if not page_gotovo(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na sažetku. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 6)
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (npr. servis bez terminala) — bez tracebacka.
        # ponytail: run() ostaje `-> None`; pozivatelj (_cmd_setup) ne detektira
        # ovaj slučaj posebno pa CLI izlazi s 0. Upgrade path: vratiti bool/kod
        # ako se pokaže da nešto ovisi o exit statusu.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `ragspine setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    wizard_state.mark_complete(spine)
    out("✓ Setup dovršen (6/6) — web sučelje je spremno.")
