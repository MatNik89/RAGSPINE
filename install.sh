#!/usr/bin/env bash
# ATLAS — početni setup za Linux i macOS.
# Pokreni iz korijena repoa:   ./install.sh
# Idempotentno: ponovno pokretanje ne razbija postojeći install.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f pyproject.toml ]; then
  echo "GREŠKA: pokreni iz korijena ATLAS repoa (nema pyproject.toml)." >&2
  exit 1
fi

# --- 1. Python 3.11+ ---
PY=""
for cand in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "GREŠKA: treba Python 3.11+ (nije pronađen). Instaliraj pa ponovi." >&2
  exit 1
fi
echo "✓ Python: $("$PY" --version 2>&1)"

# --- 2. venv ---
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "✓ Kreiran .venv"
elif [ ! -x .venv/bin/python ]; then
  echo "GREŠKA: postojeći .venv je nepotpun/korumpiran (nema .venv/bin/python)." >&2
  echo "  Obriši ga pa ponovi:  rm -rf .venv && ./install.sh" >&2
  exit 1
else
  echo "✓ .venv već postoji — koristim ga"
fi
# shellcheck disable=SC1091
. .venv/bin/activate

# --- 3. instalacija ---
python -m pip install --quiet --upgrade pip
echo "Instaliram ATLAS (.[full]) — može potrajati…"
python -m pip install --quiet -e ".[full]"
echo "✓ Instalirano"

# --- 4. seed + (opcijski) embedding model ---
if ! atlas setup >/dev/null 2>&1; then
  echo "  ⚠ 'atlas setup' (seed baze) nije uspio čist — nastavljam, provjeri kasnije 'atlas doctor'"
fi
if [ "${ATLAS_SKIP_MODEL:-0}" != "1" ]; then
  echo "Povlačim embedding model (jednokratno ~220MB; preskoči s ATLAS_SKIP_MODEL=1)…"
  atlas setup --download-models || echo "  (model preskočen/nedostupan — RAG radi degradirano, nastavljam)"
fi

# --- 5. operater (owner) ---
DATA_DIR="${ATLAS_DATA_DIR:-$HOME/.atlas}"
OWNER="${1:-}"
if [ -z "$OWNER" ]; then
  printf "Korisničko ime operatera (owner) [Enter za preskočiti]: "
  read -r OWNER || OWNER=""
fi
case "$OWNER" in
  -*) echo "GREŠKA: ime operatera ne smije počinjati s '-' ($OWNER)." >&2; exit 1 ;;
esac
if [ -z "$OWNER" ]; then
  echo "  Operater preskočen — kreiraj kasnije:  atlas auth add <ime>"
else
  ERRF="$(mktemp "${TMPDIR:-/tmp}/rs_auth.XXXXXX")"
  # '--' zaustavlja parsanje opcija (npr. ime '-h' inače pokrene help i lažira uspjeh)
  if atlas auth add -- "$OWNER" 2>"$ERRF"; then
    echo "✓ Operater '$OWNER' kreiran"
  elif grep -qiE "UNIQUE|already|postoji" "$ERRF" 2>/dev/null; then
    echo "✓ Operater '$OWNER' već postoji — preskačem"
  else
    echo "  (kreiranje preskočeno: $(tail -1 "$ERRF" 2>/dev/null))"
  fi
  rm -f "$ERRF"
fi

# --- 6. gotovo ---
PORT="${ATLAS_PORT:-8400}"
cat <<EOF

════════════════════════════════════════════
✓ ATLAS spreman.  Podaci: $DATA_DIR  (0700)

Pokreni server:
  . .venv/bin/activate && atlas serve

Pa otvori:  http://127.0.0.1:$PORT/login

Provjera:   atlas doctor
Deploy:     docs/DEPLOY_URED.md (KLIJENTI mapa, uređaji, HTTPS, GDPR)
════════════════════════════════════════════
EOF
