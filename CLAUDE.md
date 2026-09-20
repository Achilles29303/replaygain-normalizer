# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-only dark-mode desktop app (CustomTkinter) that writes **ReplayGain 2.0 tags**
into `.mp3`/`.flac` files by calling the bundled `rsgain.exe`. Audio is never decoded or
re-encoded — only the metadata container — so every run is lossless and undoable.
Full design rationale lives in `docs/PLAN.md`; read it before changing the rsgain contract.

## Commands

```bash
# python is ALWAYS the full path — `python`/`python3` are broken Store stubs
PY="$LOCALAPPDATA/Programs/Python/Python312/python.exe"

"$PY" -m pip install -r requirements.txt
"$PY" tools/get_rsgain.py        # one-time: downloads rsgain/ (not committed)
"$PY" app.py                     # run the GUI
"$PY" engine.py "D:\Music"       # headless: list discovered songs, writes nothing
"$PY" tools/make_icon.py         # regenerate assets/icon.ico
```

Build the exe: `./build.bat` (PyInstaller `--onefile --windowed`, bundles `rsgain/` and
`assets/` as data). `ReplayGain Normalizer.spec` is a generated artifact — edit `build.bat`,
not the spec.

**There is no test suite.** Verification is manual: `engine.py <folder>` for discovery/parsing,
then the real GUI on the `.mp3`/`.flac` files in the repo root, which are gitignored fixtures.

## Architecture

Two modules, one hard boundary:

- **`engine.py`** — file discovery, `subprocess` calls to rsgain, output parsing. **Imports no
  tkinter, ever.** That is what makes it runnable from a terminal; breaking it removes the only
  way to verify the parser without opening a window.
- **`app.py`** — UI only. No subprocess, no parsing.

**The queue is the only bridge between the worker thread and the UI.** `engine.run_batch` pushes
tuples — `("progress", done, total)`, `("log", text)`, `("result", Result)`, `("done", ok, failed)`
— and `App._poll_queue` drains them every 100 ms. Never touch a widget from the worker thread:
tkinter is not thread-safe and the corruption is random and unreproducible.

**One rsgain process per file, deliberately.** rsgain accepts many paths at once, but column 0 of
its output is only the *basename*, and a file that fails produces no row at all — batching would
silently misalign every later result. Per-file invocation gives a 1:1 mapping keyed by the path we
passed in, honest `file N of M` progress, and isolation of a single corrupt or locked file.

**Errors never abort a batch.** A non-zero exit becomes `Result(..., error=...)`, one red row in the
table, and the loop continues.

`engine.base_dir()` returns `sys._MEIPASS` when frozen, else the script folder — every bundled
resource path must go through it, or the `.exe` cannot find `rsgain/`.

## rsgain contract — verified against v3.8 source, not from memory

```
normalize: rsgain.exe custom -s i -l <TARGET> -c p -O -p "<one absolute path>"
undo:      rsgain.exe custom -s d -O -p "<one absolute path>"
```

- `-S` is `--skip-existing`, **not** an ID3v2 flag. Never pass it — always recalculate.
- `-L` writes lowercase tags; uppercase is already the default, so never pass it.
- `-m` in Custom Mode is `--max-peak`, not multithread. Multithreading is Easy-Mode only.
- `-p` preserves mtime when *writing* tags but **not** when deleting them. Known and documented
  in `README.md` and `engine.remove_tags`; do not "fix" it by dropping the flag.

Parsing (7 tab-separated columns, header + one data row):

- Decode stdout as **UTF-8** — digital silence prints `-∞` (U+221E) and breaks cp1252.
- The `Peak (dB)` header cell has a **leading space**; always `.strip()` before comparing headers.
- `-∞` maps to `None`, which the UI renders as an em dash — never as `0.00`.
- rsgain colors errors with ANSI escapes; strip them (`ANSI_ESCAPE`) or the table shows literal junk.

Every `subprocess.run` goes through `engine._run`, which is the single place that sets
`encoding="utf-8"`, `errors="replace"`, `stdin=DEVNULL` (the app has no console) and
`CREATE_NO_WINDOW` (otherwise a black console flashes once per file).

## Conventions

**Language rule, non-negotiable, every file:** UI strings, identifiers and filenames in **English**;
**every comment and docstring in pt-BR**. Comments explain the *why*, not the *what* — roughly one
per logical block, plus a docstring on every function. The repo owner is learning Python and these
comments are the teaching surface, so assume no prior knowledge of threads, subprocess or tkinter.

**Dependencies stay at three** (`customtkinter`, `tkinterdnd2`, `pyinstaller`). Pillow was rejected
on purpose — `tools/make_icon.py` writes the `.ico` with stdlib `zlib` + `struct`. Adding a
dependency needs a reason bigger than convenience.

**Colors are constants at the top of `app.py`.** `ttk.Treeview` is a native widget that ignores
CustomTkinter: it needs `style.theme_use("clam")` plus every color set by hand, or the table renders
white inside the dark window.

**Windows-only by design:** DWM titlebar painting (`paint_titlebar`), `CREATE_NO_WINDOW`, and
`%APPDATA%\ReplayGainNormalizer\config.json` for preferences (not next to the `.exe`, which may sit
in a read-only folder).

**Adding a format** means editing `engine.SUPPORTED_EXTENSIONS` only — rsgain already handles opus,
m4a, wav and more.

## Repo hygiene

- **This repository is public and `.gitignore` is a privacy guard.** It blocks all audio, all images
  (except `docs/screenshot*.png`), `rsgain/`, and build output. Never `git add -f` past it — the
  media files in the root are the owner's music library.
- `rsgain/` is a third-party GPL binary and is **not redistributed**; it is fetched by
  `tools/get_rsgain.py`, which verifies a pinned `SHA256`. Bumping `VERSION` means recomputing that
  hash in the same commit — the script downloads something that then gets executed.
