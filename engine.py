"""Motor do ReplayGain Normalizer: descoberta de arquivos, chamadas ao rsgain e parsing.

Este modulo NAO importa tkinter de proposito. Sem dependencia de interface, ele pode
ser executado e testado direto no terminal (`python engine.py <pasta>`), que e' a
unica forma honesta de provar que o parsing funciona antes de existir qualquer tela.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from queue import Queue
from typing import NamedTuple

# Unico lugar do projeto que define quais extensoes entram no scan. O rsgain aceita
# muito mais (opus, m4a, wav, wavpack...); ampliar o app significa mexer so' aqui.
SUPPORTED_EXTENSIONS = {".mp3", ".flac"}

# O rsgain imprime o simbolo de infinito (U+221E) quando o audio e' silencio digital:
# nao existe "loudness" de silencio, entao nao existe numero para mostrar.
INFINITY = "∞"

# O rsgain colore suas mensagens de erro com sequencias ANSI (ex.: "\x1b[1;31mERROR\x1b[0m").
# O terminal as interpreta como cor, mas a nossa tabela as mostraria como lixo literal.
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


class Result(NamedTuple):
    """Uma linha de resultado por arquivo processado.

    Todos os campos numericos sao opcionais porque o rsgain pode devolver "-inf"
    (silencio digital) e porque um arquivo que falhou nao produz numero nenhum —
    nesse caso apenas `error` vem preenchido.
    """

    path: Path                # caminho completo do arquivo processado
    loudness: float | None    # None quando o rsgain imprimiu "-inf"
    gain: float | None
    peak: float | None
    peak_db: float | None
    peak_type: str            # "Sample" ou "True"
    clipped: bool             # True quando a coluna 6 e' "Y"
    error: str | None         # mensagem quando algo falhou; None em caso de sucesso


def base_dir() -> Path:
    """Devolve a pasta onde os recursos do app vivem, tanto em script quanto em .exe.

    Quando o PyInstaller monta o executavel --onefile, ele descompacta tudo numa pasta
    temporaria e guarda o caminho dela em `sys._MEIPASS`. Rodando como script comum
    esse atributo nao existe, e a referencia correta e' a pasta deste arquivo .py.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent


def rsgain_path() -> Path:
    """Caminho do rsgain.exe empacotado, validado antes de qualquer subprocess.

    Falhar aqui com uma mensagem legivel e' muito melhor do que deixar o
    `subprocess.run` estourar um FileNotFoundError cru la' na frente, quando o
    usuario ja' clicou em Normalize e nao faz ideia do que deu errado.
    """
    exe = base_dir() / "rsgain" / "rsgain.exe"
    if not exe.is_file():
        raise FileNotFoundError(
            f"rsgain.exe não encontrado em: {exe}\n"
            "O motor de áudio está faltando nesta instalação."
        )
    return exe


