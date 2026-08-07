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

# --- 0. uv (Astral) — brži Python+venv+paketi; ako ne uspije, pip put ispod NETAKNUT ---
# ATLAS_NO_UV=1 preskače uv u cijelosti (izlaz za probleme s uv-om).
UV_BIN=""
if [ "${ATLAS_NO_UV:-0}" != "1" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="uv"
  else
    export UV_INSTALL_DIR="$HOME/.local/bin"
    curl -LsSf https://astral.sh/uv/install.sh 2>/dev/null | sh >/dev/null 2>&1 || true
    export PATH="$UV_INSTALL_DIR:$PATH"
    command -v uv >/dev/null 2>&1 && UV_BIN="uv"
  fi
fi

if [ -n "$UV_BIN" ]; then
  # --- 1-3. uv put: Python 3.12 + venv + paketi u jednom potezu ---
  echo "✓ uv pronađen: $(uv --version)"
  if [ ! -d .venv ]; then
    uv venv .venv --python 3.12
    echo "✓ Kreiran .venv (uv)"
  else
    echo "✓ .venv već postoji — koristim ga"
  fi
  echo "Instaliram ATLAS (.[full]) preko uv-a — može potrajati…"
  uv pip install --python .venv/bin/python --quiet -e ".[full]"
  echo "✓ Instalirano (uv)"
else
  echo "uv nije dostupan — koristim klasični pip (sporije)"

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
fi

# --- 4. (opcijski) embedding model ---
# operatera i seed baze kreira "atlas setup" čarobnjak — install.sh ga ne
# poziva headless, samo priprema okolinu.
if [ "${ATLAS_SKIP_MODEL:-0}" != "1" ]; then
  echo "Povlačim embedding model (jednokratno ~220MB; preskoči s ATLAS_SKIP_MODEL=1)…"
  .venv/bin/atlas setup --download-models || echo "  (model preskočen/nedostupan — RAG radi degradirano, nastavljam)"
fi

# --- 5. gotovo ---
DATA_DIR="${ATLAS_DATA_DIR:-$HOME/.atlas}"
cat <<EOF

════════════════════════════════════════════
✓ Okolina spremna.  Podaci: $DATA_DIR  (0700)

Dovrši postavljanje čarobnjakom (preduvjeti, operater, model, HTTPS, mape):
  .venv/bin/atlas setup

Provjera:   .venv/bin/atlas doctor
Deploy:     docs/DEPLOY_URED.md (KLIJENTI mapa, uređaji, HTTPS, GDPR)
════════════════════════════════════════════
EOF
