"""30 verzonnen cv's met verwachte uitkomst (mee/weg) voor de filters taal, woonplaats en 5 jaar NL/BE.
Doel: 0 onterechte uitsluitingen en de duidelijke gevallen wel eruit."""
import os, sys, datetime as dt
from pathlib import Path
os.environ.setdefault('CV_AI', '0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv_crawler as c  # noqa: E402
Y = dt.date.today().year
W = "Ik werk met het team aan de ontwikkeling van systemen en help klanten met hun vragen over software."
E = "I work with the team on the development of systems and help clients with questions about the software."
C = [  # (naam, tekst, telefoons, emails, verwacht)
 ('nl compleet', f"Curriculum Vitae\nJan Bakker\nKerkstraat 1\n2011 AB Haarlem\n06-12345678\nWerkervaring\n{Y-9} - heden Developer bij Datafabriek, Haarlem. {W}\nOpleiding\n{Y-13} - {Y-9} HBO Informatica, Hogeschool van Amsterdam", ['+31612345678'], [], 'mee'),
 ('nl geen adres', f"Curriculum Vitae\nSanne de Wit\nsanne@gmail.com\nWerkervaring\n{Y-3} - heden Analist bij Rekenhuis. {W}", [], ['sanne@gmail.com'], 'mee'),
 ('nl geen jaren', f"Curriculum Vitae\nTom Jansen\nWerkervaring\nDeveloper bij Codewerk. {W}\nOpleiding\nMBO ICT", [], [], 'mee'),
 ('be antwerpen', f"Curriculum Vitae\nLies Peeters\nMeir 10, 2000 Antwerpen\n+32 470 00 00 01\nWerkervaring\n{Y-7} - heden Developer bij Havenbouw, Rotterdam. {W}", ['+32470000001'], [], 'mee'),
 ('nl woont, werkt in be', f"Curriculum Vitae\nBram Dekker\nBreda\n06-00000805\nWerkervaring\n{Y-6} - heden Developer bij Haventech, Antwerpen. {W}", ['+31600000805'], [], 'mee'),
 ('engels cv nl', f"Curriculum Vitae\nJohn Smith\nUtrecht\n+31 6 11111111\nExperience\n{Y-6} - present Engineer at Bol, Utrecht. {E}", ['+31611111111'], [], 'mee'),
 ('indiase naam nl 9j', f"Resume\nRajesh Kumar\nAmsterdam\n+31 6 22222222\nExperience\n{Y-9} - present Developer, Adyen, Amsterdam. {E}\nEducation\n{Y-13} - {Y-9} B.Tech, Anna University, Chennai", ['+31622222222'], [], 'mee'),
 ('alleen 2j werk', f"Resume\nPriya Nair\npriya@gmail.com\nExperience\n{Y-2} - present Developer, Adyen, Amsterdam. {E}", [], [], 'mee'),
 ('indiaas werk ervoor, 3j nl', f"Resume\nArjun Mehta\nAmsterdam\n+31 6 33333333\nExperience\n{Y-3} - present Developer, Adyen, Amsterdam. {E}\n{Y-9} - {Y-3} Developer, Infosys, Pune, India. {E}", ['+31633333333'], [], 'mee'),
 ('4,5 jaar', f"Resume\nArjun Rao\nAmsterdam\nExperience\n{Y-4} - present Developer, Adyen, Amsterdam. {E}\n{Y-9} - {Y-4} Developer, Infosys, Pune, India. {E}", [], [], 'mee'),
 ('school nl dan londen', f"CV\nJan de Vries\nUtrecht\nOpleiding\n{Y-16} - {Y-10} Havo, Utrecht\nWerkervaring\n{Y-10} - {Y-7} Developer bij Monzo, London. {W}\n{Y-7} - heden Developer bij Datafabriek, Utrecht. {W}", [], [], 'mee'),
 ('nu in londen, nl-band', f"CV\nJan de Vries\nLondon, United Kingdom\n+44 7700 900123\nWerkervaring\n{Y-2} - heden Developer bij Monzo. {W}\n{Y-10} - {Y-2} Developer bij Datafabriek, Utrecht. {W}", ['+447700900123'], [], 'mee'),
 ('remote buitenlandse werkgever, be', f"CV\nAn Claes\nGent\n+32 470 22 22 22\nExperience\n{Y-6} - present Developer at Google (remote) from Belgium. {E}", ['+32470222222'], [], 'mee'),
 ('overlap 2 banen', f"CV\nKees Mol\nZwolle\nWerkervaring\n{Y-5} - heden Developer bij Datafabriek, Zwolle. {W}\n{Y-5} - heden Docent bij ROC, Zwolle. {W}", [], [], 'mee'),
 ('gaten', f"CV\nEva Smit\nWerkervaring\n{Y-14} - {Y-12} Developer bij Datafabriek, Leiden. {W}\n{Y-2} - heden Developer bij Codewerk, Leiden. {W}", [], [], 'mee'),
 ('studieadres buitenland', f"CV\nArjun Pillai\nStudieadres: Anna University Campus, Chennai, India\n06-00000901\nExperience\n{Y-6} - present Developer, Adyen, Amsterdam. {E}", ['+31600000901'], [], 'mee'),
 ('oude buitenl werkgever', f"CV\nKiran Das\nExperience\n{Y-12} - {Y-9} Developer, Infosys, Pune, India. {E}\n{Y-2} - present Developer, Adyen, Amsterdam. {E}", [], [], 'mee'),
 ('geboren in india, nl', f"CV\nArjun Shah\nUtrecht\nNationality: Indian\nBorn in Delhi\nExperience\n{Y-3} - present Developer at Bol, Utrecht. {E}", [], [], 'mee'),
 ('alleen +91 (wens: weg)', f"CV\nRahul Verma\n+91 98450 12345\nExperience\nSoftware developer. {E}", ['+919845012345'], [], 'weg'),
 ('alleen +48', f"CV\nPiotr Nowak\n+48 512 345 678\nExperience\nSoftware developer. {E}", ['+48512345678'], [], 'mee'),
 ('twee adressen onduidelijk', f"CV\nMeena Iyer\nAdres: Pune, India\nAdres: Damrak 1, Amsterdam\nExperience\n{Y-3} - present Developer, Adyen, Amsterdam. {E}", [], [], 'mee'),
 ('huidig nl vorig india', f"CV\nPriya Menon\nHuidig adres: Kerkstraat 12, 2011 AB Haarlem\nVorig adres: MG Road 5, Pune, India\n+91 98450 55555\nExperience\n{Y-3} - present Developer, Spaarnedata, Haarlem. {E}", ['+919845055555'], [], 'mee'),
 ('bergen nh', f"CV\nOla de Boer\nDorpsstraat 1, 1861 AB Bergen\nWerkervaring\n{Y-6} - heden Developer bij Duinsoft, Alkmaar. {W}", [], [], 'mee'),
 ('slecht leesbaar ocr', "CV\nJ0hn Sm1th\nUtr3cht\n0 6 1 2 3\nExp3rience\nDev3loper at B0l 2O19 - pres3nt. " + E, [], [], 'mee'),
 ('tegenstrijdige jaren', f"CV\nMark Vos\nWerkervaring\n{Y} - {Y-6} Developer bij Datafabriek, Utrecht. {W}\n{Y+3} - heden Developer bij Codewerk. {W}", [], [], 'mee'),
 ('meerdere landen', f"CV\nSara Lopez\nRotterdam\n06-44444444\nExperience\n{Y-3} - present Developer, Coolblue, Rotterdam. {E}\n{Y-6} - {Y-3} Developer, BBVA, Madrid, Spain. {E}\n{Y-9} - {Y-6} Developer, SAP, Berlin, Germany. {E}", ['+31644444444'], [], 'mee'),
 # moeten eruit
 ('woont madrid', f"CV\nCarlos Ruiz\nMadrid, Spain\n+34 600 000 000\nExperience\n{Y-3} - present Developer at BBVA, Madrid. {E}", ['+34600000000'], [], 'weg'),
 ('woont india', f"CV\nRahul Sharma\nAddress: MG Road, Bangalore, India\n+91 98450 12345\nExperience\n{Y-6} - present Developer, Infosys, Bangalore. {E}", ['+919845012345'], [], 'weg'),
 ('bergen noorwegen', f"CV\nOla Nordmann\nBergen, Norway\n+47 400 00 000\nExperience\n{Y-6} - present Developer at Fjordsoft. {E}", ['+4740000000'], [], 'weg'),
 ('expliciet sinds vorig jaar', f"CV\nVikram Rao\nAmsterdam\nMoved to the Netherlands in {Y-1}.\nExperience\n{Y-1} - present Developer, Adyen, Amsterdam. {E}\n{Y-8} - {Y-1} Developer, Infosys, Pune, India. {E}", [], [], 'weg'),
]
fout = 0
for naam, t, ph, em, verwacht in C:
    doc = {'snippet': c.normalized_snippet(t), 'taal': c.cv_hoofdtaal(t), 'opleidingen': '', 'werkplaatsen': '', 'sha': 'x',
           'woonplaats': ''}
    n = c.filter_kandidaat(doc, ph, em, lambda s: None)
    uitkomst = 'weg' if n[0] == 'weg' else 'mee'
    ok = uitkomst == verwacht
    fout += not ok
    print(('OK   ' if ok else 'FOUT ') + f"{naam:34s} {n[0]:11s} {n[1][:40]:40s} verwacht={verwacht}")
print(f"\n{len(C)} cv's, {fout} fout")
print('UITSLUITING OK' if not fout else 'FOUT'); sys.exit(1 if fout else 0)
