"""Terminal setup wizard. One fixed sequence, resume via wizard_state.
5 pages: prerequisites, operator, model, network/HTTPS/service, done.
Network folders are not configured here — Settings -> Network folders (web)."""
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
_BGE_M3_GB = 1.2   # fp16, approximately (manually curated)


def render_preflight(reqs, *, out=print) -> bool:
    """Print prerequisites with glyph+detail; fix only for warn/fail. Return True when there is no 'fail'."""
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
        # winget auto-install is Windows-only (elsewhere the wizard does not offer it — brief).
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
        # "retry" and post-install: the loop checks again


def page_operater(spine, *, input_fn=input, out=print) -> bool:
    """Page 2: creates the first admin (operator). Return True on success.
    If the admin already exists (e.g. created via the web /setup/owner path, or a
    previous attempt failed between create_first_owner and set_stage), skip
    the prompt — otherwise resume has no exit (create_first_owner always throws)."""
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
    """Indirection for testability (embed pulls in fastembed only when called)."""
    from atlas.rag import embed
    return embed.download_model(cfg)


_SELF_TEST_PROMPT = "Odgovori točno: OK ATLAS"


def _llm_complete(spine, cfg, prompt: str):
    """Indirection for testability; LLMClient has its own timeout (120 s)
    that also covers the cold-load of larger models."""
    from atlas.business import model_settings
    from atlas.core.llm import LLMClient
    return model_settings.build_llm(spine, cfg).complete(
        [{"role": "user", "content": prompt}], max_tokens=20)


def self_test(spine, cfg, *, input_fn=input, out=print, retries: int = 3) -> bool:
    """Short test of the selected model. Success = non-empty response within the timeout.
    Regex "OK ATLAS" = soft-check (warning). Failure does not break setup."""
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
    """bge-m3 when it fits COMFORTABLY in RAM and when fastembed actually supports it
    (E2E: an unsupported model must not appear in the offering); otherwise the small default."""
    total = state.get("ram_total_gb") or 0.0
    if preflight.fit_pill(_BGE_M3_GB, total) == "fits" and embed.supports(_BGE_M3):
        return _BGE_M3
    return default_model


def setup_embedding(spine, cfg, *, out=print) -> str | None:
    """Choose the embedding by RAM, download and VERIFY it; on error fall back to
    cfg.embed_model. Return the name of the verified model or None (does not block setup)."""
    chosen = choose_embed_model(preflight.system_state(cfg), cfg.embed_model)
    for candidate in dict.fromkeys([chosen, cfg.embed_model]):   # no duplicates
        out(f"Embedding model: {candidate} — skidam i provjeravam...")
        res = _download_embed(dataclasses.replace(cfg, embed_model=candidate))
        if res.get("ok"):
            out(f"  ✓ {candidate} (dim {res.get('dim')})")
            return candidate
        out(f"  ⚠ {candidate}: {res.get('error', 'nepoznata greška')}")
    out("Embedding nije skinut — RAG indeksiranje neće raditi dok se ne skine u Postavkama.")
    return None


