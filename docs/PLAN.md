# ReplayGain Normalizer — Implementation Plan

## Context

**Problem.** The user's music library plays at inconsistent volumes in AIMP 3. Songs from different sources sit at wildly different loudness levels, forcing constant manual volume adjustment.

**Solution.** A small dark-mode Windows 11 desktop app that measures each song's loudness and writes **ReplayGain 2.0 tags** into the file's metadata. The audio stream is never touched — only tags — so the operation is lossless and fully reversible. AIMP 3 reads those tags at playback and adjusts volume on the fly.

**Outcome.** Point the app at a folder, click Normalize, and every `.mp3`/`.flac` in it gets `REPLAYGAIN_TRACK_GAIN` / `REPLAYGAIN_TRACK_PEAK` tags. Final deliverable is a single double-click `.exe`.

---

## Language rule (non-negotiable — applies to every file)

| Thing | Language |
|---|---|
| UI strings (buttons, labels, dialogs, log messages) | **English** |
| Function names, variable names, class names, file names | **English** |
| **Every comment and every docstring** | **pt-BR (Brazilian Portuguese)** |

The user is a Python learner. Comments must explain the **why**, not restate the code. A comment like `# incrementa o contador` is worthless; `# a fila e' a unica ponte segura entre a thread e a UI` is the target. Assume no prior knowledge of threads, subprocess, or tkinter internals.

**Comment density:** roughly one explanatory comment per logical block (~5–10 lines), plus a pt-BR docstring on every function. Non-obvious lines get their own comment.

---

## Decisions locked in (from the Q&A)

| Question | Decision |
|---|---|
| UI language | **English** (comments pt-BR — see rule above) |
| rsgain binary | **Claude downloads it** and verifies flags empirically |
| Files already tagged | **Always recalculate** — never pass `--skip-existing` |
| Album gain | **Dropped entirely.** Track gain only, always |
| Undo scope | **Only the rows selected in the results table** |
| Library size | **10–100 files per run** → sequential, no thread pool |
| Preferences | **Persist** target LUFS + last folder |
| Export to file | **None.** On-screen table only |

**Naming assumption (one line, easy to change):** window title and exe name = `ReplayGain Normalizer`.

---

## Corrections to the original brief (verified in source, not from memory)

Read `src/rsgain.cpp` (the `custom_mode()` getopt table and `help_custom()`) and `src/scan.cpp` at tag `v3.8`:

1. **`-S` is NOT the ID3v2 version flag.** `-S` = `--skip-existing`. The ID3v2 flag is **`-I`** (`--id3v2-version=keep|3|4`) and `keep` is already the default — so we pass **neither**. Omitting `-S` is also exactly what delivers "always recalculate".
2. **`-m` is NOT multithread in Custom Mode.** It is `--max-peak` (a dB value). Multithreading exists only in Easy Mode. Irrelevant at 10–100 files.
3. **`-O` DOES work in Custom Mode.** The repo README implies it's Easy-Mode-only; the source disagrees — `short_opts = "+aec:m:tdl:O::qps:LSI:o:h?"` and `help_custom()` documents it.
4. **Use v3.8, not v3.7.** Released 2026-09-02; changelog includes *"Windows: work around Microsoft STL bug that can cause program to crash when run in some locales"* — exactly the bug class that bites a pt-BR Windows machine.
5. **`mutagen` is dropped.** It existed only to read ALBUM tags for album-gain detection, which is gone. Stack is now `customtkinter` + `tkinterdnd2` only.
6. **CustomTkinter has no table widget.** Multi-row selection for Undo requires `tkinter.ttk.Treeview` with `selectmode="extended"`, restyled dark by hand.
7. **Uppercase tags are already the default.** `-L` (lowercase) is the opt-in. We never pass it.

---

## The rsgain contract (verified against source)

### Normalize — one process per file

```
rsgain.exe custom -s i -l <TARGET> -c p -O -p "<absolute path to ONE file>"
```

| Flag | Meaning | Why |
|---|---|---|
| `custom` | Custom Mode | Per-file control; Easy Mode forces its own presets |
| `-s i` | tagmode = *insert* | Scan **and write** ReplayGain 2.0 tags |
| `-l N` | target loudness | `-18` / `-16` / `-14` from the dropdown |
| `-c p` | clip protection, positive gains only | Stops a boosted quiet track from clipping |
| `-O` | tab-delimited data to stdout | Our parsing input; also forces `quiet=1` |
| `-p` | preserve mtimes | Keeps "date modified" intact so library sorting isn't scrambled |

