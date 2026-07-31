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


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ragspine")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("serve").set_defaults(func=_cmd_serve)
    for name in ("setup", "doctor", "health", "eval", "stats", "reminders"):
        sub.add_parser(name).set_defaults(func=_stub)

    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("path")
    p_ingest.set_defaults(func=_stub)

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
    p_browser.set_defaults(func=_stub)

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