def page_model(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Page 3: Ollama readiness -> llmfit list -> ONE model -> pull -> save
    -> embedding -> self-test. The skip branch returns True (spec: do not get stuck)."""
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
        preflight.install_llmfit(out=out)   # pip is cross-platform — no os.name check
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
        cancel_returns=len(names),          # ESC = skip
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
    """Thin alias — the only implementation is certs.best_display_host."""
    return certs.best_display_host(names, fallback_ip)


def page_mreza(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Page 4: bind IP + port, cert/HTTPS, proxy, service (skippable)."""
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

    # 3) static address (warning, we do not run netsh set)
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
    from atlas import config
    bport = config._env("BOOTSTRAP_PORT", "8080")
    out("  Na OVOM računalu: atlas trust")
    out(f"  Radnici: nakon pokretanja servera otvore http://{best}:{bport}/postavi (bootstrap stranica)")

    # 6) save net settings
    spine.set_override("net", "host", bind)
    spine.set_override("net", "port", str(port))
    spine.set_override("net", "cert_path", cert)
    spine.set_override("net", "key_path", key)
    if best != cert_ip:
        out(f"✓ Server će služiti na https://{best}:{port} (i https://{cert_ip}:{port})")
    else:
        out(f"✓ Server će služiti na https://{cert_ip}:{port}")

    # 7) service (skippable; failure does not break the page)
    if tui.prompt_yes_no("Instaliraj kao servis (autostart)?", default=False,
                         input_fn=input_fn, out=out):
        from atlas.business import folders
        exe, cmd_args = winsvc.resolve_atlas_cmd()
        roots = [m["path"] for m in folders.list_folders(spine)]
        ok = winsvc.install_service(exe, cmd_args, getattr(cfg, "data_dir", "."), port,
                                    mount_roots=roots, out=out)
        if not ok:
            out("⚠ Servis nije instaliran — možeš ponoviti kasnije (admin konzola).")
    return True


def render_summary(spine, cfg, *, out=print) -> None:
    """Summary of pages 1-4: configured / skipped + where to add it later."""
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
    """Page 5: summary + concrete backup/restore. Always returns True —
    the last page must not block completion of setup."""
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
    cert_path = spine.get_override("net", "cert_path")
    if cert_path:
        host = spine.get_override("net", "host") or "127.0.0.1"
        port = spine.get_override("net", "port") or "8443"
        url_host = preflight.local_ip() if host == "0.0.0.0" else host
        # finding: the display name must be aligned with the SAN of the EXISTING cert —
        # an old installation may have cert=[atlas.local, IP] while friendly_names()
        # already offers a newer name (e.g. nick.fritz.box) -> browser warning even after
        # installing the cert because the cert does not cover that name at all.
        name = certs.verified_display_host(cert_path, certs.friendly_names(), url_host)
        from atlas import config
        bport = config._env("BOOTSTRAP_PORT", "8080")
        out("")
        out("Uputa za radnike (kopiraj u mail):")
        if bport != "0":
            out(f"  1. Otvori: http://{name}:{bport}/postavi")
            out("  2. Klikni \"Preuzmi postavljanje\" pa desni klik na preuzeto →")
            out("     Pokreni kao administrator → Da")
            out(f"  3. Ubuduće koristi: https://{name}:{port} (spremi u favorite)")
        else:
            out(f"  Ubuduće koristi: https://{name}:{port} (spremi u favorite)")
    if tui.prompt_yes_no("Napravi verificirani snapshot baze sada?", default=True,
                         input_fn=input_fn, out=out):
        try:
            res = backup.create_backup(cfg)
            out(f"  ✓ {res['path']} ({res['size']} B, verificiran quick_checkom)")
        except Exception as e:
            out(f"  ⚠ Snapshot nije uspio: {e} — pokreni `atlas backup` ručno.")
    return True


def _detached_kwargs(data_dir: str | None = None) -> dict:
    """Popen kwargs so the child survives closing the wizard console (same pattern
    as preflight.start_ollama; getattr fallback for tests on Linux with a
    mocked platform.system()). data_dir given (serve call) -> stdout/stderr
    go to <data_dir>/logs/serve.{out,err}.log (append) instead of DEVNULL — a silent
    server crash otherwise leaves no trace anywhere (E2E finding). The Edge call has no
    data_dir -> stays DEVNULL (nothing to log)."""
    if data_dir:
        logs = Path(data_dir) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"stdout": open(logs / "serve.out.log", "ab"),
                        "stderr": open(logs / "serve.err.log", "ab"),
                        "stdin": subprocess.DEVNULL}
    else:
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                  "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0x8)
                                   | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200))
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _open_edge(url: str, *, out, popen) -> None:
    """Open an Edge app window (Windows); elsewhere just an instruction. Edge needs no
    log — DEVNULL via _detached_kwargs without data_dir."""
    if platform.system() != "Windows":
        out(f"Otvori u pregledniku: {url}")
        return
    try:
        # start via App Paths (msedge is not on PATH); "" = window title
        popen(["cmd", "/c", "start", "", "msedge", f"--app={url}"], **_detached_kwargs())
        out("Otvaram Edge app-prozor...")
    except OSError:
        out(f"Otvori ručno u pregledniku: {url}")


