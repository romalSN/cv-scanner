"""Maakt een 'gescande' pdf (alleen een plaatje, geen tekst) en test de ingebouwde OCR."""
import sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
import cv_crawler as c

if sys.platform not in ('win32', 'darwin'):
    print('OCR-test overgeslagen (geen Windows/Mac)'); sys.exit(0)

lines = ['Curriculum Vitae', 'Marloes Testgegeven', 'marloes.test@voorbeeldmail.nl',
         'Werkervaring', '2021 - heden Projectleider bij Bouwbedrijf Testhuis', 'Opleiding', 'Hogeschool Rotterdam']
img = Image.new('RGB', (1700, 2200), 'white')
d = ImageDraw.Draw(img)
font = None
for f in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf', 'C:/Windows/Fonts/arial.ttf'):
    try:
        font = ImageFont.truetype(f, 54); break
    except OSError:
        pass
font = font or ImageFont.load_default()
for i, line in enumerate(lines):
    d.text((120, 150 + i * 110), line, fill='black', font=font)
with tempfile.TemporaryDirectory() as tmp:
    pdf = Path(tmp) / 'scan_0001.pdf'
    img.save(pdf, 'PDF', resolution=200)
    try:
        direct = c.native_ocr(pdf, 1)
    except Exception as exc:
        direct = f'FOUT {type(exc).__name__}: {exc}'
    try:
        text, note, pages = c.read_document(pdf, use_ocr=True)
    except Exception as exc:
        text, note = '', f'FOUT {type(exc).__name__}: {exc}'
    print('NOTE:', note)
    print(text)
    if not ('Marloes' in text and 'Testhuis' in text):
        flat = lambda x: ' / '.join(str(x).split())[:900]
        print(f'::error::OCR mislukt. note={flat(note)} | tekst={flat(text)} | native={flat(direct)}')
        sys.exit(1)
print('OCR OK')
