"""Wake-on-LAN: magic packet (6x0xFF + 16xMAC) over UDP broadcast. On startup the
server wakes the workstations in order (reverse of shutdown). No dependencies."""
import re
import socket

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2})([:-]?)(?:[0-9A-Fa-f]{2}\2){4}[0-9A-Fa-f]{2}$")


def _mac_bytes(mac: str) -> bytes:
    if not mac or not _MAC_RE.match(mac.strip()):
        raise ValueError(f"neispravan MAC: {mac!r}")
    hexonly = re.sub(r"[:-]", "", mac.strip())
    return bytes.fromhex(hexonly)


def magic_packet(mac: str) -> bytes:
    return b"\xff" * 6 + _mac_bytes(mac) * 16


def send(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    pkt = magic_packet(mac)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))


def wake_fleet(devices_list: list[dict], sender=None) -> list[str]:
    """Send a magic packet to every device with a MAC. Return the names of the woken.
    `sender` is injectable for tests (default: real UDP broadcast)."""
    sender = sender or (lambda pkt: _broadcast(pkt))
    woken = []
    for d in devices_list:
        mac = d.get("mac")
        if not mac:
            continue
        try:
            sender(magic_packet(mac))
            woken.append(d["name"])
        except (ValueError, OSError):
            continue  # invalid MAC / network — skip, do not break waking the rest
    return woken


def _broadcast(pkt: bytes, broadcast: str = "255.255.255.255", port: int = 9) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(pkt, (broadcast, port))