def launch_now(spine, cfg, *, input_fn=input, out=print, popen=subprocess.Popen) -> None:
    """Offer to start the server (detached) + Edge app window. Called AFTER
    mark_complete — no failure here may undo the completed setup.
    If a real service (winsvc) is already running, no detached copy is spun up —
    just a shortcut + Edge to the existing service (spec 2026-08-08, service t.2)."""
    host = spine.get_override("net", "host") or "127.0.0.1"
    port = spine.get_override("net", "port") or "8443"
    url_host = preflight.local_ip() if host == "0.0.0.0" else host
    url = f"https://{url_host}:{port}"
    shortcut.create_desktop_shortcut(url, out=out)
    if winsvc.service_status() == "running":
        out(f"Servis ATLAS već radi — {url}")
        _open_edge(url, out=out, popen=popen)
        return
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
    # folders is fail-closed without mount_roots (see business/folders._scoped) — without this
    # folders registered through the web Settings -> Network folders would silently not
    # work on the happy path (upgrade installations may have them even without this wizard branch).
    from atlas.business import folders
    roots = [m["path"] for m in folders.list_folders(spine)]
    env = None
    if roots:
        from atlas import config
        existing = [p for p in config._env("MOUNT_ROOTS", "").split(",") if p.strip()]
        merged = list(dict.fromkeys(existing + roots))
        env = {**os.environ, "ATLAS_MOUNT_ROOTS": ",".join(merged)}
    kwargs = _detached_kwargs(getattr(cfg, "data_dir", None))
    try:
        popen(cmd, env=env, **kwargs)
    except OSError as e:
        out(f"⚠ Server nije pokrenut ({e}) — pokreni ručno: atlas serve")
        return
    finally:
        # Popen (when it is a real subprocess) duplicates the fd into the child — the parent
        # must close its own copy of the log files on both the success and the OSError
        # path, otherwise an open file handle leaks in the wizard process.
        for key in ("stdout", "stderr"):
            fh = kwargs.get(key)
            if hasattr(fh, "close"):
                fh.close()
    out(f"✓ Server pokrenut u pozadini — {url}")
    _open_edge(url, out=out, popen=popen)


def page_upgrade(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Existing database without a setup_complete flag (installation before the wizard):
    the spec requires "detect, offer migration, not a new setup". Migration =
    adopt the existing configuration (the schema is the same, no data to migrate).
    True = adopted (the caller marks setup as complete), False = normal wizard."""
    tui.print_header("Postojeća baza otkrivena", out=out)
    render_summary(spine, cfg, out=out)
    out("")
    # Default "yes" only for a classic legacy installation (admin is the trigger in run();
    # model+host confirm the system was configured, not half set up).
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
    # min(): a legacy database may have stage=5 (the old 6-page numbering) —
    # the display must not promise a non-existent "step 6".
    out(f"ATLAS setup (nastavak od koraka {min(stage + 1, 5)}).")
    try:
        # Upgrade branch (spec p.1): existing database — users exist, but
        # setup was never completed. Resume (stage>0) is not an upgrade case.
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
        # <= not < : a legacy database with stage=5 (from the 6-page era — it passed the old
        # folders page, but NOT the summary) must still pass page_gotovo. The new flow
        # never resumes with stage=5 without the complete flag — set_stage(5) below then
        # mark_complete execute in the same call, with no return between them.
        if stage <= 5:
            if not page_gotovo(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na sažetku. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 5)
    except (EOFError, KeyboardInterrupt):
        # non-TTY / piped stdin (e.g. a service without a terminal) — no traceback.
        # ponytail: run() stays `-> None`; the caller (_cmd_setup) does not detect
        # this case specially so the CLI exits with 0. Upgrade path: return a bool/code
        # if it turns out something depends on the exit status.
        out("")
        out("Setup zahtijeva interaktivni terminal. Pokreni `atlas setup` u terminalu; "
            "stanje je spremljeno — nastavlja gdje je stao.")
        return
    wizard_state.mark_complete(spine)
    out("✓ Setup dovršen (5/5) — web sučelje je spremno.")
    launch_now(spine, cfg, input_fn=input_fn, out=out)
