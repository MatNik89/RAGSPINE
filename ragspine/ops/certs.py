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


def generate_self_signed(out_dir: str, ips: list[str],
                         hostnames: list[str] | None = None,
                         days: int = 3650) -> tuple[str, str]:
    """Generiraj (ili vrati postojeći) cert.pem + key.pem sa SAN unosima."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = out / "cert.pem", out / "key.pem"
    if cert_p.exists() and key_p.exists():
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
    key_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(key_p, 0o600)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_p), str(key_p)


def fingerprint_sha256(cert_path: str) -> str:
    """SHA256 fingerprint certa, formatiran AA:BB:..."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    raw = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))
