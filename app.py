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
    "-18 LUFS  ·  ReplayGain standard": -18,
    "-16 LUFS  ·  slightly louder": -16,
    "-14 LUFS  ·  streaming level": -14,
}

# Preferencias no %APPDATA% e nao ao lado do .exe: um executavel portatil pode acabar
# numa pasta somente-leitura (Arquivos de Programas, pendrive), e ai' salvar falharia.
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


class Dialog(ctk.CTkToplevel):
    """Caixa de dialogo modal com a mesma linguagem visual do resto do app.

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
            ctk.CTkButton(buttons, text="Cancel", width=104, height=36,
                          corner_radius=8, fg_color="transparent", border_width=1,
                          border_color=BORDER, hover_color=SURFACE_2,
                          text_color=MUTED, font=ctk.CTkFont("Segoe UI", 12),
                          command=self._cancel).pack(side="right", padx=(8, 0))

        ctk.CTkButton(buttons, text=confirm or "Got it", width=124, height=36,
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
        self.geometry("1000x720")
        self.minsize(860, 620)
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
        w, h = 1000, 720
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
                     text="Writes ReplayGain 2.0 tags \u00b7 metadata only, audio untouched",
                     text_color=MUTED, anchor="w",
                     font=ctk.CTkFont("Segoe UI", 12)
                     ).grid(row=1, column=1, sticky="w")

        ctk.CTkLabel(header, text="  rsgain 3.8  ", height=26, corner_radius=13,
                     fg_color=SURFACE_2, text_color=MUTED,
                     font=ctk.CTkFont("Segoe UI", 11)
                     ).grid(row=0, column=2, rowspan=2, sticky="e")

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
            placeholder_text="Drop a folder anywhere on this window, or browse\u2026",
            font=ctk.CTkFont("Segoe UI", 12))
        self.path_entry.grid(row=0, column=0, sticky="ew")
        self.path_entry.configure(state="readonly")

        ctk.CTkButton(row, text="Browse", width=112, height=42, corner_radius=9,
                      fg_color=SURFACE_2, hover_color=BORDER, text_color=TEXT,
                      border_width=1, border_color=BORDER,
                      font=ctk.CTkFont("Segoe UI", 12),
                      command=self._pick_folder).grid(row=0, column=1, padx=(10, 0))

        self.count_label = ctk.CTkLabel(
            card, text="No folder selected", text_color=MUTED, anchor="w",
            font=ctk.CTkFont("Segoe UI", 11))
        self.count_label.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 16))

    def _build_controls(self) -> None:
        """Linha de acao: alvo de loudness, Normalize e Undo."""
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=28, pady=(14, 0))
        bar.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(left, text="TARGET LOUDNESS", text_color=MUTED,
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
            right, text="Undo Selected", width=150, height=42, corner_radius=9,
            fg_color="transparent", border_width=1, border_color=BORDER,
            hover_color=SURFACE_2, text_color=MUTED,
            font=ctk.CTkFont("Segoe UI", 12),
            state="disabled", command=self._undo_selected)
        self.undo_button.grid(row=0, column=0, padx=(0, 10), pady=(20, 0))

        self.normalize_button = ctk.CTkButton(
            right, text="Normalize", width=164, height=42, corner_radius=9,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#0F1115",
            font=ctk.CTkFont("Segoe UI", 13, "bold"),
            state="disabled", command=self._normalize)
        self.normalize_button.grid(row=0, column=1, pady=(20, 0))

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
            wrap, text="Idle", text_color=MUTED, width=170, anchor="e",
            font=ctk.CTkFont("Segoe UI", 11))
        self.progress_label.grid(row=0, column=1, padx=(14, 0))

    def _build_table(self) -> None:
        """Tabela de resultados: um ttk.Treeview repintado a' mao para o tema escuro."""
        card = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid(row=4, column=0, sticky="nsew", padx=28)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(0, weight=1)

        style = ttk.Style()
        # O tema nativo do Windows IGNORA cores de fundo. O "clam" as respeita —
        # sem esta linha a tabela sai branca no meio de uma janela escura.
        style.theme_use("clam")
        style.configure("RG.Treeview",
                        background=SURFACE, fieldbackground=SURFACE,
                        foreground=TEXT, borderwidth=0, relief="flat",
                        rowheight=30, font=("Segoe UI", 10))
        style.configure("RG.Treeview.Heading",
                        background=SURFACE_2, foreground=MUTED,
                        borderwidth=0, relief="flat",
                        font=("Segoe UI", 9, "bold"), padding=(10, 9))
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

        columns = ("file", "loudness", "gain", "peak", "clip")
        self.tree = ttk.Treeview(card, columns=columns, show="headings",
                                 style="RG.Treeview", selectmode="extended")

        headings = {
            "file": ("File", 400, "w"),
            "loudness": ("Loudness (LUFS)", 130, "e"),
            "gain": ("Gain (dB)", 130, "e"),
            "peak": ("Peak", 110, "e"),
            "clip": ("Clipping", 90, "center"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            # `stretch` so' na coluna do nome: as numericas tem largura previsivel e
            # esticar todas deixaria numeros perdidos no meio de celulas gigantes.
            self.tree.column(key, width=width, anchor=anchor,
                             stretch=(key == "file"), minwidth=70)

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

        ctk.CTkLabel(card, text="ACTIVITY", text_color=MUTED, anchor="w",
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

    def _first_run_warning(self) -> None:
        """Avisa, uma unica vez, que o app grava nos arquivos do usuario."""
        if self.config_data.get("warning_shown"):
            return
        Dialog(self, "Before you start",
               "This app writes ReplayGain tags into your music files. "
               "Only metadata is modified \u2014 the audio stream is never "
               "re-encoded, and every change can be undone.\n\n"
               "Even so, try it on a copy of a few songs first.")
        self.config_data["warning_shown"] = True
        save_config(self.config_data)

    # -- selecao de pasta -----------------------------------------------------

    def _pick_folder(self) -> None:
        """Abre o dialogo nativo de escolha de pasta, comecando na ultima usada."""
        initial = str(self.folder) if self.folder else self.config_data.get("last_folder", "")
        chosen = filedialog.askdirectory(title="Select a music folder",
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
            self._log(f"Dropped a file \u2014 using its folder: {dropped.parent}")
            dropped = dropped.parent
        if not dropped.is_dir():
            self._log(f"Could not use dropped item: {dropped}")
            return

        if len(items) > 1:
            self._log(f"{len(items)} items dropped \u2014 using the first one only.")
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
                text=f"{count} song{'s' if count != 1 else ''} found  \u00b7  .mp3 and .flac, "
                     "including subfolders",
                text_color=SUCCESS)
        else:
            self.count_label.configure(
                text="No .mp3 or .flac files found in this folder", text_color=WARN)

        self._set_busy(False)
        if log:
            self._log(f"Folder: {folder}")
            self._log(f"{count} song(s) found.")

        self.config_data["last_folder"] = str(folder)
        save_config(self.config_data)

    # -- execucao -------------------------------------------------------------

    def _normalize(self) -> None:
        """Dispara a medicao e gravacao de tags de todas as musicas encontradas."""
        if self.running or not self.songs:
            return
        self._clear_table()
        self._log(f"\u2014 Normalizing {len(self.songs)} file(s) to "
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
            self, "Remove ReplayGain tags?",
            f"The ReplayGain tags of {len(paths)} selected file"
            f"{'s' if len(paths) != 1 else ''} will be deleted.\n\n"
            "The audio is not affected, but the measured values are lost \u2014 "
            "you would have to normalize them again.",
            confirm="Remove tags", danger=True)
        if not dialog.result:
            return

        # Guardar quais linhas foram afetadas permite marca-las como limpas depois,
        # em vez de reconstruir a tabela inteira e perder o que ja' estava nela.
        self._undo_rows = {self.row_paths[i]: i for i in selection if i in self.row_paths}
        self._log(f"\u2014 Removing tags from {len(paths)} file(s) \u2014")
        self._start(paths, mode="undo")

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
            Dialog(self, "Audio engine missing", str(exc))
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
                        text=f"File {done} of {total}", text_color=TEXT)
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

        summary = f"Done \u00b7 {ok} ok"
        if failed:
            summary += f", {failed} failed"
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
                                                 result.error, "\u2014", "\u2014"),
                                   tags=("error",))
                else:
                    self.tree.item(item, values=(result.path.name, "\u2014",
                                                 "tags removed", "\u2014", "\u2014"),
                                   tags=("cleared",))
                return

        stripe = "even" if len(self.tree.get_children()) % 2 else "odd"

        if result.error:
            values = (result.path.name, "\u2014", result.error, "\u2014", "\u2014")
            tags = ("error", stripe)
        else:
            # `-inf` (silencio digital) nao tem numero para mostrar; o travessao
            # comunica "nao se aplica" melhor do que um "0.00" enganoso.
            loudness = "\u2014" if result.loudness is None else f"{result.loudness:.2f}"
            gain = "\u2014" if result.gain is None else f"{result.gain:+.2f}"
            peak = "\u2014" if result.peak is None else f"{result.peak:.4f}"
            values = (result.path.name, loudness, gain, peak,
                      "clipped" if result.clipped else "\u2014")
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
            self.normalize_button.configure(state="disabled", text="Working\u2026")
            self.undo_button.configure(state="disabled")
            self.target_menu.configure(state="disabled")
        else:
            self.normalize_button.configure(
                text="Normalize",
                state="normal" if self.songs else "disabled")
            self.target_menu.configure(state="normal")
            self._on_select()


if __name__ == "__main__":
    App().mainloop()
