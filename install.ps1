# ATLAS — pocetni setup za Windows (PowerShell 5.1+ i 7+).
# Pokreni iz korijena repoa:   .\install.ps1
# Ako PowerShell blokira skriptu:  powershell -ExecutionPolicy Bypass -File .\install.ps1
# Idempotentno: ponovno pokretanje ne razbija postojeci install.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Native programi (py/pip/atlas) NE bacaju exception na non-zero exit ni uz
# ErrorActionPreference=Stop u Win PowerShell 5.1 — zato eksplicitno provjeravamo
# $LASTEXITCODE nakon svakog kriticnog poziva.
function Assert-Ok($msg) {
  if ($LASTEXITCODE -ne 0) { Write-Error "GRESKA: $msg (izlazni kod $LASTEXITCODE)"; exit 1 }
}

if (-not (Test-Path "pyproject.toml")) {
  Write-Error "Pokreni iz korijena ATLAS repoa (nema pyproject.toml)."
  exit 1
}

# --- 1. Python 3.11+ ---
function Find-Python {
  $cands = @(
    @{ exe = "py";      args = @("-3.13") },
    @{ exe = "py";      args = @("-3.12") },
    @{ exe = "py";      args = @("-3.11") },
    @{ exe = "py";      args = @("-3") },
    @{ exe = "python";  args = @() },
    @{ exe = "python3"; args = @() }
  )
  # PS 5.1 + EAP=Stop: stderr nativnog programa kroz 2>$null postane
  # TERMINIRAJUCA greska (py.exe "No suitable Python runtime found" za
  # neinstaliranu verziju ubije petlju umjesto da proba sljedeceg kandidata)
  # — zato probe vrtimo pod EAP=Continue (E2E nalaz, stroj Nick).
  $eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try {
    foreach ($c in $cands) {
      if (Get-Command $c.exe -ErrorAction SilentlyContinue) {
        & $c.exe @($c.args) -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $c }
      }
    }
  } finally { $ErrorActionPreference = $eap }
  return $null
}
$py = Find-Python
if ($null -eq $py) {
  Write-Error "Treba Python 3.11+ (nije pronaden). Instaliraj s python.org pa ponovi."
  exit 1
}
$pyExe = $py.exe; $pyArgs = $py.args
Write-Host ("✓ Python: " + (& $pyExe @pyArgs --version 2>&1)); Assert-Ok "provjera Pythona"

# --- 2. venv ---
if (-not (Test-Path ".venv")) {
  & $pyExe @pyArgs -m venv .venv; Assert-Ok "kreiranje venv-a"
  Write-Host "✓ Kreiran .venv"
} else {
  Write-Host "✓ .venv vec postoji — koristim ga"
}
$venvPy   = Join-Path ".venv" "Scripts\python.exe"
$atlas = Join-Path ".venv" "Scripts\atlas.exe"
if (-not (Test-Path $venvPy)) { Write-Error "GRESKA: .venv je nepotpun ($venvPy nedostaje). Obrisi .venv i ponovi."; exit 1 }

# --- 3. instalacija ---
& $venvPy -m pip install --quiet --upgrade pip; Assert-Ok "nadogradnja pip-a"
Write-Host "Instaliram ATLAS (.[full]) — moze potrajati…"
& $venvPy -m pip install --quiet -e ".[full]"; Assert-Ok "instalacija paketa"
Write-Host "✓ Instalirano"

# --- 4. (opcijski) embedding model ---
# operatera i seed baze kreira "atlas setup" carobnjak (stranica 2) — install.ps1
# ga ne poziva headless, samo priprema okolinu.
if ($env:ATLAS_SKIP_MODEL -ne "1") {
  Write-Host "Povlacim embedding model (jednokratno ~220MB; preskoci s `$env:ATLAS_SKIP_MODEL=1)…"
  & $atlas setup --download-models
  if ($LASTEXITCODE -ne 0) { Write-Host "  (model preskocen/nedostupan — RAG radi degradirano, nastavljam)" }
}

# --- 5. gotovo ---
$dataDir = if ($env:ATLAS_DATA_DIR) { $env:ATLAS_DATA_DIR } else { Join-Path $env:USERPROFILE ".atlas" }
Write-Host ""
Write-Host "════════════════════════════════════════════"
Write-Host "✓ Okolina spremna.  Podaci: $dataDir"
Write-Host ""
Write-Host "Dovrši postavljanje čarobnjakom (preduvjeti, operater, model, HTTPS, mape):"
Write-Host "  .\.venv\Scripts\atlas.exe setup"
Write-Host ""
Write-Host "Provjera:   .\.venv\Scripts\atlas.exe doctor"
Write-Host "Deploy:     docs\DEPLOY_URED.md (KLIJENTI mapa, uredaji, HTTPS, GDPR)"
Write-Host "════════════════════════════════════════════"
