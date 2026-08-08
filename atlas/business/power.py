"""Faza 4: napajanje (UPS/NUT) — config + stroj stanja + gašenje redom.

Ovaj modul drži config napajanja (T1) i, u T2, stroj stanja koji na održivom
nestanku struje gasi uređaje po `caps.shutdown_order` iz faze 2. Gašenje je
DESTRUKTIVNO: default `armed=False` (samo alarm), izvršenje tek kad korisnik
izričito naoruža.
"""
from atlas.core import lan

_MODULE = "napajanje"

_DEFAULTS = {
    "enabled": False,
    "nut_host": "",
    "nut_port": 3493,
    "ups_name": "ups",
    "on_battery_seconds": 120,
    "armed": False,  # nikad auto-gašenje dok korisnik ne uključi
}
_BOOL_FIELDS = ("enabled", "armed")
_INT_FIELDS = ("nut_port", "on_battery_seconds")


def get_config(spine) -> dict:
    out = dict(_DEFAULTS)
    for key, default in _DEFAULTS.items():
        raw = spine.get_override(_MODULE, key, None)
        if raw is None:
            continue
        if key in _BOOL_FIELDS:
            out[key] = raw in ("1", "true", "True", True)
        elif key in _INT_FIELDS:
            # read boundary drži isti invariant kao save (fail-safe za
            # destruktivnu putanju): pokvaren red -> default, ne procuri
            try:
                val = int(raw)
            except (ValueError, TypeError):
                continue
            if key == "nut_port" and not (1 <= val <= 65535):
                continue
            if key == "on_battery_seconds" and val <= 0:
                continue
            out[key] = val
        else:
            out[key] = raw
    return out


def save_config(spine, **fields) -> dict:
    unknown = set(fields) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"nepoznata polja: {sorted(unknown)}")

    if "nut_host" in fields:
        host = (fields["nut_host"] or "").strip()
        if host:
            try:
                lan.assert_lan_host(host, 0)  # samo LAN UPS/NUT server (anti-SSRF)
            except Exception as e:
                raise ValueError(f"NUT host mora biti na LAN-u: {e}") from e
        fields["nut_host"] = host
    if "nut_port" in fields:
        port = int(fields["nut_port"])
        if not (1 <= port <= 65535):
            raise ValueError("port mora biti 1..65535")
        fields["nut_port"] = port
    if "on_battery_seconds" in fields:
        secs = int(fields["on_battery_seconds"])
        if secs <= 0:
            raise ValueError("prag 'na bateriji' mora biti > 0 sekundi")
        fields["on_battery_seconds"] = secs
    if "ups_name" in fields:
        name = (fields["ups_name"] or "").strip()
        if not name:
            raise ValueError("naziv UPS-a je obavezan")
        fields["ups_name"] = name

    for key, val in fields.items():
        stored = "1" if (key in _BOOL_FIELDS and val) else "0" if key in _BOOL_FIELDS else str(val)
        spine.set_override(_MODULE, key, stored)
    return get_config(spine)
