"""ATLAS CLI entrypoint: python -m atlas <cmd>."""
import argparse
import getpass
import os
import sys
from pathlib import Path


def _stub(args) -> int:
    print("nije još implementirano")
    return 2


def _net_overrides(spine, cfg):
    """Network settings from config_overrides(module='net') with a cfg fallback."""
    host = spine.get_override("net", "host") or cfg.host
    try:
        port = int(spine.get_override("net", "port") or cfg.port)
    except ValueError:
        port = cfg.port
    cert = spine.get_override("net", "cert_path") or ""
    key = spine.get_override("net", "key_path") or ""
    return host, port, cert, key


def _start_bootstrap(cert: str, host: str, port: int) -> None:
    """Bootstrap HTTP server for workstations (alongside the HTTPS server) — a
    convenience, not a requirement: ATLAS_BOOTSTRAP_PORT="0" disables it, a bind
    error only warns (logged)."""
    from atlas import config
    from atlas.ops import certs, preflight
    from atlas.web.bootstrap_http import start_bootstrap_server

    bport_raw = config._env("BOOTSTRAP_PORT", "8080")
    if bport_raw == "0":
        return
    try:
        bport = int(bport_raw)
    except ValueError:
        bport = 8080
    url_host = preflight.local_ip() if host == "0.0.0.0" else host
    # finding: the display name must match the SAN of the EXISTING cert (see
    # certs.verified_display_host) — otherwise an old install offers a name the
    # cert does not cover at all -> browser warning even after installing the cert.
    display = certs.verified_display_host(cert, certs.friendly_names(), url_host)
    https_url = f"https://{display}:{port}"
    thread = start_bootstrap_server(cert, https_url, host, port=bport)
    if thread is not None:
        print(f"Bootstrap za radnike: http://{display}:{bport}/postavi")


