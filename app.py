"""Interface do ReplayGain Normalizer.

Este modulo cuida APENAS da tela. Toda a logica de medicao e gravacao de tags mora
em `engine.py`. A separacao nao e' enfeite: ela e' o que permite testar o motor pelo
terminal, sem abrir janela nenhuma, e o que impede que uma mudanca visual quebre o
processamento dos arquivos.
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import threading
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

import engine

APP_NAME = "ReplayGain Normalizer"

# ----------------------------------------------------------------------------------
# Paleta
# ----------------------------------------------------------------------------------
# Cores centralizadas em constantes para que a identidade visual seja trocada num
# lugar so'. O ttk.Treeview (widget nativo, alheio ao CustomTkinter) precisa receber
# esses mesmos valores na mao — sem isso a tabela sai branca no meio da janela escura.
BG = "#0F1115"          # fundo da janela
SURFACE = "#171A21"     # cartoes sobre o fundo
SURFACE_2 = "#1E222B"   # campos e cabecalho da tabela
BORDER = "#272C37"      # linhas de contorno, sutis de proposito
TEXT = "#E6EAF2"
MUTED = "#8B93A7"       # textos de apoio; contraste menor guia o olho para o principal
ACCENT = "#6C8CFF"
ACCENT_HOVER = "#5A7BF0"
SUCCESS = "#3ECF8E"
DANGER = "#FF6B6B"
DANGER_HOVER = "#E85A5A"
WARN = "#FFB454"

# Opcoes do dropdown. O texto mostrado e' amigavel; o numero real vai para o rsgain.
TARGETS: dict[str, int] = {
    "-18 LUFS · ReplayGain-Standard": -18,
    "-16 LUFS · etwas lauter": -16,
    "-14 LUFS · Streaming-Niveau": -14,
}

# Conteudo do painel de ajuda, em pares (titulo, texto). Fica aqui em cima, longe do
# codigo de layout, porque e' texto que o dono do app vai querer reescrever sozinho —
# e mexer em texto nao deveria exigir entender widget nenhum.
HELP_SECTIONS: tuple[tuple[str, str], ...] = (
    ("Was dieses Programm macht",
     "Es misst die wahrgenommene Lautstärke jedes Musikstücks und schreibt den Messwert als ReplayGain-Tag in die Datei. Die Audiodaten selbst werden nicht verändert: Dein Player (VLC, foobar2000, Rockbox usw.) liest das Tag und gibt alle Titel mit angeglichener Lautstärke wieder. Es geht nichts verloren und alles lässt sich rückgängig machen."),
    ("Gemessene Lautstärke (LUFS) · warum immer negativ?",
     "LUFS ist eine Lautstärkeskala, die das menschliche Hörvermögen berücksichtigt. Null ist die Obergrenze digitaler Audiosignale; alle Werte liegen darunter – deshalb sind die Zahlen negativ. Das ist völlig normal.\n\nJe näher der Wert an null liegt, desto lauter ist die Musik. -8 LUFS entspricht modernem, stark komprimiertem Rock; -18 LUFS eher ruhigem Jazz. Ein negativer Wert ist kein Problem."),
    ("Anpassung (dB) · die erforderliche Korrektur",
     "Das ist die Differenz zwischen der gemessenen Lautstärke und dem oben gewählten Zielwert.\n\nNegativ: Der Titel ist lauter als das Ziel und wird leiser wiedergegeben.\nPositiv (grün): Der Titel ist zu leise und wird lauter wiedergegeben.\n\nAls Orientierung: 3 dB sind deutlich hörbar; 10 dB werden ungefähr als doppelte Lautstärke wahrgenommen."),
    ("Peak · der höchste Punkt des Audiosignals",
     "Das ist der lauteste Moment eines Stücks. In dieser Skala entspricht 1,000 genau der digitalen Obergrenze. Oberhalb von 1,000 kann das Signal übersteuern, wenn es verstärkt wird. Gemessen wird nicht in dB, sondern auf einer Skala von 0 bis etwas über 1."),
    ("Clipping · der gelbe Hinweis",
     "Er erscheint, wenn eine Lautstärkeerhöhung den Peak über die Grenze treiben würde. Das Programm reduziert die Verstärkung automatisch so weit, dass das nicht passiert. Der Schutz vor Clipping ist immer aktiviert.\n\nDieser Hinweis bedeutet keinen Fehler: Das Programm hat die Lautstärke vorsorglich begrenzt."),
    ("Ziel-Lautstärke · welche soll ich wählen?",
     "-18 LUFS ist der ReplayGain-Standard und die sicherste Wahl für eine persönliche Musiksammlung: Der Peak muss dabei fast nie begrenzt werden.\n-14 LUFS wird von Spotify und YouTube verwendet.\n-16 LUFS liegt dazwischen.\n\nJe näher an null, desto lauter das Ergebnis – und desto wahrscheinlicher muss das Programm den Peak begrenzen, um Verzerrungen zu vermeiden."),
    ("Gedankenstrich (—) · nicht zutreffend",
     "Es gibt keinen anzuzeigenden Messwert: Entweder enthält die Datei digitale Stille oder die Messung ist fehlgeschlagen. Der Grund steht dann rot in der Spalte „Bedeutung“."),
    ("Rückgängig",
     "Wähle die betreffenden Zeilen in der Tabelle aus (Strg oder Umschalt für mehrere) und klicke auf „Ausgewählte Tags entfernen“. Die Tags werden gelöscht und die Dateien kehren zum vorherigen Zustand zurück. Die Audiodaten wurden nie verändert; es muss nur die Metadatenänderung rückgängig gemacht werden."),
)

# Passos de tamanho de texto. O rotulo e' o que aparece no botao do canto; o numero e'
# o fator entregue ao CustomTkinter, que redimensiona fontes E widgets de uma vez so'.
SCALE_STEPS: dict[str, float] = {"S": 1.0, "M": 1.25, "L": 1.5, "XL": 1.75}
DEFAULT_SCALE = "M"   # 25% maior que o desenho original ja' na primeira abertura

# Tamanho da janela em 100%. Cada passo de fonte multiplica estes numeros: texto maior
# dentro de uma janela do mesmo tamanho so' produziria conteudo cortado.
BASE_SIZE = (1080, 720)
MIN_SIZE = (900, 620)

# Preferencias no %APPDATA% e nao ao lado do .exe: um executavel portatil pode acabar
# numa pasta somente-leitura (Dateis de Programas, pendrive), e ai' salvar falharia.
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ReplayGainNormalizer"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _colorref(hex_color: str) -> int:
    """Converte "#RRGGBB" no inteiro 0x00BBGGRR que a API do Windows espera.

    O Windows guarda cor como COLORREF, que inverte a ordem dos canais em relacao
    ao hexadecimal que usamos no CSS e no CustomTkinter.
    """
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return (b << 16) | (g << 8) | r


def paint_titlebar(window) -> None:
    """Pinta a barra de titulo com as cores do app em vez da cor de destaque do Windows.

    A janela e' desenhada pelo tkinter, mas a moldura ao redor dela pertence ao
    sistema: sem isto o Windows usa a cor de destaque do usuario (verde, rosa,
    o que estiver configurado) e a barra destoa por completo da janela escura.
    Os numeros 20/34/35/36 sao atributos do DWM (dark mode, borda, fundo e texto
    da legenda). A chamada e' envolvida em try/except porque a funcao so' existe
    no Windows 10 1809 ou mais novo — em qualquer outro caso, apenas nao pinta.
    """
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        dwm = ctypes.windll.dwmapi.DwmSetWindowAttribute
        for attribute, value in (
            (20, 1),                    # modo escuro na moldura
            (35, _colorref(BG)),        # fundo da barra de titulo
            (34, _colorref(BORDER)),    # borda da janela
            (36, _colorref(TEXT)),      # cor do texto do titulo
        ):
            dwm(hwnd, attribute, ctypes.byref(ctypes.c_int(value)),
                ctypes.sizeof(ctypes.c_int))
    except Exception:
        pass


def apply_icon(window) -> None:
    """Aplica o icone do app na barra de titulo e na barra de tarefas."""
    icon = engine.base_dir() / "assets" / "icon.ico"
    try:
        if icon.is_file():
            window.iconbitmap(default=str(icon))
    except Exception:
        pass


def load_config() -> dict:
    """Le as preferencias salvas, caindo para os padroes se algo estiver errado.

    Um arquivo corrompido, apagado ou de uma versao antiga nao pode impedir o app de
    abrir — por isso o `except` largo: preferencia e' conveniencia, nao requisito.
    """
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_config(data: dict) -> None:
    """Grava as preferencias, ignorando falhas de escrita em silencio.

    Se o disco estiver cheio ou a pasta protegida, perder a preferencia e' irrelevante
    perto de derrubar o app na cara do usuario no meio de um lote.
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


