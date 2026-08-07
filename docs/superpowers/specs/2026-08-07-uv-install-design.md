# uv u install skriptama — dizajn (2026-08-07)

## Cilj

Instalacija ureda brža i otpornija: uv (Astral) kao primarni put za
Python + venv + pakete u install.ps1 i install.sh, s POSTOJEĆIM
pip+venv putem kao netaknutim fallbackom. Usput: install.sh uskladba s
wizardom (propuštena u tui-facelift-2 — samo ps1 je bio usklađen).

## Odluke

- uv se NE dodaje u pyproject (nije runtime ovisnost) — samo install
  skripte.
- Fallback je obavezan: bez mreže / winget nema uv / curl pada → stari
  pip put radi kao dosad. Skripta NIKAD ne smije biti u gorem stanju
  nego prije uv-a.
- uv upravlja i Pythonom: `uv venv .venv --python 3.12` sam skine
  CPython ako ga nema — na svježem stroju ubija Find-Python probing
  (E2E zamka s py launcherom).

## install.ps1

1. Nova sekcija 0: nađi uv (`Get-Command uv`); nema →
   `winget install --exact --id astral-sh.uv --accept-...` (tiho, bez
   pada skripte ako winget ne uspije) → refresh PATH-a u procesu
   (registry User+Machine, kao winpath logika — inline u ps1) → ponovo
   Get-Command uv.
2. Ako uv POSTOJI: preskoči Find-Python; `uv venv .venv --python 3.12`
   (ako .venv ne postoji); `uv pip install --python .venv\Scripts\python.exe
   --quiet -e ".[full]"`. Assert-Ok nakon svakog.
3. Ako uv NE postoji: postojeći put (Find-Python → venv → pip) NETAKNUT,
   uz poruku "uv nije dostupan — koristim klasični pip (sporije)".
4. Embedding sekcija i završni ispis nepromijenjeni.

## install.sh

1. Ista sekcija 0: `command -v uv`; nema → pokušaj
   `curl -LsSf https://astral.sh/uv/install.sh | sh` (env
   UV_INSTALL_DIR="$HOME/.local/bin"; PATH+=to); i dalje nema → fallback.
   ATLAS_NO_UV=1 env preskače uv u cijelosti (izlaz za probleme).
2. uv put: `uv venv .venv --python 3.12` + `uv pip install --python
   .venv/bin/python --quiet -e ".[full]"`.
3. Fallback: postojeći Python-probe + venv + pip NETAKNUT.
4. USKLADBA s wizardom (kao ps1 u tui-facelift-2): obriši headless
   `atlas setup` seed poziv (sad je to interaktivni wizard — non-TTY
   poziv je besmislen) i CIJELU operater sekciju; završni ispis →
   uputa na `.venv/bin/atlas setup` (wizard), bez serve/login URL-a.
   ATLAS_SKIP_MODEL/embedding sekcija ostaje.

## Provjere (nema pytest pokrivenosti za shell — ručne provjere u grani)

- `bash -n install.sh` (syntax) + shellcheck ako postoji.
- `pwsh -NoProfile -Command "[void][ScriptBlock]::Create((Get-Content -Raw install.ps1))"`
  (syntax parse; pwsh postoji na razvojnom stroju).
- install.ps1 BOM (ef bb bf) MORA ostati.
- Smoke na Piju: ATLAS_NO_UV=0 put s već instaliranim uv-om (uv postoji
  na stroju) u tmp klonu — `uv venv` + `uv pip install -e .` bez [full]
  (brzo), provjeri da `atlas --help` radi. Ako uv nije na PATH-u stroja,
  preskoči smoke i zabilježi.
- Puni pytest suite (ništa Python koda se ne mijenja — očekuj isti broj).

## Ne-ciljevi

- pyproject/uv.lock tooling (uv lock, uv sync) — projekt nije uv-managed,
  samo installer koristi uv kao brži pip+venv.
- Wizard/preflight izmjene.
- README preporuke (usput jedan redak ako spominje pip korake).
