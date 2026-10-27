"""Maakt een map met 3 soorten cv's om de GEBOUWDE app (.exe/.app) mee te testen:
gewone tekst, een Pages-document en (op Mac/Windows) een gescande pdf zonder tekst."""
import shutil, sys
from pathlib import Path
doel = Path(sys.argv[1]); doel.mkdir(parents=True, exist_ok=True)
hier = Path(__file__).resolve().parent
shutil.copy(hier / 'voorbeeld' / 'CV Lotte Verbeek.txt', doel)
regels = ['Curriculum Vitae', 'Marloes Testgegeven', 'marloes.test@voorbeeldmail.nl | 06-00000777 | Utrecht',
          'Werkervaring', '2021 - heden Projectleider bij Bouwbedrijf Testhuis',
          '2018 - 2021 Werkvoorbereider bij Aannemer Fictief', 'Opleiding', '2014 - 2018 Hogeschool Rotterdam, Bouwkunde']
pg = doel / 'CV Marloes Pages.pages'; pg.mkdir()
(pg / 'index.xml').write_text('<sl:document xmlns:sl="x" xmlns:sf="y">' +
                              ''.join(f'<sf:p>{r}</sf:p>' for r in regels).replace('Marloes Testgegeven', 'Pieter Paginaman')
                              .replace('marloes.test', 'pieter.pagina') + '</sl:document>', encoding='utf-8')
if sys.platform == 'darwin':
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('RGB', (1700, 2200), 'white'); d = ImageDraw.Draw(img)
    font = None
    for f in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf'):
        try:
            font = ImageFont.truetype(f, 54); break
        except OSError:
            pass
    for i, r in enumerate(regels):
        d.text((120, 150 + i * 110), r, fill='black', font=font or ImageFont.load_default())
    img.save(doel / 'scan_0001.pdf', 'PDF', resolution=200)
print('testmap klaar:', doel)