def explain(result: engine.Result) -> str:
    """Traduz um resultado numerico do rsgain numa frase em portugues comum.

    Esta e' a unica coisa que separa uma tabela util de uma tabela intimidadora: o
    numero responde "quanto", a frase responde "e dai'?". A ordem dos testes segue a
    gravidade — erro primeiro, depois o aviso de distorcao, e so' entao o caso comum.
    """
    if result.error:
        # A mensagem crua do rsgain cabe aqui e nao cabia na coluna estreita de ajuste;
        # o prefixo em portugues da' a ela um contexto que o ingles tecnico nao da'.
        return f"Fehlgeschlagen: {result.error}"
    if result.gain is None:
        # Silencio digital: o rsgain devolve -inf, e nao ha' volume a corrigir.
        return "Digitale Stille – keine Lautstärke messbar"

    # Meio decibel e' o limiar abaixo do qual ninguem escuta diferenca; chamar isso de
    # "ajuste" assustaria o usuario a' toa.
    if abs(result.gain) < 0.5:
        frase = "Bereits auf Ziellautstärke"
    elif result.gain > 0:
        frase = f"Zu leise – wird um {result.gain:.1f} dB"
    else:
        frase = f"Zu laut – wird um {abs(result.gain):.1f} dB"

    # Frase curta de proposito: a coluna e' larga, mas nao infinita, e o significado
    # completo desse aviso mora no painel de ajuda.
    if result.clipped:
        frase += "  ·  pico begrenzt"
    return frase


