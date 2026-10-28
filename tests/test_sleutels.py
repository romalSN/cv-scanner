"""Sleutelbestand naast de app / in Downloads wordt op de vaste plek gezet (zelfde als zelf invullen) en weggehaald.
Alleen nep-sleutels."""
import os, sys, tempfile
from pathlib import Path
tmp = Path(tempfile.mkdtemp())
os.environ['HOME'] = str(tmp); os.environ['USERPROFILE'] = str(tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gui  # noqa: E402
fout = []
def check(naam, ok):
    print(('OK   ' if ok else 'FOUT ') + naam)
    if not ok: fout.append(naam)
gui.HOME = tmp; gui.RUNTIME = tmp / '.cv-scanner-windows'
gui.KEY_FILE = gui.RUNTIME / 'tavily_sleutel.txt'; gui.SERPER_FILE = gui.RUNTIME / 'serper_sleutel.txt'
gui.SERPAPI_FILE = gui.RUNTIME / 'serpapi_sleutel.txt'
gui.SLEUTEL_SOORTEN = {'tavily': gui.KEY_FILE, 'serper': gui.SERPER_FILE, 'serpapi': gui.SERPAPI_FILE}
(tmp / 'Downloads' / 'CV-Scanner-Mac').mkdir(parents=True)
f = tmp / 'Downloads' / 'CV-Scanner-Mac' / gui.SLEUTELBESTAND
f.write_text('tavily = tvly-NEP123\nserper=NEPSERPER\nserpapi=NEPSERPAPI\n', encoding='utf-8')
gezet = gui.sleutels_importeren()
check('alle drie geïnstalleerd', sorted(gezet) == ['serpapi', 'serper', 'tavily'])
check('Tavily op dezelfde plek als zelf invullen (load_key)', gui.load_key() == 'tvly-NEP123')
check('Serper op de vaste plek (load_serper)', gui.load_serper() == 'NEPSERPER')
check('SerpApi op de vaste plek (load_serpapi)', gui.load_serpapi() == 'NEPSERPAPI')
check('sleutelbestand weggehaald uit Downloads', not f.exists())
check('tweede keer: niets te doen', gui.sleutels_importeren() == [])
print('SLEUTELS OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