### Undo — remove tags

```
rsgain.exe custom -s d -O -p "<absolute path to ONE file>"
```

No `-l` / `-c` (nothing is being measured). `-s d` = tagmode *delete*.

### Output format (exact, from `src/scan.cpp`)

Header printed once per invocation, then one row per file:

```
Filename<TAB>Loudness (LUFS)<TAB>Gain (dB)<TAB>Peak<TAB> Peak (dB)<TAB>Peak Type<TAB>Clipping Adjustment?
song.mp3<TAB>-9.53<TAB>-8.47<TAB>0.988851<TAB>-0.10<TAB>Sample<TAB>N
```

Seven columns, zero-indexed:

| # | Column | Type | Notes |
|---|---|---|---|
| 0 | Filename | str | **Basename only**, not the full path |
| 1 | Loudness (LUFS) | float | Literal `-∞` for digital silence |
| 2 | Gain (dB) | float | The number we care about |
| 3 | Peak | float | Linear, 6 decimals |
| 4 | Peak (dB) | float | Literal `-∞` when peak is 0 |
| 5 | Peak Type | str | `Sample` or `True` |
| 6 | Clipping Adjustment? | str | `Y` or `N` |

**Parsing gotchas, all confirmed in source:**
- `-∞` is U+221E in valid UTF-8 — decode stdout as UTF-8 or the row breaks.
- The `Peak (dB)` header cell has a **leading space**. Never match headers without `.strip()`.
- Column 0 being a basename is *the* reason we call rsgain once per file (below).

### Design decision: one rsgain process per file

Custom Mode accepts many files at once, but batching costs exact progress and safe result mapping — two files named `01 - Intro.mp3` in different subfolders are indistinguishable in the output, and a file that fails to scan produces no row, silently shifting every subsequent one. At 10–100 files the extra process spawns are noise next to decode time, and per-file invocation buys: honest `file N of M` progress, a 1:1 mapping keyed by the full path we passed in, isolation of a single corrupt file, and a natural live log.

---

## Project structure

```
normalizador-de-musicas/
├── app.py              # CustomTkinter UI, queue polling, preferences
├── engine.py           # file discovery, subprocess calls, output parsing
├── rsgain/             # bundled: rsgain.exe + DLLs + LICENSE
├── build.bat           # PyInstaller one-liner
├── requirements.txt    # customtkinter, tkinterdnd2, pyinstaller
└── README.md           # short usage note + AIMP 3 setup steps
```

---

## `engine.py` — pure logic, zero tkinter imports

Keeping this module tkinter-free means it can be run and tested from a plain terminal, which is how step 2 gets verified.

```python
class Result(NamedTuple):
    path: Path          # caminho completo do arquivo processado
    loudness: float | None   # None quando rsgain imprimiu "-inf"
    gain: float | None
    peak: float | None
    peak_db: float | None
    peak_type: str      # "Sample" ou "True"
    clipped: bool       # True quando a coluna 6 e' "Y"
    error: str | None   # mensagem quando algo falhou; None em caso de sucesso
```

| Function | Signature | Responsibility |
|---|---|---|
| `base_dir()` | `-> Path` | Returns `Path(sys._MEIPASS)` when frozen, else the script's folder |
| `rsgain_path()` | `-> Path` | `base_dir() / "rsgain" / "rsgain.exe"`; raises `FileNotFoundError` with a user-readable message if missing |
| `find_songs(folder)` | `(Path) -> list[Path]` | `rglob("*")`, keep `.mp3`/`.flac` case-insensitively, return sorted |
| `_run(args)` | `(list[str]) -> tuple[int, str, str]` | The single place that touches `subprocess` |
| `_parse_row(stdout, path)` | `(str, Path) -> Result` | Skips the header, splits the data row on `\t`, converts `-∞` → `None` |
| `normalize_file(path, target)` | `(Path, int) -> Result` | Builds the `-s i` command, returns a `Result` |
| `remove_tags(path)` | `(Path) -> Result` | Builds the `-s d` command, returns a `Result` |
| `run_batch(paths, target, q, mode)` | `(list[Path], int, Queue, str) -> None` | The worker loop; `mode` is `"normalize"` or `"undo"` |

