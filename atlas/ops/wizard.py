"""Terminal setup wizard. Jedan fiksni slijed, resume preko wizard_state.
5 stranica: preduvjeti, operater, model, mreža/HTTPS/servis, gotovo.
Mrežne mape se ne postavljaju ovdje — Postavke → Mrežne mape (web)."""
import dataclasses
import ipaddress
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from atlas.core.llm import LLMError, LLMUnavailable
from atlas.ops import backup, certs, model_table, preflight, shortcut, tui, tui_curses, winsvc, wizard_state
from atlas.rag import embed
from atlas.web import firstrun

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
    tui.print_header("1/5  Preduvjeti", out=out)
    while True:
        reqs = preflight.requirements(cfg)
        ok = render_preflight(reqs, out=out)
        # winget auto-install je Windows-only (drugdje ga wizard ne nudi — brief).
        instalabilni = [r for r in reqs
                        if os.name == "nt" and r["key"] in preflight.WINGET_IDS
                        and r["status"] in ("fail", "warn")]
        if ok and not instalabilni:
            return True
        out("")
        opcije, akcije = [], []
        if ok:
            opcije.append("Nastavi (obavezni preduvjeti ✓)")
            akcije.append("ok")
        for r in instalabilni:
            opcije.append(f"Auto-instaliraj: {r['naziv']}")
            akcije.append(("winget", r["key"]))
        opcije.append("Provjeri ponovno")
        akcije.append("retry")
        opcije.append("Prekini setup")
        akcije.append("stop")
        naslov = ("Neki obavezni preduvjeti nedostaju (✗) — što dalje?"
                  if not ok else "Preporuke (⚠) — što dalje?")
        idx = tui_curses.radiolist(naslov, opcije, selected=0,
                                   cancel_returns=len(akcije) - 1,
                                   input_fn=input_fn, out=out)
        akcija = akcije[idx]
        if akcija == "ok":
            return True
        if akcija == "stop":
            return False
        if isinstance(akcija, tuple):
            preflight.install_via_winget(akcija[1], out=out)
        # "retry" i post-install: petlja ponovno provjerava


def page_operater(spine, *, input_fn=input, out=print) -> bool:
    """Stranica 2: kreira prvog admina (operatera). Vrati True na uspjeh.
    Ako admin već postoji (npr. kreiran preko web /setup/owner puta, ili je
    prošli pokušaj pao između create_first_owner i set_stage), preskoči
    prompt — inače resume nema izlaza (create_first_owner uvijek baca)."""
    if not firstrun.needs_onboarding(spine):
        out("Administrator već postoji — preskačem.")
        return True
    tui.print_header("2/5  Operater (administrator)", out=out)
    while True:
        username = tui.prompt_text("Korisničko ime", input_fn=input_fn, out=out)
        if username:
            break
        out("Korisničko ime ne smije biti prazno.")
    while True:
        pw = tui.prompt_password("Lozinka (min 8)", input_fn=input_fn, out=out)
        if len(pw) < _MIN_PW:
            out(f"Lozinka mora imati barem {_MIN_PW} znakova.")
            continue
        pw2 = tui.prompt_password("Ponovi lozinku", input_fn=input_fn, out=out)
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
    from atlas.rag import embed
    return embed.download_model(cfg)


_SELF_TEST_PROMPT = "Odgovori točno: OK ATLAS"


def _llm_complete(spine, cfg, prompt: str):
    """Indirekcija radi testabilnosti; LLMClient ima vlastiti timeout (120 s)
    koji pokriva i cold-load većih modela."""
    from atlas.business import model_settings
    from atlas.core.llm import LLMClient
    return LLMClient(model_settings.apply(spine, cfg)).complete(
        [{"role": "user", "content": prompt}], max_tokens=20)


def self_test(spine, cfg, *, input_fn=input, out=print, retries: int = 3) -> bool:
    """Kratki test odabranog modela. Uspjeh = ne-prazan odgovor unutar timeouta.
    Regex "OK ATLAS" = soft-check (upozorenje). Kvar ne ruši setup."""
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
        if not re.search(r"OK ATLAS", text, re.IGNORECASE):
            out("  ⚠ upozorenje: odgovor ne sadrži 'OK ATLAS' — model radi, ali slabo slijedi upute.")
        return True
    return False


