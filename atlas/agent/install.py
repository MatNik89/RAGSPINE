"""Bootstrap instalacija atlas-agenta na radnikovom stroju: zapiši lokalni
config (token + server_url + prazna program_map) i registriraj autostart U
SESIJI (Windows Task Scheduler pri prijavi / systemd --user). Idempotentno; NE
dira sustavske servise.

Pokretanje na radnom stroju:
    python -m atlas.agent.install --server https://SERVER:8443 --token <TOKEN>
"""
import argparse
import json
import os
import subprocess
import sys

_UNIT_NAME = "atlas-agent.service"


def write_config(path: str, server_url: str, token: str, program_map: dict | None = None) -> dict:
    if not server_url.lower().startswith("https://"):
        raise ValueError("server_url mora biti https:// (token ne ide cleartextom)")
    cfg = {"server_url": server_url, "token": token, "program_map": program_map or {}}
    data = json.dumps(cfg, ensure_ascii=False, indent=2).encode()
    # Atomarno stvori s 0600 PRIJE pisanja tokena i ne slijedi symlink — inače
    # postoji prozor u kojem je token svima čitljiv (Codex T5 nalaz).
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)  # ako je datoteka već postojala s drugim modeom
    except OSError:
        pass
    return cfg


def _systemd_unit_text(python_exe: str, config_path: str) -> str:
    return (
        "[Unit]\nDescription=ATLAS radnički agent\n\n"
        "[Service]\n"
        f"ExecStart={python_exe} -m atlas.agent {config_path}\n"
        "Restart=always\nRestartSec=10\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def write_systemd_unit(python_exe: str, config_path: str, unit_dir: str | None = None) -> str:
    unit_dir = unit_dir or os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    path = os.path.join(unit_dir, _UNIT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_systemd_unit_text(python_exe, config_path))
    return path


def autostart_argv(python_exe: str, config_path: str, platform: str | None = None) -> list[str]:
    """Argv za registraciju autostarta (nikad shell string)."""
    platform = platform or ("win" if sys.platform.startswith("win") else "linux")
    if platform == "win":
        run = f'"{python_exe}" -m atlas.agent "{config_path}"'
        return ["schtasks", "/Create", "/TN", "AtlasAgent", "/SC", "ONLOGON",
                "/TR", run, "/F"]
    return ["systemctl", "--user", "enable", "--now", _UNIT_NAME]


def register_autostart(python_exe: str, config_path: str, runner=None,
                       platform: str | None = None, unit_dir: str | None = None) -> None:
    platform = platform or ("win" if sys.platform.startswith("win") else "linux")
    if platform != "win":
        # Linux: unit MORA postojati prije enable (inače systemctl padne) i mora
        # referencirati OVAJ python + config (Codex T5 nalaz).
        write_systemd_unit(python_exe, config_path, unit_dir=unit_dir)
    runner = runner or (lambda argv: subprocess.run(argv, check=True))
    runner(autostart_argv(python_exe, config_path, platform=platform))


def install(server_url: str, token: str, config_path: str | None = None,
            python_exe: str | None = None, program_map: dict | None = None,
            runner=None, platform: str | None = None, unit_dir: str | None = None) -> str:
    config_path = config_path or os.path.expanduser("~/.atlas-agent.json")
    python_exe = python_exe or sys.executable
    write_config(config_path, server_url, token, program_map)
    register_autostart(python_exe, config_path, runner=runner, platform=platform,
                       unit_dir=unit_dir)
    return config_path


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="atlas.agent.install",
                                 description="Postavi atlas-agenta u sesiju.")
    ap.add_argument("--server", required=True, help="https://SERVER:8443")
    ap.add_argument("--token", required=True, help="token uređaja iz Postavke→Uređaji")
    ap.add_argument("--config", default=None, help="put do configa (default ~/.atlas-agent.json)")
    ap.add_argument("--no-autostart", action="store_true", help="samo zapiši config")
    args = ap.parse_args(argv)
    path = os.path.expanduser(args.config) if args.config else os.path.expanduser("~/.atlas-agent.json")
    write_config(path, args.server, args.token)
    if not args.no_autostart:
        register_autostart(sys.executable, path)
    print(f"Config zapisan: {path}")


if __name__ == "__main__":  # pragma: no cover — ulazna točka na radnikovom stroju
    main()
