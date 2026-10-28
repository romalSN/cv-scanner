"""AI-vraag 'woont deze persoon NU in NL/BE?' op lastige gevallen (alleen fictieve personen).
Gevaarlijk = AI zegt 'nee' terwijl iemand wel in NL/BE woont (dan zou hij onterecht wegvallen)."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI_MODEL'] = sys.argv[1]
os.environ['CV_AI'] = '1'
naam = sys.argv[2]
import ai_rechter  # noqa: E402
import cv_crawler as c  # noqa: E402
E = ("Experience\n2019 - present Software engineer at Bitbouw. Responsible for the backend services.\n"
     "2014 - 2019 Developer at Infosys, Pune, India. Developed internal tools.\nEducation\nB.Tech Computer Science")
G = [  # (tekst, waarheid: ja = woont in NL/BE, nee = woont erbuiten, ? = niet te zeggen)
    ("Vikram Rao\nBangalore, India\n+31 6 00000901\n" + E.replace('Bitbouw', 'Bitbouw, Rotterdam'), '?'),
    ("Arjun Mehta\nHuidig adres: Kerkstraat 12, 2011 AB Haarlem\nVorig adres: MG Road 5, Pune, India\n+91 98450 12345\n" + E, 'ja'),
    ("Arjun Mehta\nCurrent address: MG Road 5, Bangalore, India\nPrevious address: Damrak 1, Amsterdam\n+91 98450 12345\n" + E, 'nee'),
    ("Arjun Mehta\nAddress: Pune, India (until 2020)\nAddress: Damrak 1, 1012 LG Amsterdam (since 2021)\n" + E, 'ja'),
    ("Arjun Mehta\nAdres: Pune, India\nAdres: Damrak 1, Amsterdam\n" + E, '?'),
    ("Ola Nordmann\nBergen\n+47 400 00 000\n" + E, '?'),
    ("Ola Nordmann\nBergen, Norway\n+47 400 00 000\nWilling to relocate to the Netherlands\n" + E, 'nee'),
    ("Rahul Sharma\n+91 98450 12345\nrahul.test@gmail.com\n" + E.replace('Bitbouw', 'Bitbouw, Amsterdam'), '?'),
    ("Rahul Sharma\n+91 98450 12345\nNationality: Indian\nBorn in Delhi\nUtrecht\n" + E, 'ja'),
    ("Jan de Vries\nLondon, United Kingdom\n+44 7700 900123\njan@devries.nl\n" + E, 'nee'),
    ("Lies Peeters\nMeir 10, 2000 Antwerpen\n+32 470 00 00 01\n" + E.replace('Bitbouw', 'Bitbouw, Rotterdam'), 'ja'),
    ("Bram Dekker\nBreda\n" + E.replace('Bitbouw', 'Haventech, Antwerpen'), 'ja'),
    ("Rahul Sharma\n1012 AB, Bangalore, India\n" + E, '?'),
    ("Sanne Visser\nsanne.test@gmail.com\n" + E, '?'),
]
gevaarlijk, goed, t0 = [], 0, time.time()
for i, (tekst, waar) in enumerate(G, 1):
    a = ai_rechter.filter_vraag('woont', c.normalized_snippet(tekst))
    if a == 'nee' and waar != 'nee':
        gevaarlijk.append(i)
    if (waar == 'ja' and a == 'ja') or (waar == 'nee' and a == 'nee') or (waar == '?' and a != 'nee'):
        goed += 1
    print(f"{naam} {i:2d} waar={waar} ai={a}")
print(f"::notice::{naam} | WOONT: {goed}/{len(G)} goed, ONTERECHT 'woont buiten NL/BE': {gevaarlijk}, "
      f"{(time.time() - t0) / len(G):.1f}s per geval")