class HelpWindow(ctk.CTkToplevel):
    """Painel de ajuda: uma janela rolavel com uma secao por duvida real.

    Nao e' um `Dialog` porque o texto e' longo e precisa de rolagem — e porque ele
    nao bloqueia nada: da' para ler a explicacao com a tabela aberta atras.
    """

    def __init__(self, master) -> None:
        super().__init__(master)
        self.title("Ajuda — entendendo os resultados")
        self.configure(fg_color=BG)
        self.transient(master)
        # `geometry` fala em pixels reais da tela, mas os widgets ja' vem ampliados
        # pelo tamanho de texto escolhido. Sem multiplicar aqui pela mesma escala, o
        # conteudo fica maior que a janela e os paragrafos saem cortados na direita.
        scale = SCALE_STEPS[master.scale_name]
        self.geometry(f"{int(620 * scale)}x{int(700 * scale)}")
        self.minsize(int(520 * scale), int(460 * scale))

        ctk.CTkLabel(self, text="Ergebnisse verstehen", text_color=TEXT,
                     anchor="w", font=ctk.CTkFont("Segoe UI", 18, "bold")
                     ).pack(fill="x", padx=26, pady=(22, 2))
        ctk.CTkLabel(self, text="Erklärung der Tabellenspalten",
                     text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)
                     ).pack(fill="x", padx=26, pady=(0, 14))

        body = ctk.CTkScrollableFrame(
            self, fg_color=SURFACE, corner_radius=14,
            border_width=1, border_color=BORDER,
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=MUTED)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))

        for title, text in HELP_SECTIONS:
            ctk.CTkLabel(body, text=title, text_color=ACCENT, anchor="w",
                         justify="left", font=ctk.CTkFont("Segoe UI", 13, "bold")
                         ).pack(fill="x", padx=18, pady=(16, 4))
            # `wraplength` em pixels e' o que quebra o paragrafo; sem ele o texto
            # viraria uma unica linha gigante e a janela ganharia rolagem horizontal.
            ctk.CTkLabel(body, text=text, text_color=TEXT, anchor="w",
                         justify="left", wraplength=500,
                         font=ctk.CTkFont("Segoe UI", 12)
                         ).pack(fill="x", padx=18, pady=(0, 2))

        ctk.CTkButton(self, text="Verstanden", width=130, height=38, corner_radius=8,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      text_color="#0F1115",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self.destroy).pack(pady=(0, 18))

        self.bind("<Escape>", lambda _e: self.destroy())
        # A janela nasce no canto superior esquerdo do monitor; coloca-la sobre a
        # janela principal evita que ela pareca ter aberto em outro lugar.
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + 40
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.after(120, lambda: paint_titlebar(self))
        self.focus_force()


