# RAGSPINE — pocetni setup za Windows (PowerShell 5.1+ i 7+).
# Pokreni iz korijena repoa:   .\install.ps1
# Ako PowerShell blokira skriptu:  powershell -ExecutionPolicy Bypass -File .\install.ps1
# Idempotentno: ponovno pokretanje ne razbija postojeci install.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# Native programi (py/pip/ragspine) NE bacaju exception na non-zero exit ni uz
# ErrorActionPreference=Stop u Win PowerShell 5.1 — zato eksplicitno provjeravamo
# $LASTEXITCODE nakon svakog kriticnog poziva.
function Assert-Ok($msg) {
  if ($LASTEXITCODE -ne 0) { Write-Error "GRESKA: $msg (izlazni kod $LASTEXITCODE)"; exit 1 }
}

if (-not (Test-Path "pyproject.toml")) {
  Write-Error "Pokreni iz korijena RAGSPINE repoa (nema pyproject.toml)."
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
  foreach ($c in $cands) {
    if (Get-Command $c.exe -ErrorAction SilentlyContinue) {
      & $c.exe @($c.args) -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)" 2>$null
      if ($LASTEXITCODE -eq 0) { return $c }
    }
  }
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
$ragspine = Join-Path ".venv" "Scripts\ragspine.exe"
if (-not (Test-Path $venvPy)) { Write-Error "GRESKA: .venv je nepotpun ($venvPy nedostaje). Obrisi .venv i ponovi."; exit 1 }

# --- 3. instalacija ---
& $venvPy -m pip install --quiet --upgrade pip; Assert-Ok "nadogradnja pip-a"
Write-Host "Instaliram RAGSPINE (.[full]) — moze potrajati…"
& $venvPy -m pip install --quiet -e ".[full]"; Assert-Ok "instalacija paketa"
Write-Host "✓ Instalirano"

# --- 4. seed + (opcijski) embedding model ---
& $ragspine setup 2>$null | Out-Null   # seed baze; ignoriramo izlazni kod (idempotentno)
if ($env:RAGSPINE_SKIP_MODEL -ne "1") {
  Write-Host "Povlacim embedding model (jednokratno ~220MB; preskoci s `$env:RAGSPINE_SKIP_MODEL=1)…"
  & $ragspine setup --download-models
  if ($LASTEXITCODE -ne 0) { Write-Host "  (model preskocen/nedostupan — RAG radi degradirano, nastavljam)" }
}

# --- 5. operater (owner) ---
$owner = if ($args.Count -ge 1) { $args[0] } else { Read-Host "Korisnicko ime operatera (owner) [Enter za preskociti]" }
if ([string]::IsNullOrWhiteSpace($owner)) {
  Write-Host "  Operater preskocen — kreiraj kasnije: ragspine auth add <ime>"
} elseif ($owner.StartsWith("-")) {
  Write-Error "Ime operatera ne smije pocinjati s '-' ($owner)."; exit 1
} else {
  # '--' zaustavlja parsanje opcija (npr. ime '-h' inace pokrene help i lazira uspjeh)
  & $ragspine auth add -- $owner
  if ($LASTEXITCODE -eq 0) { Write-Host "✓ Operater '$owner' kreiran" }
  else { Write-Host "  (operater '$owner' vec postoji ili nije kreiran — provjeri: ragspine auth add <ime>)" }
}

# --- 6. gotovo ---
$dataDir = if ($env:RAGSPINE_DATA_DIR) { $env:RAGSPINE_DATA_DIR } else { Join-Path $env:USERPROFILE ".ragspine" }
$port = if ($env:RAGSPINE_PORT) { $env:RAGSPINE_PORT } else { "8400" }
Write-Host ""
Write-Host "════════════════════════════════════════════"
Write-Host "✓ RAGSPINE spreman.  Podaci: $dataDir"
Write-Host ""
Write-Host "Pokreni server:"
Write-Host "  .\.venv\Scripts\ragspine.exe serve"
Write-Host ""
Write-Host "Pa otvori:  http://127.0.0.1:$port/login"
Write-Host ""
Write-Host "Provjera:   .\.venv\Scripts\ragspine.exe doctor"
Write-Host "Deploy:     docs\DEPLOY_URED.md (KLIJENTI mapa, uredaji, HTTPS, GDPR)"
Write-Host "════════════════════════════════════════════"
