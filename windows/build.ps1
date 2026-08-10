# Sastavi ATLAS jedan-.exe installer (opcija B). Pokreni NA WINDOWSU.
# Preduvjeti: Python 3.11+ u PATH-u, Inno Setup 6 (ISCC.exe u PATH-u).
# Rezultat: windows\Output\atlas-setup.exe (jedan installer, uloga na 1. ekranu).
param([string]$PyEmbedUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip")
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

Write-Host "== 1/3: gradim atlas wheel =="
python -m pip install --upgrade build
python -m build --wheel --outdir windows\dist

Write-Host "== 2/3: embedded Python + pip + atlas u payload =="
$payPy = "windows\payload\python"
Remove-Item $payPy -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $payPy | Out-Null
Invoke-WebRequest $PyEmbedUrl -OutFile windows\py.zip
Expand-Archive windows\py.zip -DestinationPath $payPy -Force
# omogući `import site` u embed distribuciji (inače pip ne radi)
$pth = Get-ChildItem "$payPy\python*._pth" | Select-Object -First 1
(Get-Content $pth.FullName) -replace '#\s*import site', 'import site' | Set-Content $pth.FullName
Invoke-WebRequest https://bootstrap.pypa.io/get-pip.py -OutFile "$payPy\get-pip.py"
& "$payPy\python.exe" "$payPy\get-pip.py"
$whl = (Get-ChildItem windows\dist\atlas-*.whl | Select-Object -First 1).FullName
# [full] povlači OCR/Excel/mail ekstre; makni ako želiš minimalni server
& "$payPy\python.exe" -m pip install "$whl[full]"

Write-Host "== 3/3: kompajliram installer (ISCC) =="
ISCC windows\atlas-setup.iss
Write-Host "Gotovo -> windows\Output\atlas-setup.exe"
