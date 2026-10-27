@echo off
setlocal
cd /d "%~dp0"
title CV Scanner Lenovo - V12

REM Free-threaded Python (3.13t / 3.14t): zet de GIL aan voor stabiliteit.
set PYTHON_GIL=1
set PYTHONFAULTHANDLER=1
REM Unicode voor de hele scanner, ook bij bestandsnamen met emoji en accenten.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8:backslashreplace

echo CV Scanner voor Lenovo / Windows - versie 12
echo PDF-lezer is ingebouwd. Geen PDF-installatie nodig.
echo.
if not exist "%~dp0pypdf\__init__.py" (
 echo FOUT: Het ZIP-bestand is niet volledig uitgepakt.
 echo Pak het HELE ZIP-bestand uit in een map en probeer opnieuw.
 pause
 exit /b 1
)

if not exist "%~dp0reader_worker.py" (
 echo FOUT: reader_worker.py ontbreekt. Pak het volledige ZIP-bestand uit.
 pause
 exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys,tkinter; assert sys.version_info >= (3, 10)" >nul 2>nul
  if not errorlevel 1 (
    py -3 "%~dp0gui.py"
    if errorlevel 1 goto fout
    exit /b 0
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys,tkinter; assert sys.version_info >= (3, 10)" >nul 2>nul
  if not errorlevel 1 (
    python "%~dp0gui.py"
    if errorlevel 1 goto fout
    exit /b 0
  )
)

echo Python 3.10 of nieuwer met Tkinter is niet gevonden.
echo Installeer de gewone Python voor Windows via https://www.python.org/downloads/windows/
echo Kies de optie Add python.exe to PATH, indien aangeboden.
pause
exit /b 1

:fout
echo De scanner kon niet starten. Stuur een foto van de fout boven deze regel.
pause
exit /b 1