def find_songs(folder: Path) -> list[Path]:
    """Lista recursivamente as musicas suportadas dentro de `folder`, em ordem alfabetica.

    A comparacao da extensao e' em minusculas porque o Windows guarda ".MP3" e ".mp3"
    como nomes distintos na listagem, ainda que trate os dois como o mesmo arquivo.
    """
    if not folder.is_dir():
        return []
    songs = [
        p for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    # Ordenar pelo caminho em minusculas deixa a lista igual a' que o Explorer mostra.
    return sorted(songs, key=lambda p: str(p).lower())


def _run(args: list[str]) -> tuple[int, str, str]:
    """Executa o rsgain e devolve (codigo de saida, stdout, stderr).

    Este e' o UNICO ponto do projeto que toca em `subprocess`. Concentrar isso num
    lugar so' garante que os quatro ajustes abaixo nunca sejam esquecidos numa chamada.
    """
    completed = subprocess.run(
        args,
        capture_output=True,
        encoding="utf-8",        # o rsgain emite "-inf" em UTF-8; sem isso a linha quebra
        errors="replace",        # nunca deixa um byte estranho derrubar o app
        stdin=subprocess.DEVNULL,  # o app nao tem console: sem isso o rsgain herdaria um stdin invalido
        # Sem esta flag, cada arquivo processado faz piscar uma janela preta de console
        # na frente da interface — o app parece travado e o efeito e' horrivel.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _to_float(cell: str) -> float | None:
    """Converte uma celula numerica do rsgain em float, ou None quando e' infinito.

    Silencio digital vira "-inf" (ou "inf"), que nao e' um numero exibivel na tabela.
    Devolver None deixa a decisao de como mostrar isso para a interface.
    """
    cell = cell.strip()
    if not cell or INFINITY in cell:
        return None
    try:
        return float(cell)
    except ValueError:
        return None


def _parse_row(stdout: str, path: Path) -> Result:
    """Transforma a saida tab-delimitada do rsgain no `Result` de UM arquivo.

    A saida tem duas linhas: um cabecalho fixo e a linha de dados. Procuramos a
    primeira linha que NAO seja o cabecalho, porque a coluna 0 traz so' o nome do
    arquivo (basename) — por isso o caminho completo vem do parametro `path`, nunca
    da saida do programa.
    """
    data_row: list[str] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        cells = line.split("\t")
        # O cabecalho tem um espaco a mais em " Peak (dB)"; comparar sem .strip()
        # faria esta deteccao falhar silenciosamente e o cabecalho viraria "resultado".
        if cells[0].strip() == "Filename":
            continue
        data_row = cells
        break

    if data_row is None or len(data_row) < 7:
        return Result(path, None, None, None, None, "", False,
                      "O rsgain não retornou dados para este arquivo")

    return Result(
        path=path,
        loudness=_to_float(data_row[1]),
        gain=_to_float(data_row[2]),
        peak=_to_float(data_row[3]),
        peak_db=_to_float(data_row[4]),
        peak_type=data_row[5].strip(),
        clipped=data_row[6].strip().upper().startswith("Y"),
        error=None,
    )


def _execute(args: list[str], path: Path) -> Result:
    """Roda o rsgain e converte tanto o sucesso quanto a falha num unico `Result`.

    Um arquivo corrompido ou travado por outro programa nao pode derrubar o lote
    inteiro: o erro vira apenas mais uma linha na tabela e o loop segue em frente.
    """
    try:
        code, out, err = _run(args)
    except OSError as exc:
        # OSError cobre o caso do .exe sumir ou o Windows recusar a execucao.
        return Result(path, None, None, None, None, "", False, str(exc))

    if code != 0:
        message = err.strip() or out.strip() or f"O rsgain terminou com código {code}"
        # Limpa as cores ANSI e achata em uma linha: a tabela so' tem uma celula.
        message = ANSI_ESCAPE.sub("", message)
        return Result(path, None, None, None, None, "", False,
                      " ".join(message.split()))

    return _parse_row(out, path)


def normalize_file(path: Path, target: int) -> Result:
    """Mede a musica e grava as tags ReplayGain 2.0 nela.

    `-s i` e' o que escreve as tags; sem o `-S` (skip-existing) o rsgain sempre
    recalcula, que e' exatamente o comportamento desejado. O fluxo de audio nao e'
    tocado — so' os metadados — entao a operacao e' reversivel e sem perda.
    """
    args = [
        str(rsgain_path()),
        "custom",
        "-s", "i",            # tagmode insert: mede E grava as tags
        "-l", str(target),    # alvo de loudness vindo do dropdown (-18/-16/-14 LUFS)
        "-c", "p",            # protecao de clipping apenas para ganhos positivos
        "-O",                 # saida tab-delimitada no stdout: a nossa entrada de parsing
        "-p",                 # preserva o mtime, para nao baguncar a ordenacao por data
        str(path),
    ]
    return _execute(args, path)


def remove_tags(path: Path) -> Result:
    """Apaga as tags ReplayGain do arquivo, devolvendo-o ao estado original.

    Sem `-l` e sem `-c`: nada esta' sendo medido aqui, apenas removido. O `-O`
    continua para que a saida tenha o mesmo formato e o mesmo parser sirva aos dois.

    Limitacao medida no rsgain 3.8 (nao e' um bug nosso): o `-p` preserva a data de
    modificacao ao GRAVAR tags, mas nao ao apaga-las. Quem usa o Undo vai ver a
    "data de modificacao" do arquivo mudar para o momento da remocao.
    """
    args = [
        str(rsgain_path()),
        "custom",
        "-s", "d",            # tagmode delete: remove as tags ReplayGain
        "-O",
        "-p",
        str(path),
    ]
    return _execute(args, path)


def run_batch(paths: list[Path], target: int, q: "Queue", mode: str = "normalize") -> None:
    """Processa a lista de arquivos um a um, relatando tudo pela fila `q`.

    Roda numa thread separada para a janela nao congelar. A fila e' a UNICA ponte
    entre esta thread e a interface: tocar um widget daqui travaria ou corromperia
    a tela de forma aleatoria e dificil de reproduzir.

    Um processo do rsgain por arquivo e' proposital. Em lote, a coluna 0 traz so' o
    nome base — dois "01 - Intro.mp3" em pastas diferentes ficariam indistinguiveis,
    e um arquivo que falha simplesmente nao gera linha, deslocando todas as seguintes.
    Chamando um por vez, o resultado sempre casa com o caminho que nos mandamos.
    """
    total = len(paths)
    ok = 0
    failed = 0
    action = remove_tags if mode == "undo" else normalize_file
    verb = "Removendo etiquetas de" if mode == "undo" else "Analisando"

    for index, path in enumerate(paths, start=1):
        q.put(("progress", index, total))
        q.put(("log", f"[{index}/{total}] {verb} {path.name}"))

        # `normalize_file` recebe o alvo; `remove_tags` nao mede nada e nao usa alvo.
        result = action(path, target) if mode != "undo" else action(path)

        if result.error:
            failed += 1
            q.put(("log", f"    ERROR: {result.error}"))
        else:
            ok += 1
            if mode == "undo":
                q.put(("log", "    etiquetas removidas"))
            else:
                gain = "n/a" if result.gain is None else f"{result.gain:+.2f} dB"
                q.put(("log", f"    ajuste {gain}"))

        q.put(("result", result))

    q.put(("done", ok, failed))


if __name__ == "__main__":
    # Modo de teste pelo terminal: `python engine.py <pasta>` escaneia sem gravar nada
    # util so' para conferir a descoberta de arquivos sem abrir a interface.
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    found = find_songs(folder)
    print(f"rsgain: {rsgain_path()}")
    print(f"{len(found)} musica(s) encontrada(s) em {folder}")
    for song in found:
        print(f"  {song}")
