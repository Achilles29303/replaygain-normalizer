"""Gera assets/icon.ico sem depender de Pillow.

Por que na mao: o app so' precisa de customtkinter e tkinterdnd2. Arrastar o Pillow
inteiro (e mais uns 3 MB no executavel) para desenhar um quadrado com quatro barras
seria desproporcional. Um .ico no formato moderno e' apenas um cabecalho de 22 bytes
seguido de um PNG comum, e um PNG e' escrevivel com `zlib` + `struct` da biblioteca
padrao.

Uso:  python tools\\make_icon.py
"""

import struct
import zlib
from pathlib import Path

SIZE = 256
BG = (0x6C, 0x8C, 0xFF)     # o mesmo indigo do accent da interface
FG = (0x0F, 0x11, 0x15)     # as barras, na cor do fundo da janela
RADIUS = 56                 # canto arredondado, no estilo dos icones do Windows 11


def rounded_alpha(x: int, y: int) -> int:
    """Diz o quanto o pixel (x, y) pertence ao quadrado arredondado, de 0 a 255.

    Nos quatro cantos o teste vira uma distancia ate' o centro do arco. Amostrar a
    borda com uma transicao de 1 pixel (em vez de sim/nao) e' o que evita o efeito
    de escada no contorno.
    """
    cx = RADIUS if x < RADIUS else (SIZE - 1 - RADIUS if x > SIZE - 1 - RADIUS else x)
    cy = RADIUS if y < RADIUS else (SIZE - 1 - RADIUS if y > SIZE - 1 - RADIUS else y)
    dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    if dist <= RADIUS - 1:
        return 255
    if dist >= RADIUS:
        return 0
    return int(255 * (RADIUS - dist))


# Quatro barras verticais de alturas diferentes: a leitura imediata de "niveis de
# audio", que e' exatamente o que o app mede.
BARS = [(62, 92, 72), (100, 64, 128), (138, 84, 88), (176, 108, 40)]


def build_png() -> bytes:
    """Monta o PNG RGBA linha a linha e o devolve como bytes."""
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)  # cada linha do PNG comeca com o byte de filtro (0 = nenhum)
        for x in range(SIZE):
            alpha = rounded_alpha(x, y)
            color = BG
            for bx, bw, bh in BARS:
                top = (SIZE - bh) // 2
                if bx <= x < bx + bw and top <= y < top + bh:
                    color = FG
                    break
            rows += bytes((color[0], color[1], color[2], alpha))

    def chunk(tag: bytes, data: bytes) -> bytes:
        # Todo chunk PNG e': tamanho, tipo, dados e um CRC32 sobre tipo+dados.
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8 bits, cor tipo 6 = RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def build_ico(png: bytes) -> bytes:
    """Embrulha o PNG num container .ico de uma unica imagem."""
    # ICONDIR: reservado(0), tipo(1 = icone), quantidade(1).
    # ICONDIRENTRY: 0 em largura/altura significa 256 — o campo so' tem um byte.
    return (struct.pack("<HHH", 0, 1, 1)
            + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
            + png)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "assets"
    out.mkdir(exist_ok=True)
    path = out / "icon.ico"
    path.write_bytes(build_ico(build_png()))
    print(f"wrote {path} ({path.stat().st_size:,} bytes)")