def choose_embed_model(state: dict, default_model: str) -> str:
    """bge-m3 kad KOMOTNO stane u RAM i kad ga fastembed stvarno podržava
    (E2E: unsupported model ne smije u ponudu); inače mali default."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits" and embed.supports(_BGE_M3):
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


def page_model(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 3: Ollama spremnost -> llmfit lista -> JEDAN model -> pull -> spremi
    -> embedding -> self-test. Skip-grana vraća True (spec: ne zaglavi)."""
    tui.print_header("3/5  Model (LLM)", out=out)
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
    st = preflight.system_state(cfg)
    out(f"Stroj: slobodno ~{st.get('ram_free_gb', '?')} GB RAM / "
        f"~{st.get('disk_free_gb', '?')} GB diska "
        f"(ukupno {st.get('ram_total_gb', '?')} GB RAM)")
    out("Modeli za ovaj hardver (llmfit — kvantizacija izračunata po stroju):")
    header, lines = model_table.table_rows(rows)
    names = [r["ollama_name"] for r in rows]
    items = lines + ["Preskoči — postavi kasnije"]
    idx = tui_curses.radiolist(
        "Odaberi JEDAN model (🟢 komotno / 🟡 tijesno — RAM, ne disk):",
        items, selected=0, header=header,
        cancel_returns=len(names),          # ESC = preskoči
        input_fn=input_fn, out=out)
    if idx == len(names):
        return True
    model = names[idx]

    row = rows[idx]
    kandidati = preflight.quant_tags(model, row.get("best_quant", "")) + [model]
    pulled_tag = None
    for tag in kandidati:
        if tag != model:
            out(f"Skidam {tag} (točan kvant; prekid je siguran — nastavlja gdje je stalo)...")
        else:
            out(f"Skidam {model} (prekid je siguran — nastavlja gdje je stalo)...")
        if preflight.ollama_pull(tag, url, out=out):
            pulled_tag = tag
            break
    if not pulled_tag:
        out("Model nije skinut. Pokreni setup ponovno ili postavi kasnije u Postavkama.")
        return tui.prompt_yes_no("Nastavi setup bez modela?", default=True,
                                 input_fn=input_fn, out=out)
    if pulled_tag == model and len(kandidati) > 1:
        out("  ⚠ Registry nema izračunati kvant — skinut zadani tag "
            "(može biti veći od procjene).")
    stvarno = preflight.ollama_model_size(pulled_tag, url)
    if stvarno:
        procjena = model_table.disk_gb(row.get("params", ""), row.get("best_quant", ""))
        linija = f"  Stvarna veličina: {stvarno:.1f} GB"
        if procjena:
            linija += f" (procjena {procjena:.1f} GB)"
        if procjena and stvarno > procjena * 1.3:
            linija = "  ⚠ " + linija.removeprefix("  ") + " — veće od procjene!"
        out(linija)

    emb = setup_embedding(spine, cfg, out=out)
    from atlas.business import model_settings
    model_settings.save(spine, "ollama", model=pulled_tag, ollama_url=url,
                        embed_model=emb or "", user="setup")
    if not self_test(spine, cfg, input_fn=input_fn, out=out):
        out("⚠ Self-test nije prošao — model je spremljen, provjeri ga kasnije u Postavkama.")
    return True


def _best_name(names: list[str], fallback_ip: str) -> str:
    """Najbolje ime za prikaz: prava FQDN (točka, nije atlas.local/.local),
    pa hostname.local, pa fallback_ip."""
    for n in names:
        if "." in n and n != "atlas.local" and not n.endswith(".local"):
            return n
    for n in names:
        if n.endswith(".local") and n != "atlas.local":
            return n
    return fallback_ip