### Subprocess rules (every call)

```python
subprocess.run(
    args,
    capture_output=True,
    encoding="utf-8",        # rsgain emite "-∞" em UTF-8; sem isso a linha quebra
    errors="replace",        # nunca deixa um byte estranho derrubar o app
    creationflags=subprocess.CREATE_NO_WINDOW,  # evita a janela preta piscando
)
```

A non-zero exit code becomes `Result(..., error=stderr.strip())` — the batch **continues** to the next file. One bad file must never abort the run.

### Queue protocol (the contract between thread and UI)

`run_batch` pushes plain tuples; `app.py` reads them. No other communication exists.

| Message | When |
|---|---|
| `("progress", done, total)` | Before each file starts |
| `("log", text)` | Free-form status line for the log box |
| `("result", Result)` | After each file completes, success or failure |
| `("done", ok_count, fail_count)` | Once, at the end |

---

## `app.py` — UI only

### Root window: the CustomTkinter + tkinterdnd2 combination

A plain `ctk.CTk()` has no `drop_target_register`. The two libraries are joined by inheriting from both:

```python
class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self) -> None:
        super().__init__()
        # tkinterdnd2 precisa carregar sua extensao Tcl no interpretador desta janela.
        # Sem esta linha, drop_target_register simplesmente nao existe.
        self.TkdndVersion = TkinterDnD._require(self)
```

Drop registration and parsing:

```python
self.drop_target_register(DND_FILES)
self.dnd_bind("<<Drop>>", self._on_drop)

def _on_drop(self, event) -> None:
    # event.data chega como lista do Tcl: caminhos com espaco vem entre chaves {}.
    # splitlist e' a unica forma correta de separar — split(" ") quebra nomes com espaco.
    paths = self.tk.splitlist(event.data)
```

Accept the first dropped item; if it is a file rather than a folder, use its parent folder and say so in the log.

### Layout (grid, top to bottom)

1. **Folder row** — read-only `CTkEntry` showing the path + `CTkButton("Select Folder")` (opens `filedialog.askdirectory`). The whole window is a drop target.
2. **Status label** — `"42 songs found"` after a scan.
3. **Controls row** — `CTkOptionMenu` with `["-18 LUFS", "-16 LUFS", "-14 LUFS"]`, plus `CTkButton("Normalize")` and `CTkButton("Undo Selected")`.
4. **Progress row** — `CTkProgressBar` + label `"Processing file 7 of 42"`.
5. **Results table** — `ttk.Treeview`, the app's main area, expands with the window.
6. **Log** — `CTkTextbox`, read-only, auto-scrolls to the bottom.

Buttons disable while a run is in progress and re-enable on `("done", ...)`.

### Results table — dark-styling a ttk widget

```python
style = ttk.Style()
# O tema nativo do Windows IGNORA cores de fundo. "clam" as respeita —
# sem esta linha a tabela sai branca no meio de uma janela escura.
style.theme_use("clam")
style.configure("Treeview",
                background="#2b2b2b", fieldbackground="#2b2b2b",
                foreground="#dce4ee", borderwidth=0, rowheight=26)
style.configure("Treeview.Heading", background="#212121",
                foreground="#dce4ee", borderwidth=0)
style.map("Treeview", background=[("selected", "#1f6aa5")])
```

Columns: `File` · `Loudness (LUFS)` · `Gain (dB)` · `Peak` · `Clipping`. Built with `selectmode="extended"` so Ctrl+click and Shift+click select multiple rows. Keep a `dict[str, Path]` mapping each Treeview item id to its full path — the table shows basenames, but Undo needs absolute paths.

Failed files get inserted with the error text in the Gain column and a `tags=("error",)` marker styled red.

### Threading

```python
# tkinter NAO e' thread-safe: tocar um widget de outra thread trava ou
# corrompe a interface de forma aleatoria e dificil de reproduzir.
# Por isso a thread so' escreve na fila, e a UI le' a fila sozinha.
threading.Thread(target=engine.run_batch, args=(...), daemon=True).start()
self.after(100, self._poll_queue)
```

`_poll_queue` drains with `q.get_nowait()` inside `try/except queue.Empty`, applies each message, and reschedules itself with `self.after(100, self._poll_queue)` until it sees `("done", ...)`.

### Preferences

`%APPDATA%\ReplayGainNormalizer\config.json` — chosen over "next to the .exe" because a portable exe may live in a read-only folder.

