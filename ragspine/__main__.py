"""RAGSPINE CLI entrypoint: python -m ragspine <cmd>."""
import argparse
import getpass
import os
import sys


def _stub(args) -> int:
    print("nije još implementirano")
    return 2


def _cmd_serve(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.web.api import create_app
    import uvicorn

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    uvicorn.run(create_app(spine, cfg), host=cfg.host, port=cfg.port)
    return 0


def _cmd_ingest(args) -> int:
    if not args.imap:
        return _stub(args)
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.docs.imap_fetch import fetch_new

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    result = fetch_new(spine, cfg)
    print(f"fetched={result['fetched']} attachments={len(result['attachments'])}")
    return 0


def _cmd_doctor(args) -> int:
    from ragspine.config import get_config
    from ragspine.ops import doctor

    cfg = get_config()
    results = doctor.run(cfg)
    print(doctor.format_report(results))
    return 0 if all(r["ok"] for r in results) else 1


def _cmd_health(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.ops import health

    cfg = get_config()
    spine = init_spine(cfg.db_path)
    print(health.check(spine, cfg))
    return 0


def _cmd_auth(args) -> int:
    if getattr(args, "auth_cmd", None) != "add":
        return _stub(args)
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.web.deps import add_user

    pw = os.environ.get("RAGSPINE_PASS") or getpass.getpass()
    spine = init_spine(get_config().db_path)
    add_user(spine, args.user, pw)
    print(args.user)
    return 0


def _cmd_setup(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.ops import setup

    cfg = get_config()
    init_spine(cfg.db_path)
    print(setup.run(cfg))
    return 0


def _cmd_eval(args) -> int:
    from ragspine.config import get_config
    from ragspine.ops import evalrun

    report = evalrun.run(get_config())
    print(report)
    return 0 if report["pass"] else 1


def _cmd_stats(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine

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


def _cmd_reminders(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine

    spine = init_spine(get_config().db_path)
    if getattr(args, "reminders_cmd", None) == "add":
        user = os.environ.get("RAGSPINE_USER", "sustav")
        with spine.write() as c:
            c.execute(
                "INSERT INTO reminders(user, body, due) VALUES(?,?,?)",
                (user, args.text, args.due),
            )
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
    from ragspine.browser.bridge import Bridge

    print(f"pending={Bridge().pending()}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ragspine")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve").set_defaults(func=_cmd_serve)
    sub.add_parser("doctor").set_defaults(func=_cmd_doctor)
    sub.add_parser("health").set_defaults(func=_cmd_health)
    sub.add_parser("setup").set_defaults(func=_cmd_setup)
    sub.add_parser("eval").set_defaults(func=_cmd_eval)
    sub.add_parser("stats").set_defaults(func=_cmd_stats)

    p_reminders = sub.add_parser("reminders")
    rem_sub = p_reminders.add_subparsers(dest="reminders_cmd")
    p_reminders_add = rem_sub.add_parser("add")
    p_reminders_add.add_argument("text")
    p_reminders_add.add_argument("due")
    p_reminders.set_defaults(func=_cmd_reminders)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path", nargs="?")
    p_ingest.add_argument("--imap", action="store_true")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_forget = sub.add_parser("forget")
    p_forget.add_argument("term")
    p_forget.set_defaults(func=_stub)

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
    p_watch.set_defaults(func=_stub)

    p_ocr = sub.add_parser("ocr")
    p_ocr.add_argument("path")
    p_ocr.set_defaults(func=_stub)

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
