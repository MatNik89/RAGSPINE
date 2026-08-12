"""Self-signed SAN certificate for LAN HTTPS. Uses the existing `cryptography`
dependency. The public cert (cert.pem) may be shared with clients; key.pem NEVER.
Note: if the office has AD CS/GPO, prefer a domain certificate (backlog)."""
import ipaddress
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def friendly_names() -> list[str]:
    """Friendly names for the certificate SAN: hostname, FQDN (a DNS suffix
    like fritz.box) if present, hostname.local (mDNS), always + atlas.local
    (compatibility with already-issued instructions). Pure stdlib, no network;
    any error returns at least ["atlas.local"]."""
    names: list[str] = []
    try:
        host = socket.gethostname().lower()
        if host:
            names.append(host)
            local = f"{host}.local"
            try:
                fqdn = socket.getfqdn().lower()
            except Exception:
                fqdn = ""
            if "." in fqdn and fqdn != local:
                names.append(fqdn)
            names.append(local)
    except Exception:
        pass
    names.append("atlas.local")
    seen: set[str] = set()
    result = []
    for n in names:
        if n and n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _warn_if_san_stale(cert_p: Path, ips: list[str], *, out=print) -> None:
    """Warn (do not regenerate — trust may already be installed on clients)
    when the existing cert does not cover the requested IP (the machine changed address)."""
    try:
        cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        existing = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    except Exception:
        return
    missing = [i for i in ips if i not in existing]
    if missing:
        out(f"⚠ Postojeći certifikat ne pokriva IP {', '.join(missing)} "
            f"(SAN sadrži: {', '.join(sorted(existing)) or 'ništa'}). "
            f"Ako se adresa promijenila, obriši {cert_p.parent} pa ponovi setup za novi certifikat.")


def generate_self_signed(out_dir: str, ips: list[str],
                         hostnames: list[str] | None = None,
                         days: int = 3650, *, out=print) -> tuple[str, str]:
    """Generate (or return the existing) cert.pem + key.pem with SAN entries."""
    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = out_dir_p / "cert.pem", out_dir_p / "key.pem"
    if cert_p.exists() and key_p.exists():
        _warn_if_san_stale(cert_p, ips, out=out)
        return str(cert_p), str(key_p)

    key = ec.generate_private_key(ec.SECP256R1())
    cn = (hostnames or ips or ["atlas"])[0]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    san_entries: list[x509.GeneralName] = [
        x509.DNSName(h) for h in (hostnames or [])
    ] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    # Create key.pem with 0600 permissions from the start — no window where it is readable by others.
    # O_EXCL fails on an existing file; if one appears in the meantime, ponytail:
    # we assume a single writer (the setup wizard/CLI does not run in parallel).
    # If cert.pem is missing, the key is orphaned from a previous crash -> delete it and retry.
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    for attempt in range(2):
        try:
            fd = os.open(str(key_p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key_bytes)
            break  # Key created successfully
        except FileExistsError:
            if cert_p.exists():
                # Both files appeared between the check and creation — return the existing ones.
                return str(cert_p), str(key_p)
            # Orphan: a key without a cert from a previous crash. Delete it and retry.
            if attempt == 0:
                key_p.unlink(missing_ok=True)
                continue
            raise  # Second attempt failed
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_p), str(key_p)


def fingerprint_sha256(cert_path: str) -> str:
    """SHA256 fingerprint of the cert, formatted AA:BB:..."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    raw = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))


def san_dns_names(cert_path: str) -> list[str]:
    """DNS names from the SAN extension of the EXISTING cert (same x509 parsing as
    _warn_if_san_stale). [] on any error (missing/unreadable/no SAN)
    — the caller then falls back to the IP."""
    try:
        cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return list(san.get_values_for_type(x509.DNSName))
    except Exception:
        return []


def best_display_host(names: list[str], fallback: str) -> str:
    """Best name to display to clients: a real FQDN (has a dot, not
    atlas.local/.local), then hostname.local, then fallback (usually the IP).
    Single implementation — wizard._best_name and bootstrap_http.best_display_host
    are thin aliases over this one."""
    for n in names:
        if "." in n and n != "atlas.local" and not n.endswith(".local"):
            return n
    for n in names:
        if n.endswith(".local") and n != "atlas.local":
            return n
    return fallback


def verified_display_host(cert_path: str, names: list[str], fallback: str) -> str:
    """best_display_host, but only over names the SAN of the EXISTING cert
    actually covers (finding: an old install may have cert=[atlas.local, IP]
    while the instructions offer a newer name from friendly_names() -> browser
    warning even after installing the cert). Empty intersection (or unreadable cert) -> fallback (IP)."""
    san = san_dns_names(cert_path)
    candidates = [n for n in names if n in san]
    return best_display_host(candidates, candidates[0] if candidates else fallback)
