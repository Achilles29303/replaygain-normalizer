@echo off
REM Gera o executavel final. Rode este arquivo com duplo clique.
REM --onefile: um unico .exe | --windowed: sem console preto atras da janela
REM --collect-all: arrasta os arquivos de dados das libs (temas do CTk, Tcl do DnD)
REM --add-data "rsgain;rsgain": empacota o motor; sys._MEIPASS o encontra em runtime
REM --icon: o icone que o Windows mostra no arquivo .exe e na barra de tarefas
REM %LOCALAPPDATA% em vez do caminho completo: funciona em qualquer usuario do Windows
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m PyInstaller ^
  --onefile --windowed --clean --noconfirm ^
  --name "ReplayGain Normalizer" ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --add-data "rsgain;rsgain" ^
  --add-data "assets;assets" ^
  --icon "assets\icon.ico" ^
  app.py
echo.
echo Build finished. The executable is in the dist folder.
pause