```json
{"target_lufs": -18, "last_folder": "D:\\Music", "warning_shown": true}
```

Loaded at startup inside `try/except` — a corrupt or missing file falls back to defaults silently, never crashes the app.

### Dialogs

- **First run** (`warning_shown` absent/false): *"Tags will be written to your files. Test on a copy first."* — then set the flag.
- **Undo confirm:** *"Remove ReplayGain tags from 12 selected file(s)? This cannot be undone."* The Undo button stays disabled while the selection is empty.

---

## Build order — stop for approval after each step

**Step 0 — Setup.** Download `rsgain-3.8-win64.zip` from the official GitHub release, extract to `rsgain/`, run `rsgain.exe --version` and `rsgain.exe custom -h`, and paste the real output. Every flag above gets confirmed against that output before any code depends on it. Also: `git init` + first commit, and save the pt-BR-comments preference to memory.

**Step 1 — Window, folder picker, drag-and-drop.** Dark window, folder entry + button, whole-window drop target, LUFS dropdown, "N songs found" counter. No processing yet.

**Step 2 — Normalize, progress bar, live log.** Build `engine.py` and test it from the terminal *first*, then wire it to the UI through the queue.

**Step 3 — Results table.** Dark-styled `ttk.Treeview` fed by `("result", ...)` messages.

**Step 4 — Undo.** Multi-select, confirm dialog, `-s d` per selected file, rows visually marked as cleared.

**Step 5 — `build.bat` → `.exe`.**

```bat
@echo off
REM --onefile: um unico .exe | --windowed: sem console preto atras da janela
REM --collect-all: arrasta os arquivos de dados das libs (temas do CTk, Tcl do DnD)
REM --add-data "rsgain;rsgain": empacota o motor; sys._MEIPASS o encontra em runtime
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m PyInstaller ^
  --onefile --windowed --clean ^
  --name "ReplayGain Normalizer" ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --add-data "rsgain;rsgain" ^
  app.py
pause
```

**Step 6 — AIMP 3 setup guide.** Short steps for enabling ReplayGain in AIMP 3 (track mode, prevent clipping). The exact menu path gets **verified against AIMP 3 documentation** before being written down — not recalled from memory.

---

## Verification — evidence per step, no "it compiles"

1. **rsgain flags:** paste real `custom -h` output; confirm `-s i`, `-s d`, `-l`, `-c p`, `-O`, `-p` exist as described.
2. **Parser, no music required:** generate two short WAVs with Python's stdlib `wave` module — one near-silent, one loud (rsgain supports WAV) — plus one named `Canção.wav` to test accented filenames through the pipe. Run `normalize_file()` on each and assert the parsed values match the raw stdout, including the `-∞` path.
3. **Real tag write:** ask the user for a folder of **copies** of ~5 songs (mixed .mp3 and .flac). Run the app, then dump the tags to prove `REPLAYGAIN_TRACK_GAIN`/`_TRACK_PEAK` were written, and confirm **file size and mtime unchanged** — the proof that audio was not re-encoded.
4. **Undo round-trip:** select 2 of those 5 rows, Undo, re-dump. Those 2 must have no ReplayGain tags; the other 3 must keep theirs.
5. **UI smoke test:** drag a folder onto the window, watch the bar advance file by file, confirm no console window flashes, confirm every filename appears in the log.
6. **Frozen exe:** run the built `.exe` from a *different directory* than the source, on a fresh folder, confirming it finds its bundled `rsgain.exe` via `sys._MEIPASS`.
7. **AIMP 3:** play a normalized track and confirm the gain is applied.

**Destructive-action gate:** steps 3 and 4 write and delete tags on real files. The exact folder path gets confirmed with the user before either runs, and it must be a copy.

---

## Open TODOs (logged, not silently fixed)

- **Cancel mid-run:** out of scope. If wanted later, a `threading.Event` passed into `run_batch` and checked at the top of each loop iteration is the minimal addition.
- **Formats beyond mp3/flac:** rsgain also handles Opus, M4A, WAV, WavPack and more. The `SUPPORTED_EXTENSIONS` constant in `engine.py` is the single place to widen it.
- **Windows file locks:** if AIMP has a song open, the tag write may fail with a permission error. It will surface as a per-file error row rather than a crash; a retry loop is not planned unless it actually shows up in testing.
