"""Baixa o rsgain 3.8 (win64) e o instala na pasta rsgain/ do projeto.

Por que este script existe: o rsgain.exe e suas DLLs nao sao redistribuidos neste
repositorio publico (sao ~5 MB de binario de terceiros, sob licenca propria).
Quem clonar o projeto roda este script uma vez e fica com o motor no lugar certo.

Uso:  python tools/get_rsgain.py
"""

import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.8"
URL = (
    "https://github.com/complexlogic/rsgain/releases/download/"
    f"v{VERSION}/rsgain-{VERSION}-win64.zip"
)
# Impressao digital do arquivo oficial, conferida no download usado para construir o app.
# Este script baixa um programa que sera EXECUTADO na maquina de quem clonar o repo;
# checar o hash e o que garante que o binario e exatamente o mesmo que foi revisado,
# e nao algo trocado no meio do caminho ou um release substituido depois.
SHA256 = "8923cfe3ac930b7342ceae63839dcb65b450628aa452cb8b0047fd35cb86f640"

DEST = Path(__file__).resolve().parent.parent / "rsgain"


def main() -> None:
    # TemporaryDirectory: o zip e a extracao somem sozinhos, de erro ou de sucesso.
    with tempfile.TemporaryDirectory(prefix="rsgain-") as tmpdir:
        tmp = Path(tmpdir)
        archive = tmp / "rsgain.zip"

        print(f"Downloading rsgain {VERSION}...")
        urllib.request.urlretrieve(URL, archive)

        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != SHA256:
            sys.exit(
                f"Checksum mismatch! Expected {SHA256} but got {actual}. "
                "The download was NOT used."
            )
        print("Checksum OK.")

        print("Extracting...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(tmp)
        shutil.copytree(tmp / f"rsgain-{VERSION}-win64", DEST, dirs_exist_ok=True)

    print("Verifying...")
    subprocess.run([str(DEST / "rsgain.exe"), "--version"], check=True)
    print(f"\nrsgain installed at: {DEST}")


if __name__ == "__main__":
    main()