def page_mreza(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 4: bind IP + port, cert/HTTPS, proxy, servis (preskočivo)."""
    tui.print_header("4/5  Mreža + HTTPS + servis", out=out)
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
    names = certs.friendly_names()
    cert, key = certs.generate_self_signed(cert_dir, ips=[cert_ip],
                                           hostnames=names, out=out)
    out(f"HTTPS certifikat: {cert}")
    out(f"  SHA256: {certs.fingerprint_sha256(cert)}")
    best = _best_name(names, cert_ip)
    out("  Na OVOM računalu: atlas trust")
    out(f"  Radnici: nakon pokretanja servera otvore http://{best}:8080/postavi (bootstrap stranica)")

    # 6) spremi net postavke
    spine.set_override("net", "host", bind)
    spine.set_override("net", "port", str(port))
    spine.set_override("net", "cert_path", cert)
    spine.set_override("net", "key_path", key)
    if best != cert_ip:
        out(f"✓ Server će služiti na https://{best}:{port} (i https://{cert_ip}:{port})")
    else:
        out(f"✓ Server će služiti na https://{cert_ip}:{port}")

    # 7) servis (preskočivo; neuspjeh ne ruši stranicu)
    if tui.prompt_yes_no("Instaliraj kao servis (autostart)?", default=False,
                         input_fn=input_fn, out=out):
        exe = shutil.which("atlas") or f"{sys.executable} -m atlas"
        if not winsvc.install_service(exe, getattr(cfg, "data_dir", "."), port, out=out):
            out("⚠ Servis nije instaliran — možeš ponoviti kasnije (admin konzola).")
    return True


def render_summary(spine, cfg, *, out=print) -> None:
    """Sažetak stranica 1-4: konfigurirano / preskočeno + gdje dodati kasnije."""
    from atlas.business import folders
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
    out(f"  HTTPS cert: {cert or '— nema (atlas setup, stranica 4)'}")
    mape = folders.list_folders(spine)
    if mape:
        for m in mape:
            out(f"  Mapa [{m['role']}]: {m['path']}")
    else:
        out("  Mape: — dodaj nakon prijave (Postavke → Mrežne mape)")


def page_gotovo(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 5: sažetak + konkretan backup/restore. Uvijek vraća True —
    zadnja stranica ne smije blokirati dovršetak setupa."""
    tui.print_header("5/5  Gotovo — sažetak i sigurnosne kopije", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    out("Sigurnosne kopije (spremi na vanjski medij/NAS — bez ovoga restore ne vraća sustav):")
    out(f"  • Baza: {cfg.db_path} — snapshot naredbom: atlas backup")
    out(f"  • Tajni ključ (JWT): {Path(cfg.data_dir) / 'secret'} — bez njega prijave "
        "nakon restorea ne rade; kopiraj ga UZ bazu")
    ollama_dir = r"%USERPROFILE%\.ollama\models" if os.name == "nt" else "~/.ollama/models"
    out(f"  • Ollama modeli: {ollama_dir} — bez njih RAG ne radi "
        "ni uz vraćenu bazu i ključ")
    out("  • Restore provjeri na drugom stroju: atlas restore <putanja do kopije> "
        "(server zaustavljen)")
    if tui.prompt_yes_no("Napravi verificirani snapshot baze sada?", default=True,
                         input_fn=input_fn, out=out):
        try:
            res = backup.create_backup(cfg)
            out(f"  ✓ {res['path']} ({res['size']} B, verificiran quick_checkom)")
        except Exception as e:
            out(f"  ⚠ Snapshot nije uspio: {e} — pokreni `atlas backup` ručno.")
    return True


def _detached_kwargs() -> dict:
    """Popen kwargs da dijete preživi zatvaranje wizard konzole (isti obrazac
    kao preflight.start_ollama; getattr fallback za testove na Linuxu s
    mockanim platform.system())."""
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    return kwargs


def launch_now(spine, cfg, *, input_fn=input, out=print, popen=subprocess.Popen) -> None:
    """Ponudi start servera (detached) + Edge app-prozor. Poziva se IZA
    mark_complete — nikakav kvar ovdje ne smije poništiti dovršeni setup.
    ponytail: pravi servis (autostart) čeka WinSW/NSSM wrapper (v. winsvc);
    do tada je ovo ručni start koji preživi zatvaranje konzole."""
    host = spine.get_override("net", "host") or "127.0.0.1"
    port = spine.get_override("net", "port") or "8443"
    url_host = preflight.local_ip() if host == "0.0.0.0" else host
    url = f"https://{url_host}:{port}"
    shortcut.create_desktop_shortcut(url, out=out)
    try:
        start = tui.prompt_yes_no(f"Pokreni ATLAS sada? ({url})", default=True,
                                  input_fn=input_fn, out=out)
    except (EOFError, KeyboardInterrupt):
        start = False
    if not start:
        out(f"Kasnije: `atlas serve` pa otvori {url}")
        return
    exe = shutil.which("atlas")
    cmd = [exe, "serve"] if exe else [sys.executable, "-m", "atlas", "serve"]
    # folders je fail-closed bez mount_roots (v. business/folders._scoped) — bez ovoga
    # bi mape registrirane kroz web Postavke → Mrežne mape na happy pathu tiho ne
    # radile (upgrade instalacije mogu ih imati čak i bez ove wizard grane).
    from atlas.business import folders
    roots = [m["path"] for m in folders.list_folders(spine)]
    env = None
    if roots:
        from atlas import config
        existing = [p for p in config._env("MOUNT_ROOTS", "").split(",") if p.strip()]
        merged = list(dict.fromkeys(existing + roots))
        env = {**os.environ, "ATLAS_MOUNT_ROOTS": ",".join(merged)}
    try:
        popen(cmd, env=env, **_detached_kwargs())
    except OSError as e:
        out(f"⚠ Server nije pokrenut ({e}) — pokreni ručno: atlas serve")
        return
    out(f"✓ Server pokrenut u pozadini — {url}")
    if platform.system() == "Windows":
        try:
            # start preko App Paths (msedge nije na PATH-u); "" = naslov prozora
            popen(["cmd", "/c", "start", "", "msedge", f"--app={url}"],
                  **_detached_kwargs())
            out("Otvaram Edge app-prozor...")
        except OSError:
            out(f"Otvori ručno u pregledniku: {url}")
    else:
        out(f"Otvori u pregledniku: {url}")


def page_upgrade(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Postojeća baza bez setup_complete flaga (instalacija prije wizarda):
    spec traži „detektiraj, ponudi migraciju, ne novi setup". Migracija =
    preuzmi postojeću konfiguraciju (shema je ista, nema podataka za seljenje).
    True = preuzeto (pozivatelj označava setup dovršenim), False = normalni wizard."""
    tui.print_header("Postojeća baza otkrivena", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    # Default „da" samo za klasičnu legacy instalaciju (admin je okidač u run();
    # model+host potvrđuju da je sustav bio konfiguriran, ne napola postavljen).
    default = bool(spine.get_override("model", "model")
                   and spine.get_override("net", "host"))
    return tui.prompt_yes_no(
        "Preuzmi postojeće postavke i označi setup dovršenim? (ne = prođi setup)",
        default=default, input_fn=input_fn, out=out)


def run(spine, cfg, *, input_fn=input, out=print) -> None:
    if wizard_state.is_complete(spine):
        out("Setup je već dovršen. Za ponovno: `atlas setup --reset`.")
        return
    stage = wizard_state.get_stage(spine)
    # min(): legacy baza može imati stage=5 (stara 6-stranična numeracija) —
    # prikaz ne smije obećati nepostojeći "korak 6".
    out(f"ATLAS setup (nastavak od koraka {min(stage + 1, 5)}).")
    try:
        # Upgrade grana (spec str.1): postojeća baza — korisnici postoje, a
        # setup nikad nije dovršen. Resume (stage>0) nije upgrade slučaj.
        if stage == 0 and not firstrun.needs_onboarding(spine):
            if page_upgrade(spine, cfg, input_fn=input_fn, out=out):
                wizard_state.mark_complete(spine)
                out("✓ Postojeća konfiguracija preuzeta — setup dovršen.")
                launch_now(spine, cfg, input_fn=input_fn, out=out)
                return
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
        # <= ne < : legacy baza sa stage=5 (iz vremena 6 stranica — prošla staru
        # stranicu mapa, ali NE i sažetak) mora još proći page_gotovo. Novi tok
        # nikad ne resumira sa stage=5 bez complete flaga — set_stage(5) niže pa
        # mark_complete izvrše se u istom pozivu, bez povratka između njih.
        if stage <= 5:
            if not page_gotovo(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na sažetku. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 5)
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (npr. servis bez terminala) — bez tracebacka.
        # ponytail: run() ostaje `-> None`; pozivatelj (_cmd_setup) ne detektira
        # ovaj slučaj posebno pa CLI izlazi s 0. Upgrade path: vratiti bool/kod
        # ako se pokaže da nešto ovisi o exit statusu.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `atlas setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    wizard_state.mark_complete(spine)
    out("✓ Setup dovršen (5/5) — web sučelje je spremno.")
    launch_now(spine, cfg, input_fn=input_fn, out=out)