def _cmd_serve(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.web.api import create_app
    import uvicorn

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    # server_header=False: don't leak the "uvicorn" version; middleware already sends Server: ATLAS
    host, port, cert, key = _net_overrides(spine, cfg)
    ssl_kw = {}
    if cert and key:
        if Path(cert).exists() and Path(key).exists():
            ssl_kw = {"ssl_certfile": cert, "ssl_keyfile": key}
        else:
            print(f"⚠ Cert/key ne postoje ({cert}, {key}) — pokrećem bez HTTPS-a.")
    if ssl_kw:
        _start_bootstrap(cert, host, port)
    _start_mdns(port)  # advertise _atlas._tcp so workstations find the server without manual entry
    uvicorn.run(create_app(spine, cfg), host=host, port=port,
                server_header=False, **ssl_kw)
    return 0


def _local_ip() -> str:  # pragma: no cover — network
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _start_mdns(port: int) -> None:  # pragma: no cover — network/thread
    import threading

    import atlas
    from atlas.core import mdns
    stop = threading.Event()
    threading.Thread(target=mdns.serve_responder,
                     args=(_local_ip(), port, atlas.__version__, stop),
                     daemon=True, name="atlas-mdns").start()


def _cmd_discover(args) -> int:
    """Find ATLAS servers on the network (workstation, before entering an address)."""
    import json as _json

    from atlas.core import mdns
    servers = mdns.discover_atlas(timeout=args.timeout)
    print(_json.dumps(servers, ensure_ascii=False))
    return 0 if servers else 1


def _dashboard_url(spine, cfg) -> str:
    """ATLAS dashboard URL from the net override + cert SAN. The installer opens
    it after installing the service (instead of guessing the port/host)."""
    from atlas.ops import certs
    host, port, cert, key = _net_overrides(spine, cfg)
    scheme = "https" if (cert and key and Path(cert).exists()) else "http"
    display = host if host not in ("0.0.0.0", "", "127.0.0.1") else "localhost"
    if scheme == "https" and cert and Path(cert).exists():
        display = certs.verified_display_host(cert, certs.friendly_names(), display)
    return f"{scheme}://{display}:{port}"


def _cmd_open(args) -> int:
    """Open (or print) the ATLAS dashboard in the browser."""
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    cfg = get_config()
    url = _dashboard_url(init_spine(cfg.db_path), cfg)
    print(url)
    if not args.print_only:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass
    return 0


def _cmd_ingest(args) -> int:
    if not args.imap:
        return _stub(args)
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.docs.imap_fetch import fetch_new

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    result = fetch_new(spine, cfg)
    print(f"fetched={result['fetched']} attachments={len(result['attachments'])}")
    return 0


def _cmd_doctor(args) -> int:
    from atlas.config import get_config
    from atlas.ops import doctor

    cfg = get_config()
    results = doctor.run(cfg)
    print(doctor.format_report(results))
    return 0 if doctor.required_ok(results) else 1


def _cmd_health(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.ops import health

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    print(health.check(spine, cfg))
    return 0


def _cmd_auth(args) -> int:
    if getattr(args, "auth_cmd", None) != "add":
        return _stub(args)
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.web.deps import add_user

    from atlas import config
    pw = config._env("PASS") or getpass.getpass()
    spine = init_spine(get_config().db_path)
    add_user(spine, args.user, pw)
    print(args.user)
    return 0


def _cmd_digest(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.ops import digest

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    print(digest.build_digest(spine, cfg))
    return 0


def _cmd_daemon(args) -> int:
    import signal
    import threading

    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.ops.scheduler import build_default_scheduler

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    sched = build_default_scheduler(spine, cfg)
    stop_event = threading.Event()

    def _handle(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    poll_s = 30
    print(f"ATLAS daemon pokrenut, poll {poll_s}s, Ctrl-C za stop")
    sched.run(poll_s=poll_s, stop_event=stop_event)
    print("ATLAS daemon zaustavljen")
    return 0


def _cmd_setup(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    if getattr(args, "download_models", False):
        from atlas.rag import embed
        print(f"Povlačim embedding model: {cfg.embed_model} … (jednokratno, može potrajati)")
        res = embed.download_model()
        if res["ok"]:
            print(f"✓ Spremno: {res['model']} (dim={res['dim']}). Vektorska pretraga je aktivna.")
            return 0
        print(f"✗ Neuspjeh: {res['error']}")
        return 1
    from datetime import date

    from atlas.ops import seeds, wizard, wizard_state
    if getattr(args, "reset", False):
        wizard_state.reset(spine)
    seeds.all(spine, date.today().year)  # base data (chart of accounts, watch sources...) — idempotent, like the old setup.run
    wizard.run(spine, cfg)
    return 0


def _cmd_models(args) -> int:
    from atlas.ops import model_recommender

    print(model_recommender.report())
    return 0


def _cmd_eval(args) -> int:
    from atlas.config import get_config
    from atlas.ops import evalrun

    report = evalrun.run(get_config())
    print(report)
    return 0 if report["pass"] else 1


def _cmd_stats(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine

    spine = init_spine(get_config().db_path)
    conn = spine.read()
    print("interactions po laneu:")
    for row in conn.execute(
        "SELECT lane, COUNT(*) AS n FROM interactions GROUP BY lane ORDER BY n DESC"
    ):
        print(f"  {row['lane']}: {row['n']}")
    total_cache = conn.execute("SELECT COUNT(*) AS n FROM query_cache").fetchone()["n"]
    print(f"cache entries: {total_cache}")
    print("top 5 upita:")
    for row in conn.execute(
        "SELECT query, COUNT(*) AS n FROM interactions GROUP BY query ORDER BY n DESC LIMIT 5"
    ):
        print(f"  {row['n']}x {row['query']}")
    return 0


def _cmd_backup(args) -> int:
    from atlas.config import get_config
    from atlas.ops import backup

    cfg = get_config()
    b = backup.create_backup(cfg)
    n = backup.prune(cfg, keep=14)
    print(f"✓ Kopija: {b['path']} ({b['size']} B)" + (f"; obrisano starih: {n}" if n else ""))
    return 0


def _cmd_restore(args) -> int:
    from atlas.config import get_config
    from atlas.ops import backup

    cfg = get_config()
    try:
        res = backup.restore_backup(cfg, args.path)
    except ValueError as e:
        print(f"GREŠKA: {e}")
        return 1
    print(f"✓ Vraćeno iz {res['restored_from']}. Prethodna baza: {cfg.db_path}.prerestore")
    return 0


def _cmd_watch(args) -> int:
    if getattr(args, "watch_cmd", None) != "run":
        return _stub(args)
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.web.watchlist import check_all

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    changes = check_all(spine, cfg)
    print(f"changes={len(changes)}")
    return 0


def _cmd_ocr(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.docs.ocr import ocr_pdf, OCRUnavailable

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    try:
        result = ocr_pdf(spine, cfg, args.path)
    except (OCRUnavailable, ValueError) as e:
        print(e)
        return 1
    print(result)
    return 0


def _cmd_reminders(args) -> int:
    import re

    from atlas.config import get_config
    from atlas.core.spine import init_spine

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    reminders_cmd = getattr(args, "reminders_cmd", None)
    if reminders_cmd == "add":
        from atlas.business.nldate import parse_date

        from atlas import config
        user = config._env("USER", "sustav")
        due = parse_date(args.due)
        if due is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.due):
            due = args.due  # already ISO — parse_date only understands NL/dot dates
        if due is None:
            print("Ne razumijem datum")
            return 1
        with spine.write() as c:
            c.execute(
                "INSERT INTO reminders(user, body, due) VALUES(?,?,?)",
                (user, args.text, due),
            )
        return 0
    if reminders_cmd == "dump":
        from atlas.ops.reminders_dump import dump

        result = dump(spine, cfg)
        print(result["path"])
        return 0
    rows = spine.read().execute(
        "SELECT id, user, body, due FROM reminders WHERE done=0 ORDER BY due"
    ).fetchall()
    for r in rows:
        print(f"[{r['id']}] {r['due']} — {r['body']} ({r['user']})")
    return 0


def _cmd_browser(args) -> int:
    if args.sub != "status":
        return _stub(args)
    # ponytail: CLI runs in its own process, separate from `serve`'s Bridge —
    # this reports 0 (a fresh queue), not the live server's pending count.
    # Upgrade path: HTTP GET /browser/status against the running server.
    from atlas.browser.bridge import Bridge

    print(f"pending={Bridge().pending()}")
    return 0


def _cmd_trust(args) -> int:
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.ops import certs

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    cert = spine.get_override("net", "cert_path") or ""
    if not cert or not Path(cert).exists():
        print("Nema certifikata — pokreni `atlas setup` (stranica Mreža) da ga generiraš.")
        return 1
    print(f"Javni certifikat: {cert}")
    print(f"SHA256 fingerprint: {certs.fingerprint_sha256(cert)}")
    print("")
    print("Instalacija na klijentima (SAMO cert.pem — key.pem se nikad ne dijeli):")
    print("  Windows: dupli klik na cert.pem → Instaliraj → 'Pouzdana izdavačka tijela'")
    print("           (ili GPO distribucija ako postoji AD domena).")
    print("  Provjeri fingerprint prije instalacije — mora se podudarati s gornjim.")
    return 0


def _elevated() -> bool:
    """Windows: admin via shell32 (ctypes.windll does not exist outside Windows
    -> AttributeError, caught). POSIX: root (uid 0)."""
    if os.name == "nt":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _cmd_servis(args) -> int:
    """atlas servis <install|uninstall|status> — WinSW (Windows) / systemd
    (Linux) service wrapping `atlas serve` (spec 2026-08-08). install/uninstall
    require elevation; status does not."""
    from atlas.business import folders
    from atlas.config import get_config
    from atlas.core.spine import init_spine
    from atlas.ops import winsvc

    if args.akcija == "status":
        print(winsvc.service_status())
        return 0
    if not _elevated():
        print("Pokreni kao administrator (Windows) / sudo (Linux, macOS).")
        return 1
    cfg = get_config()
    if args.akcija == "install":
        spine = init_spine(cfg.db_path)
        _, port, _, _ = _net_overrides(spine, cfg)
        exe, cmd_args = winsvc.resolve_atlas_cmd()
        roots = [m["path"] for m in folders.list_folders(spine)]
        ok = winsvc.install_service(exe, cmd_args, cfg.data_dir, port, mount_roots=roots)
        return 0 if ok else 1
    return 0 if winsvc.uninstall_service(cfg.data_dir) else 1


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="atlas")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve").set_defaults(func=_cmd_serve)
    _open = sub.add_parser("open")
    _open.add_argument("--print-only", action="store_true", help="samo ispiši URL, ne otvaraj")
    _open.set_defaults(func=_cmd_open)
    _disc = sub.add_parser("discover")
    _disc.add_argument("--timeout", type=float, default=2.0)
    _disc.set_defaults(func=_cmd_discover)
    sub.add_parser("doctor").set_defaults(func=_cmd_doctor)
    sub.add_parser("health").set_defaults(func=_cmd_health)
    sub.add_parser("trust").set_defaults(func=_cmd_trust)
    _setup = sub.add_parser("setup")
    _setup.add_argument("--download-models", action="store_true",
                        help="povuci embedding model (aktivira vektorsku pretragu)")
    _setup.add_argument("--reset", action="store_true",
                        help="obriši setup-stanje i kreni ispočetka")
    _setup.set_defaults(func=_cmd_setup)
    sub.add_parser("models").set_defaults(func=_cmd_models)
    sub.add_parser("daemon").set_defaults(func=_cmd_daemon)
    sub.add_parser("digest").set_defaults(func=_cmd_digest)
    sub.add_parser("eval").set_defaults(func=_cmd_eval)
    sub.add_parser("stats").set_defaults(func=_cmd_stats)

    p_reminders = sub.add_parser("reminders")
    rem_sub = p_reminders.add_subparsers(dest="reminders_cmd")
    p_reminders_add = rem_sub.add_parser("add")
    p_reminders_add.add_argument("text")
    p_reminders_add.add_argument("due")
    rem_sub.add_parser("dump")
    p_reminders.set_defaults(func=_cmd_reminders)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path", nargs="?")
    p_ingest.add_argument("--imap", action="store_true")
    p_ingest.set_defaults(func=_cmd_ingest)

    sub.add_parser("backup").set_defaults(func=_cmd_backup)
    p_restore = sub.add_parser("restore")
    p_restore.add_argument("path", help="putanja do .db kopije (server mora biti zaustavljen)")
    p_restore.set_defaults(func=_cmd_restore)

    p_auth = sub.add_parser("auth")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd")
    p_auth_add = auth_sub.add_parser("add")
    p_auth_add.add_argument("user")
    p_auth.set_defaults(func=_cmd_auth)

    p_browser = sub.add_parser("browser")
    p_browser.add_argument("sub")
    p_browser.set_defaults(func=_cmd_browser)

    p_watch = sub.add_parser("watch")
    p_watch.add_subparsers(dest="watch_cmd").add_parser("run")
    p_watch.set_defaults(func=_cmd_watch)

    p_ocr = sub.add_parser("ocr")
    p_ocr.add_argument("path")
    p_ocr.set_defaults(func=_cmd_ocr)

    p_servis = sub.add_parser("servis")
    p_servis.add_argument("akcija", choices=["install", "uninstall", "status"])
    p_servis.set_defaults(func=_cmd_servis)

    return p


def main(argv=None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 2

    if not getattr(args, "cmd", None):
        parser.print_help()
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
