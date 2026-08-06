"""Self-signed SAN certifikat za LAN HTTPS. Koristi postojeću `cryptography`
dependenciju. Javni cert (cert.pem) smije se dijeliti klijentima; key.pem NIKAD.
Napomena: ako ured ima AD CS/GPO, preferiraj domenski cert (backlog)."""
import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def _warn_if_san_stale(cert_p: Path, ips: list[str], *, out=print) -> None:
    """Upozori (ne regeneriraj — trust je možda već instaliran na klijentima)
    kad postojeći cert ne pokriva traženi IP (stroj je promijenio adresu)."""
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
    """Generiraj (ili vrati postojeći) cert.pem + key.pem sa SAN unosima."""
    out_dir_p = Path(out_dir)
    out_dir_p.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = out_dir_p / "cert.pem", out_dir_p / "key.pem"
    if cert_p.exists() and key_p.exists():
        _warn_if_san_stale(cert_p, ips, out=out)
        return str(cert_p), str(key_p)

    key = ec.generate_private_key(ec.SECP256R1())
    cn = (hostnames or ips or ["ragspine"])[0]
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
    # Kreiraj key.pem s 0600 dozvolama od početka — bez prozora gdje je dostupan drugima.
    # O_EXCL traži postojeću datoteku; ako se pojavi u međuvremenu, ponytail:
    # pretpostavljamo single-writer (setup wizard/CLI nije paralelno).
    # Ako cert.pem nedostaje, key je siročad od prethodnog crasha → obriši i pokušaj ponovno.
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    for attempt in range(2):
        try:
            fd = os.open(str(key_p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key_bytes)
            break  # Ključ je uspješno kreiran
        except FileExistsError:
            if cert_p.exists():
                # Oba fajla se pojavio između check-a i kreiranja — vrati postojeće.
                return str(cert_p), str(key_p)
            # Siročad: key bez certa od prethodnog crasha. Obriši i pokušaj ponovno.
            if attempt == 0:
                key_p.unlink(missing_ok=True)
                continue
            raise  # Drugi pokušaj neuspješan
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_p), str(key_p)


def fingerprint_sha256(cert_path: str) -> str:
    """SHA256 fingerprint certa, formatiran AA:BB:..."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    raw = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))