class Dialog(ctk.CTkToplevel):    """Caixa de dialogo modal com a mesma linguagem visual do resto do app.
    O `messagebox` do tkinter usa o tema claro do Windows e destoa por completo de uma
    janela escura. Como sao apenas dois dialogos, desenhar os dois na mao sai mais
    barato do que conviver com o contraste feio.
    """

    def __init__(self, master, title: str, message: str,
                 confirm: str | None = None, danger: bool = False) -> None:
        super().__init__(master)
        self.result = False

        self.title(title)
        self.configure(fg_color=BG)
        self.resizable(False, False)
        # `transient` prende o dialogo a' janela principal (ele nao aparece separado na
        # barra de tarefas e some junto); `grab_set` bloqueia cliques atras dele.
        self.transient(master)

        wrapper = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=12,
                               border_width=1, border_color=BORDER)
        wrapper.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(wrapper, text=title, font=ctk.CTkFont("Segoe UI", 15, "bold"),
                     text_color=TEXT, anchor="w").pack(fill="x", padx=22, pady=(20, 6))
        ctk.CTkLabel(wrapper, text=message, font=ctk.CTkFont("Segoe UI", 12),
                     text_color=MUTED, anchor="w", justify="left",
                     wraplength=380).pack(fill="x", padx=22, pady=(0, 20))

        buttons = ctk.CTkFrame(wrapper, fg_color="transparent")
        buttons.pack(fill="x", padx=22, pady=(0, 20))

        if confirm:
            # So' existe botao de cancelar quando ha' algo a confirmar; um aviso
            # informativo tem uma saida unica, e duas opcoes ali seriam ruido.
            ctk.CTkButton(buttons, text="Abbrechen", width=104, height=36,
                          corner_radius=8, fg_color="transparent", border_width=1,
                          border_color=BORDER, hover_color=SURFACE_2,
                          text_color=MUTED, font=ctk.CTkFont("Segoe UI", 12),
                          command=self._cancel).pack(side="right", padx=(8, 0))

        ctk.CTkButton(buttons, text=confirm or "Verstanden", width=124, height=36,
                      corner_radius=8,
                      fg_color=DANGER if danger else ACCENT,
                      hover_color=DANGER_HOVER if danger else ACCENT_HOVER,
                      text_color="#0F1115" if not danger else "#FFFFFF",
                      font=ctk.CTkFont("Segoe UI", 12, "bold"),
                      command=self._confirm).pack(side="right")

        self.bind("<Return>", lambda _e: self._confirm())
        self.bind("<Escape>", lambda _e: self._cancel())

        self.update_idletasks()
        self._center_on(master)
        paint_titlebar(self)
        # O grab so' funciona depois que a janela esta' visivel; por isso vem aqui
        # embaixo, e nao junto do `transient` la' em cima.
        self.grab_set()
        self.focus_force()
        self.wait_window()

    def _center_on(self, master) -> None:
        """Centraliza o dialogo sobre a janela principal, e nao sobre a tela."""
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _confirm(self) -> None:
        self.result = True
        self.destroy()

    def _cancel(self) -> None:
        self.result = False
        self.destroy()


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    """Janela principal.

    A heranca dupla existe porque um `ctk.CTk()` puro nao tem `drop_target_register`:
    arrastar e soltar vem da extensao Tcl do tkinterdnd2, que precisa ser carregada
    dentro do interpretador desta janela especifica.
    """

    def __init__(self) -> None:
        super().__init__()
        # Sem esta linha a extensao Tcl nunca e' carregada e `drop_target_register`
        # simplesmente nao existe no objeto — o erro so' apareceria ao arrastar algo.
        self.TkdndVersion = TkinterDnD._require(self)

        self.config_data = load_config()
        # A escala precisa valer ANTES do primeiro widget nascer: o CustomTkinter
        # aplica o fator no momento em que cada widget se desenha pela primeira vez.
        self.scale_name = self.config_data.get("ui_scale", DEFAULT_SCALE)
        if self.scale_name not in SCALE_STEPS:
            self.scale_name = DEFAULT_SCALE
        ctk.set_widget_scaling(SCALE_STEPS[self.scale_name])
        # A fila e' a UNICA ponte entre a thread de processamento e a interface.
        self.queue: queue.Queue = queue.Queue()
        self.songs: list[Path] = []
        self.folder: Path | None = None
        # A tabela mostra so' o nome do arquivo, mas o Undo precisa do caminho
        # absoluto; este dicionario liga o id de cada linha ao caminho real.
        self.row_paths: dict[str, Path] = {}
        self.running = False
        self.mode = "normalize"
        # Preenchido no Undo: liga cada caminho a' linha existente na tabela, para
        # que o resultado volte para a MESMA linha em vez de criar uma duplicada.
        self._undo_rows: dict[Path, str] = {}

        self._build_window()
        self._build_ui()
        self._restore_preferences()
        # O aviso de primeira execucao espera a janela existir de verdade, senao
        # o dialogo aparece centralizado sobre uma janela de tamanho 1x1.
        self.after(220, self._first_run_warning)

    # -- janela ---------------------------------------------------------------

    def _build_window(self) -> None:
        """Configura tamanho, tema e o alvo de drag-and-drop da janela inteira."""
        ctk.set_appearance_mode("dark")
        self.title(APP_NAME)
        self.configure(fg_color=BG)
        scale = SCALE_STEPS[self.scale_name]
        self.minsize(int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale))
        self._center()
        apply_icon(self)
        paint_titlebar(self)

        # A janela toda aceita drop: obrigar a mira num campo pequeno e' irritante.
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)

        self.grid_columnconfigure(0, weight=1)
        # Apenas a linha da tabela cresce quando a janela e' redimensionada; todo o
        # resto tem altura fixa, entao o espaco extra vai para onde importa.
        self.grid_rowconfigure(4, weight=1)

    def _center(self) -> None:
        """Abre a janela no centro do monitor em vez do canto superior esquerdo."""
        self.update_idletasks()
        scale = SCALE_STEPS[self.scale_name]
        w, h = int(BASE_SIZE[0] * scale), int(BASE_SIZE[1] * scale)
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2 - 20
        self.geometry(f"{w}x{h}+{x}+{max(y, 0)}")

    # -- construcao da interface ----------------------------------------------

    def _build_ui(self) -> None:
        """Monta a tela de cima para baixo, uma linha do grid por bloco."""
        self._build_header()
        self._build_source_card()
        self._build_controls()
        self._build_progress()
        self._build_table()
        self._build_log()

    def _build_header(self) -> None:
        """Cabecalho: nome do app, uma linha do que ele faz, e o selo do motor."""
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(24, 14))
        header.grid_columnconfigure(1, weight=1)

        # Um quadrado com o glifo de onda faz as vezes de logotipo sem exigir
        # um arquivo de icone empacotado junto.
        mark = ctk.CTkLabel(header, text="\u2016\u2016", width=44, height=44,
                            corner_radius=12, fg_color=SURFACE_2,
                            text_color=ACCENT, font=ctk.CTkFont("Segoe UI", 18, "bold"))
        mark.grid(row=0, column=0, rowspan=2, padx=(0, 14))

        ctk.CTkLabel(header, text=APP_NAME, text_color=TEXT, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 21, "bold")
                     ).grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(header,
                     text="Grava etiquetas ReplayGain 2.0 \u00b7 s\u00f3 metadados, o \u00e1udio n\u00e3o \u00e9 tocado",
                     text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)
                     ).grid(row=1, column=1, sticky="w")

        ctk.CTkLabel(header, text="  rsgain 3.8  ", height=26, corner_radius=13,
                     fg_color=SURFACE_2, text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 11)
                     ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 14))

        # Controle de tamanho do texto no canto superior direito. Ocupa as duas linhas
        # do cabecalho (rotulo em cima, botoes embaixo) para nao esticar a barra.
        corner = ctk.CTkFrame(header, fg_color="transparent")
        corner.grid(row=0, column=3, rowspan=2, sticky="e")

        ctk.CTkLabel(corner, text="TEXTGRÖSSE", text_color=MUTED, anchor="e",
                     font=ctk.CTkFont("Segoe UI", 10, "bold")
                     ).grid(row=0, column=0, sticky="e", pady=(0, 4))

        self.scale_selector = ctk.CTkSegmentedButton(
            corner, values=list(SCALE_STEPS), height=30, corner_radius=8,
            fg_color=SURFACE_2, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, unselected_color=SURFACE_2,
            unselected_hover_color=BORDER, text_color=TEXT,
            font=ctk.CTkFont("Segoe UI", 11, "bold"),
            command=self._on_scale_change)
        self.scale_selector.grid(row=1, column=0, sticky="e")
        self.scale_selector.set(self.scale_name)

        # Botao de ajuda no canto extremo. Redondo e com o "?" sozinho: e' o simbolo
        # que todo mundo ja' reconhece, e assim ele nao compete com o Analysieren und taggen.
        ctk.CTkButton(header, text="?", width=42, height=42, corner_radius=21,
                      fg_color=SURFACE_2, hover_color=ACCENT, text_color=TEXT,
                      border_width=1, border_color=BORDER,
                      font=ctk.CTkFont("Segoe UI", 17, "bold"),
                      command=self._show_help
                      ).grid(row=0, column=4, rowspan=2, sticky="e", padx=(14, 0))

    def _build_source_card(self) -> None:
        """Cartao da pasta de origem: caminho, botao de selecao e dica de arrastar."""
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=28)
        card.grid_columnconfigure(0, weight=1)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 4))
        row.grid_columnconfigure(0, weight=1)

        # O campo e' somente leitura de proposito: um caminho digitado errado viraria
        # um "0 songs found" sem explicacao. Escolher pelo dialogo nunca erra.
        self.path_entry = ctk.CTkEntry(
            row, height=42, corner_radius=9, fg_color=SURFACE_2,
            border_color=BORDER, border_width=1, text_color=TEXT,
            placeholder_text="Ziehe einen Ordner auf dieses Fenster oder klicke auf Durchsuchen\u2026",
            font=ctk.CTkFont("Segoe UI", 12))
        self.path_entry.grid(row=0, column=0, sticky="ew")
        self.path_entry.configure(state="readonly")

        ctk.CTkButton(row, text="Durchsuchen", width=112, height=42, corner_radius=9,
                      fg_color=SURFACE_2, hover_color=BORDER, text_color=TEXT,
                      border_width=1, border_color=BORDER,
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=self._pick_folder).grid(row=0, column=1, padx=(10, 0))

        self.count_label = ctk.CTkLabel(
            card, text="Kein Ordner ausgewählt", text_color=MUTED, anchor="w",
            font=ctk.CTkFont("Segoe UI", 11))
        self.count_label.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

    def _build_controls(self) -> None:
        """Linha de acao: alvo de loudness, Normalize e Undo."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))
        bar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(left, text="ZIEL-LAUTSTÄRKE", text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 10, "bold")
                     ).grid(row=0, column=0, sticky="w", pady=(0, 5))

        self.target_menu = ctk.CTkOptionMenu(
            left, values=list(TARGETS.keys()), width=272, height=42,
            corner_radius=9, fg_color=SURFACE_2, button_color=SURFACE_2,
            button_hover_color=BORDER, text_color=TEXT,
            dropdown_fg_color=SURFACE_2, dropdown_text_color=TEXT,
            dropdown_hover_color=ACCENT,
            font=ctk.CTkFont("Segoe UI", 12),
            dropdown_font=ctk.CTkFont("Segoe UI", 12),
            command=self._on_target_change)
        self.target_menu.grid(row=1, column=0, sticky="w")

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e")

        self.undo_button = ctk.CTkButton(
            right, text="Ausgewählte Tags entfernen", width=188, height=42, corner_radius=9,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE_2, text_color=MUTED,
            font=ctk.CTkFont("Segoe UI", 12),
            state="disabled", command=self._undo_selected)
        self.undo_button.grid(row=0, column=0, padx=(0, 10), pady=(20, 0))

        self.normalize_button = ctk.CTkButton(
            right, text="Analysieren und taggen", width=164, height=42, corner_radius=9,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0F1115",
            text_color_disabled=MUTED, border_color=BORDER,
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            command=self._normalize)
        self.normalize_button.grid(row=0, column=1, pady=(20, 0))
        self._set_normalize_enabled(False)

    def _build_progress(self) -> None:
        """Barra de progresso e o contador 'file N of M'."""
        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.grid(row=3, column=0, sticky="ew", padx=28, pady=(18, 10))
        wrap.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(
            wrap, height=6, corner_radius=3,
            fg_color=SURFACE_2, progress_color=ACCENT)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        self.progress_label = ctk.CTkLabel(
            wrap, text="Bereit", text_color=MUTED, width=190, anchor="e",
            font=ctk.CTkFont("Segoe UI", 11))
        self.progress_label.grid(row=0, column=1, padx=(14, 0))

    def _build_table(self) -> None:
        """Tabela de resultados: um ttk.Treeview repintado a' mao para o tema escuro."""
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=4, column=0, sticky="nsew", padx=28)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        # Guardado em `self` porque o controle de tamanho de texto precisa reescrever
        # estas fontes depois: o Treeview e' widget nativo e ignora o CustomTkinter.
        style = self.tree_style = ttk.Style()
        # O tema nativo do Windows IGNORA cores de fundo. O "clam" as respeita —
        # sem esta linha a tabela sai branca no meio de uma janela escura.
        style.theme_use("clam")
        style.configure("RG.Treeview",
                        background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, borderwidth=0, relief="flat")
        style.configure("RG.Treeview.Heading",
                        background=SURFACE_2, foreground=MUTED,
                        borderwidth=0, relief="flat")
        # `map` limpa o realce azul padrao do cabecalho ao passar o mouse e define
        # a cor da linha selecionada — sem isso o clique pinta com o azul do sistema.
        style.map("RG.Treeview.Heading",
                  background=[("active", BORDER)], foreground=[("active", TEXT)])
        style.map("RG.Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#0F1115")])
        # O layout padrao do clam desenha uma moldura em volta da tabela, que aparece
        # como um retangulo claro por cima do cartao. Reduzir o layout a' area de
        # conteudo e' a unica forma de remove-la — nao existe opcao de borda aqui.
        style.layout("RG.Treeview",
                     [("RG.Treeview.treearea", {"sticky": "nswe"})])
        # A barra de rolagem do clam vem com dois botoes de seta e um relevo claro.
        # Redefinir o layout para apenas trilho + polegar deixa uma barra fina e
        # discreta, no lugar do controle cinza dos anos 2000.
        style.layout("RG.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]}),
        ])
        style.configure("RG.Vertical.TScrollbar",
                        troughcolor=SURFACE, background=BORDER,
                        bordercolor=SURFACE, darkcolor=BORDER, lightcolor=BORDER,
                        borderwidth=0, relief="flat", arrowsize=0, width=9)
        style.map("RG.Vertical.TScrollbar", background=[("active", MUTED)])

        # Larguras em 100%; o controle de tamanho de texto as multiplica junto com as
        # fontes, senao um cabecalho maior nao caberia mais na propria coluna.
        # A ultima coluna e' a razao de a tabela deixar de ser um enigma: ela repete o
        # que os numeros dizem, em portugues comum. Os cabecalhos evitam jargao — o
        # termo tecnico (LUFS, dB) fica entre parenteses, explicado no botao "?".
        self.tree_columns = {
            "file": ("Datei", 210, "w"),
            "loudness": ("Lautstärke (LUFS)", 130, "e"),
            "gain": ("Anpassung (dB)", 105, "e"),
            "peak": ("Peak", 75, "e"),
            "clip": ("Clipping", 88, "center"),
            "meaning": ("Bedeutung", 370, "w"),
        }
        self.tree = ttk.Treeview(card, columns=tuple(self.tree_columns),
                                 show="headings", style="RG.Treeview",
                                 selectmode="extended")

        for key, (text, _width, anchor) in self.tree_columns.items():
            self.tree.heading(key, text=text)
            # `stretch` so' nas duas colunas de texto: as numericas tem largura
            # previsivel e esticar todas deixaria numeros perdidos em celulas gigantes.
            self.tree.column(key, anchor=anchor,
                             stretch=(key in ("file", "meaning")))
        self._apply_table_scaling(SCALE_STEPS[self.scale_name])

        # Faixas alternadas ajudam o olho a seguir a linha ate' a ultima coluna.
        self.tree.tag_configure("odd", background=SURFACE)
        self.tree.tag_configure("even", background="#12151B")
        self.tree.tag_configure("error", foreground=DANGER)
        self.tree.tag_configure("cleared", foreground=MUTED)
        self.tree.tag_configure("boost", foreground=SUCCESS)
        self.tree.tag_configure("clipwarn", foreground=WARN)

        scroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview,
                               style="RG.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 6), pady=6)

        # O botao Undo so' faz sentido com linhas selecionadas; este evento mantem
        # o estado do botao sempre coerente com a selecao atual.
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _build_log(self) -> None:
        """Caixa de log: a narracao do que esta' acontecendo, linha a linha."""
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=5, column=0, sticky="ew", padx=28, pady=(14, 24))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="PROTOKOLL", text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 10, "bold")
                     ).grid(row=0, column=0, sticky="w", padx=18, pady=(12, 0))

        self.log_box = ctk.CTkTextbox(
            card, height=118, corner_radius=9, fg_color="#13161C",
            border_width=0, text_color=MUTED, wrap="none",
            scrollbar_button_color=BORDER, scrollbar_button_hover_color=MUTED,
            font=ctk.CTkFont("Consolas", 11))
        self.log_box.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 14))
        self.log_box.configure(state="disabled")

    # -- preferencias ---------------------------------------------------------

    def _restore_preferences(self) -> None:
        """Reaplica o alvo de loudness e a ultima pasta usados na sessao anterior."""
        target = self.config_data.get("target_lufs", -18)
        for label, value in TARGETS.items():
            if value == target:
                self.target_menu.set(label)
                break

        last = self.config_data.get("last_folder")
        # A pasta pode ter sido movida, renomeada ou estar num HD desconectado —
        # por isso o `is_dir()` antes de aceita-la de volta.
        if last and Path(last).is_dir():
            self._set_folder(Path(last), log=False)

    def _current_target(self) -> int:
        """Traduz o rotulo do dropdown para o numero que o rsgain entende."""
        return TARGETS.get(self.target_menu.get(), -18)

    def _on_target_change(self, _label: str) -> None:
        """Salva o alvo assim que ele muda, e nao so' ao fechar o app.

        Fechar a janela pelo X nao dispara nenhum salvamento; gravar na hora da
        escolha e' o que garante que a preferencia sobreviva de verdade.
        """
        self.config_data["target_lufs"] = self._current_target()
        save_config(self.config_data)

    def _on_scale_change(self, name: str) -> None:
        """Aplica um novo tamanho de texto a' interface inteira e o guarda.

        Um unico interruptor do CustomTkinter reescala fontes e widgets de todos os
        controles dele, ao vivo — nada precisa ser reconstruido.
        """
        scale = SCALE_STEPS[name]
        self.scale_name = name
        ctk.set_widget_scaling(scale)
        self._apply_table_scaling(scale)
        # Texto maior sem janela maior esconde o conteudo atras da borda; por isso a
        # janela volta ao tamanho padrao correspondente a' escala escolhida.
        self.minsize(int(MIN_SIZE[0] * scale), int(MIN_SIZE[1] * scale))
        self.geometry(f"{int(BASE_SIZE[0] * scale)}x{int(BASE_SIZE[1] * scale)}")

        self.config_data["ui_scale"] = name
        save_config(self.config_data)

    def _apply_table_scaling(self, scale: float) -> None:
        """Reescala a tabela na mao, que e' o unico pedaco alheio ao CustomTkinter.

        Mudar o estilo ttk vale para o widget ja' criado: nao ha' nada a recriar,
        so' a altura da linha, as fontes e as larguras a recalcular.
        """
        self.tree_style.configure("RG.Treeview",
                                  rowheight=int(30 * scale),
                                  font=("Segoe UI", int(10 * scale)))
        self.tree_style.configure("RG.Treeview.Heading",
                                  font=("Segoe UI", int(9 * scale), "bold"),
                                  padding=(int(10 * scale), int(9 * scale)))
        for key, (_text, width, _anchor) in self.tree_columns.items():
            self.tree.column(key, width=int(width * scale),
                             minwidth=int(70 * scale))

    def _first_run_warning(self) -> None:
        """Avisa, uma unica vez, que o app grava nos arquivos do usuario."""
        if self.config_data.get("warning_shown"):
            return
        Dialog(self, "Antes de come\u00e7ar",
               "Este app grava etiquetas ReplayGain dentro dos seus arquivos de "
               "m\u00fasica. S\u00f3 os metadados mudam \u2014 o \u00e1udio nunca \u00e9 recodificado, "
               "e toda altera\u00e7\u00e3o pode ser desfeita.\n\n"
               "Ainda assim, teste primeiro numa c\u00f3pia de algumas m\u00fasicas.\n\n"
               "Se a tabela de resultados parecer confusa, o bot\u00e3o \"?\" no canto "
               "superior direito explica cada coluna.")
        self.config_data["warning_shown"] = True
        save_config(self.config_data)

    def _show_help(self) -> None:
        """Abre o painel de ajuda, ou traz de volta o que ja' esta' aberto.

        Sem esta guarda, cada clique no "?" empilharia uma janela nova por cima da
        anterior \u2014 e o usuario teria que fechar todas, uma a uma.
        """
        existing = getattr(self, "_help_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        self._help_window = HelpWindow(self)

    # -- selecao de pasta -----------------------------------------------------

    def _pick_folder(self) -> None:
        """Abre o dialogo nativo de escolha de pasta, comecando na ultima usada."""
        initial = str(self.folder) if self.folder else self.config_data.get("last_folder", "")
        chosen = filedialog.askdirectory(title="Musikordner auswählen",
                                         initialdir=initial or None)
        if chosen:
            self._set_folder(Path(chosen))

    def _on_drop(self, event) -> None:
        """Trata algo solto na janela: pasta vai direto, arquivo vira a pasta dele."""
        # `event.data` chega como lista do Tcl: caminhos com espaco vem entre chaves.
        # `splitlist` e' a unica forma correta de separar — um `split(" ")` quebraria
        # "D:/Minhas Musicas" em dois pedacos invalidos.
        items = self.tk.splitlist(event.data)
        if not items:
            return

        dropped = Path(items[0])
        if dropped.is_file():
            # Soltar uma musica quase sempre significa "quero esta pasta"; dizer isso
            # no log evita a impressao de que o app ignorou o que foi arrastado.
            self._log(f"Um arquivo foi solto \u2014 usando a pasta dele: {dropped.parent}")
            dropped = dropped.parent
        if not dropped.is_dir():
            self._log(f"N\u00e3o foi poss\u00edvel usar o item solto: {dropped}")
            return

        if len(items) > 1:
            self._log(f"{len(items)} itens soltos \u2014 usando apenas o primeiro.")
        self._set_folder(dropped)

    def _set_folder(self, folder: Path, log: bool = True) -> None:
        """Adota uma pasta: varre as musicas, atualiza o contador e salva a escolha."""
        self.folder = folder
        self.songs = engine.find_songs(folder)

        # O campo e' readonly; para escrever nele e' preciso destrava-lo por um instante.
        self.path_entry.configure(state="normal")
        self.path_entry.delete(0, "end")
        self.path_entry.insert(0, str(folder))
        self.path_entry.configure(state="readonly")

        count = len(self.songs)
        if count:
            self.count_label.configure(
                text=f"{count} Musikdatei{'en' if count != 1 else ''} gefunden"
                     f"{'en' if count != 1 else ''}  \u00b7  .mp3 und .flac, "
                     "einschließlich Unterordner",
                text_color=SUCCESS)
        else:
            self.count_label.configure(
                text="Keine .mp3- oder .flac-Dateien in diesem Ordner", text_color=WARN)

        self._set_busy(False)
        if log:
            self._log(f"Ordner: {folder}")
            self._log(f"{count} Musikdatei(en) gefunden.")

        self.config_data["last_folder"] = str(folder)
        save_config(self.config_data)

    # -- execucao -------------------------------------------------------------

    def _normalize(self) -> None:
        """Dispara a medicao e gravacao de tags de todas as musicas encontradas."""
        if self.running or not self.songs:
            return
        self._clear_table()
        self._log(f"\u2014 Analysiere {len(self.songs)} Datei(en) mit Zielwert "
                  f"{self._current_target()} LUFS \u2014")
        self._start(self.songs, mode="normalize")

    def _undo_selected(self) -> None:
        """Remove as tags ReplayGain apenas das linhas selecionadas na tabela."""
        if self.running:
            return
        selection = self.tree.selection()
        paths = [self.row_paths[item] for item in selection if item in self.row_paths]
        if not paths:
            return

        dialog = Dialog(
            self, "ReplayGain-Tags entfernen?",
            f"Die ReplayGain-Tags von {len(paths)} ausgewählten Datei(en) "
            "werden gelöscht.\n\n"
            "Die Audiodaten bleiben unverändert, aber die Messwerte gehen verloren – "
            "du müsstest die Dateien erneut analysieren.",
            confirm="Tags entfernen", danger=True)
        if not dialog.result:
            return

        # Guardar quais linhas foram afetadas permite marca-las como limpas depois,        # em vez de reconstruir a tabela inteira e perder o que ja' estava nela.
        self._undo_rows = {self.row_paths[i]: i for i in selection if i in self.row_paths}
        self._log(f"\u2014 Entferne Tags aus {len(paths)} Datei(en) \u2014")        self._start(paths, mode="undo")

    def _start(self, paths: list[Path], mode: str) -> None:
        """Sobe a thread de trabalho e comeca a ler a fila.

        `daemon=True` garante que fechar a janela nao deixe um processo fantasma
        segurando o app aberto em segundo plano.
        """
        try:
            engine.rsgain_path()
        except FileNotFoundError as exc:
            # Descobrir a ausencia do motor ANTES de travar os botoes evita deixar a
            # interface presa num estado "ocupado" que nunca vai terminar.
            Dialog(self, "Audio-Engine nicht gefunden", str(exc))
            return

        self.running = True
        self._set_busy(True)
        self.progress.set(0)
        self.mode = mode

        threading.Thread(
            target=engine.run_batch,
            args=(paths, self._current_target(), self.queue, mode),
            daemon=True,
        ).start()
        # A interface le' a fila sozinha, a cada 100 ms. A thread NUNCA toca a tela:
        # o tkinter nao e' thread-safe e mexer nele de fora trava ou corrompe a
        # janela de um jeito aleatorio e praticamente impossivel de reproduzir.
        self.after(100, self._poll_queue)

    def _poll_queue(self) -> None:
        """Drena a fila, aplica cada mensagem na tela e se reagenda ate' o 'done'."""
        try:
            while True:
                # `get_nowait` nunca bloqueia: se a fila esvaziar, o Empty nos tira
                # do laco e a interface volta a responder imediatamente.
                message = self.queue.get_nowait()
                kind = message[0]

                if kind == "progress":
                    _, done, total = message
                    self.progress.set(done / total if total else 0)
                    self.progress_label.configure(
                        text=f"Datei {done} de {total}", text_color=TEXT)
                elif kind == "log":
                    self._log(message[1])
                elif kind == "result":
                    self._add_result(message[1])
                elif kind == "done":
                    self._finish(message[1], message[2])
                    return
        except queue.Empty:
            pass

        self.after(100, self._poll_queue)

    def _finish(self, ok: int, failed: int) -> None:
        """Encerra o lote: libera os botoes e resume o resultado numa linha."""
        self.running = False
        self._set_busy(False)
        self.progress.set(1 if ok or failed else 0)

        summary = f"Abgeschlossen · {ok} erfolgreich"
        if failed:
            summary += f", {failed} fehlgeschlagen"
        self.progress_label.configure(
            text=summary, text_color=DANGER if failed else SUCCESS)
        self._log(f"\u2014 {summary} \u2014\n")
        self._undo_rows = {}

    # -- tabela e log ---------------------------------------------------------

    def _add_result(self, result: engine.Result) -> None:
        """Cria ou atualiza a linha correspondente ao arquivo processado."""
        if self.mode == "undo":
            # No undo a linha ja' existe: atualiza-la no lugar preserva a ordem da
            # tabela e a nocao visual de "esta musica aqui foi limpa".
            item = self._undo_rows.get(result.path)
            if item and self.tree.exists(item):
                if result.error:
                    self.tree.item(item, values=(result.path.name, "\u2014",
                                                 "\u2014", "\u2014", "\u2014",
                                                 explain(result)),
                                   tags=("error",))
                else:
                    self.tree.item(item, values=(result.path.name, "\u2014",
                                                 "Tags entfernt", "\u2014",
                                                 "\u2014",
                                                 "Ursprünglicher Zustand wiederhergestellt"),
                                   tags=("cleared",))
                return

        stripe = "even" if len(self.tree.get_children()) % 2 else "odd"

        if result.error:
            # O motivo do erro vai inteiro para a coluna larga da direita; na coluna
            # de ajuste ele so' cabia pela metade e terminava cortado no meio.
            values = (result.path.name, "\u2014", "\u2014", "\u2014", "\u2014",
                      explain(result))
            tags = ("error", stripe)
        else:
            # `-inf` (silencio digital) nao tem numero para mostrar; o travessao
            # comunica "nao se aplica" melhor do que um "0.00" enganoso.
            loudness = "\u2014" if result.loudness is None else f"{result.loudness:.2f}"
            gain = "\u2014" if result.gain is None else f"{result.gain:+.2f}"
            peak = "\u2014" if result.peak is None else f"{result.peak:.4f}"
            values = (result.path.name, loudness, gain, peak,
                      "begrenzt" if result.clipped else "\u2014",
                      explain(result))
            # Ganho positivo = musica baixa que foi levantada; verde marca isso.
            # O aviso de clipping tem prioridade por ser o caso que merece atencao.
            if result.clipped:
                tags = ("clipwarn", stripe)
            elif result.gain is not None and result.gain > 0:
                tags = ("boost", stripe)
            else:
                tags = (stripe,)

        item = self.tree.insert("", "end", values=values, tags=tags)
        self.row_paths[item] = result.path
        # Acompanhar a ultima linha faz a tabela rolar sozinha durante o lote.
        self.tree.see(item)

    def _clear_table(self) -> None:
        """Esvazia a tabela e o mapa de caminhos antes de um novo lote."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.row_paths.clear()
        self._on_select()

    def _log(self, text: str) -> None:
        """Acrescenta uma linha ao log e rola para o fim.

        A caixa fica `disabled` para nao ser editavel; escrever nela exige destravar,
        inserir e travar de novo — caso contrario o `insert` e' ignorado em silencio.
        """
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # -- estados --------------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        """Liga o botao Undo somente quando ha' linhas selecionadas e nada rodando."""
        has_selection = bool(self.tree.selection()) and not self.running
        self.undo_button.configure(
            state="normal" if has_selection else "disabled",
            text_color=DANGER if has_selection else MUTED,
            border_color=DANGER if has_selection else BORDER)

    def _set_busy(self, busy: bool) -> None:
        """Bloqueia os controles durante um lote e os devolve ao terminar.

        Deixar Normalize clicavel no meio de uma execucao permitiria duas threads
        gravando tags no mesmo arquivo ao mesmo tempo — receita para corrupcao.
        """
        if busy:
            self.normalize_button.configure(text="Trabalhando\u2026")
            self._set_normalize_enabled(False)
            self.undo_button.configure(state="disabled")
            self.target_menu.configure(state="disabled")
        else:
            self.normalize_button.configure(text="Analysieren und taggen")
            self._set_normalize_enabled(bool(self.songs))
            self.target_menu.configure(state="normal")
            self._on_select()

    def _set_normalize_enabled(self, enabled: bool) -> None:
        """Pinta o Normalize de acordo com o fato de ele estar ou nao clicavel.

        O CustomTkinter nao escurece o fundo de um botao desabilitado: ele continuaria
        com o mesmo azul vivo de quando esta' pronto, mentindo sobre o proprio estado.
        Desligado ele fica preto como o fundo, e so' a borda fina o mantem visivel.
        """
        self.normalize_button.configure(
            state="normal" if enabled else "disabled",
            fg_color=ACCENT if enabled else BG,
            hover_color=ACCENT_HOVER if enabled else BG,
            border_width=0 if enabled else 1)


if __name__ == "__main__":
    App().mainloop()