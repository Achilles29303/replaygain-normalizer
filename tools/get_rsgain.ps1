# Baixa o rsgain 3.8 (win64) e o instala em .\rsgain.
#
# Por que este script existe: o rsgain.exe e suas DLLs nao sao redistribuidos neste
# repositorio publico (sao ~5 MB de binario de terceiros, sob licenca propria).
# Quem clonar o projeto roda este script uma vez e fica com o motor no lugar certo.
#
# Uso:  pwsh -NoProfile -File tools\get_rsgain.ps1

$ErrorActionPreference = "Stop"

$version = "3.8"
$url  = "https://github.com/complexlogic/rsgain/releases/download/v$version/rsgain-$version-win64.zip"
# Impressao digital do arquivo oficial, conferida no download usado para construir o app.
# Este script baixa um programa que sera' EXECUTADO na maquina de quem clonar o repo;
# checar o hash e' o que garante que o binario e' exatamente o mesmo que foi revisado,
# e nao algo trocado no meio do caminho ou um release substituido depois.
$sha256 = "8923CFE3AC930B7342CEAE63839DCB65B450628AA452CB8B0047FD35CB86F640"
$root = Split-Path -Parent $PSScriptRoot
$dest = Join-Path $root "rsgain"
$tmp  = Join-Path ([IO.Path]::GetTempPath()) "rsgain-$version.zip"
$ext  = Join-Path ([IO.Path]::GetTempPath()) "rsgain-$version-extract"

Write-Host "Downloading rsgain $version..." -ForegroundColor Cyan
Invoke-WebRequest -Uri $url -OutFile $tmp

$actual = (Get-FileHash $tmp -Algorithm SHA256).Hash
if ($actual -ne $sha256) {
    Remove-Item $tmp -Force
    throw "Checksum mismatch! Expected $sha256 but got $actual. The download was NOT used."
}
Write-Host "Checksum OK." -ForegroundColor Green

Write-Host "Extracting..." -ForegroundColor Cyan
if (Test-Path $ext) { Remove-Item -Recurse -Force $ext }
Expand-Archive -Path $tmp -DestinationPath $ext -Force

New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item -Path (Join-Path $ext "rsgain-$version-win64\*") -Destination $dest -Recurse -Force

Write-Host "Verifying..." -ForegroundColor Cyan
& (Join-Path $dest "rsgain.exe") --version

Write-Host "`nrsgain installed at: $dest" -ForegroundColor Green
