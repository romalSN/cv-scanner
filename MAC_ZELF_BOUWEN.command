#!/bin/bash
# CV Scanner zelf bouwen en testen op een Mac met M-chip (M1/M2/M3/M4).
# Dubbelklik dit bestand (of: rechtermuisknop > Open). Duurt ±10-20 minuten. Er gaat geen cv naar internet.
set -u
MAP="$HOME/CV-Scanner-bouwen"
LOG="$MAP/bouwlog.txt"
mkdir -p "$MAP"; cd "$MAP" || exit 1
echo "== CV Scanner bouwen op deze Mac ==" | tee "$LOG"
stop() { echo; echo "MISLUKT: $1" | tee -a "$LOG"; echo "Stuur het bestand $LOG naar Romal."; read -r -p "Druk op Enter om te sluiten…"; exit 1; }

# 1. Python 3.10 of nieuwer nodig (python.org-installatie)
PY=""
for p in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY="$p"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.12 ontbreekt. De downloadpagina gaat nu open: installeer 'macOS 64-bit universal2 installer',"
  echo "en dubbelklik daarna dit bestand opnieuw."
  open "https://www.python.org/downloads/macos/"
  read -r -p "Druk op Enter om te sluiten…"; exit 1
fi
echo "Python: $("$PY" --version)  ($(uname -m))" | tee -a "$LOG"
[ "$(uname -m)" = "arm64" ] || echo "Let op: dit is geen M-chip-Mac; de app wordt voor $(uname -m) gebouwd." | tee -a "$LOG"

# 2. Nieuwste code ophalen
echo "1/5 Nieuwste versie ophalen…"
rm -rf code main.zip
curl -sL --fail -o main.zip https://github.com/romalSN/cv-scanner/archive/refs/heads/main.zip >>"$LOG" 2>&1 || stop "code downloaden lukte niet (internet?)"
unzip -q main.zip && mv cv-scanner-main code || stop "uitpakken lukte niet"
cd code || exit 1

# 3. Eigen Python-omgeving + onderdelen
echo "2/5 Onderdelen installeren (eenmalig wat langer)…"
"$PY" -m venv "$MAP/venv" >>"$LOG" 2>&1 || stop "venv maken lukte niet"
V="$MAP/venv/bin/python"
"$V" -m pip install --upgrade pip >>"$LOG" 2>&1
"$V" -m pip install pyinstaller cryptography pillow certifi openpyxl >>"$LOG" 2>&1 || stop "onderdelen installeren lukte niet"
IDX=cpu; [ "$(uname -m)" = "arm64" ] && IDX=metal
"$V" -m pip install llama-cpp-python --prefer-binary --no-cache-dir \
    --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/$IDX" >>"$LOG" 2>&1 \
  || "$V" -m pip install llama-cpp-python --no-binary llama-cpp-python --no-cache-dir >>"$LOG" 2>&1 \
  || stop "AI-onderdeel (llama-cpp-python) installeren lukte niet"

# 4. Testen (zonder echt AI-model)
echo "3/5 Testen…"
export CV_AI=0
TESTS="tests/test_basis.py tests/test_kandidaten.py tests/test_regels.py"
[ "$(uname -s)" = "Darwin" ] && TESTS="$TESTS tests/test_mac_lezen.py"
for t in $TESTS; do
  "$V" "$t" > test_uit.txt 2>&1; RC=$?
  cat test_uit.txt >> "$LOG"
  [ $RC -eq 0 ] || stop "test $t mislukt: $(grep -E '^FOUT' test_uit.txt | head -3 | tr '\n' ' ')"
  echo "   OK  $t"
done

# 5. App bouwen
echo "4/5 App bouwen…"
"$V" -m PyInstaller --noconfirm --windowed --name "CV Scanner" --paths . \
  --hidden-import gui --hidden-import cv_crawler --hidden-import reader_worker \
  --hidden-import rtf_reader --hidden-import pages_reader --hidden-import certifi \
  --collect-data certifi --collect-submodules pypdf \
  --hidden-import ai_rechter --collect-all llama_cpp \
  --osx-bundle-identifier nl.solutionnow.cvscanner start_app.py >>"$LOG" 2>&1 || stop "bouwen lukte niet"

# 6. Gebouwde app testen
echo "5/5 Gebouwde app testen…"
APP="dist/CV Scanner.app/Contents/MacOS/CV Scanner"
[ "$(uname -s)" = "Darwin" ] || APP="dist/CV Scanner/CV Scanner"     # alleen voor de proef op Linux
"$V" tests/maak_frozen_set.py "$MAP/testset" >>"$LOG" 2>&1
"$APP" --backend --root "$MAP/testset" --output "$MAP/testuit" > app_uit.txt 2>&1
cat app_uit.txt >> "$LOG"
grep -q "CV GEVONDEN" app_uit.txt || stop "de gebouwde app vond geen cv's in de testset"
"$APP" --ssl-test > ssl.txt 2>&1; cat ssl.txt >> "$LOG"
grep -q "SSL OK" ssl.txt || stop "internet (https) werkt niet in de gebouwde app"

# Klaar: app in Programma's (of in de map als dat niet mag)
DOEL="/Applications/CV Scanner.app"
WAAR="$MAP/code/dist/CV Scanner.app"
if [ "$(uname -s)" = "Darwin" ]; then rm -rf "$DOEL" 2>/dev/null; ditto "dist/CV Scanner.app" "$DOEL" 2>/dev/null && WAAR="$DOEL"; fi
echo
echo "KLAAR. Getest en gebouwd voor $(uname -m)." | tee -a "$LOG"
echo "De app staat hier: $WAAR"
[ "$(uname -s)" = "Darwin" ] && open -R "$WAAR"
read -r -p "Druk op Enter om te sluiten…"
