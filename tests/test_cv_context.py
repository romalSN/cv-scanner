"""Test expliciete CV-map/kop, opleidingen en bestandsnaamnamen.
Geen vacature, leeg sjabloon of naamloze cv mag kandidaat-identiteiten verzinnen.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault('CV_AI', '0')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv_crawler as c


def beoordeling(text, filename, path, expected, expected_name=None, mails=None):
    mails = mails or []
    name = c.guess_name(text, filename, mails)
    score, reden = c.classify(text, filename, name, mails, [], [], source_path=path)
    status = c.decide_status(text, filename, name, mails, [], [], score, source_path=path)
    assert status == expected, (filename, name, status, expected, score, reden)
    if expected_name is not None:
        assert name == expected_name, (filename, name, expected_name)
    return name


body = ("Profiel\nIk bouw applicaties en werk aan digitale projecten met klanten.\n"
        "Opleiding\n2012 - 2016 HBO Informatica, Hogeschool Utrecht.\n"
        "Vaardigheden\nPython, software-ontwikkeling en communicatie met collega's.")
beoordeling(body, 'cv_MarcHorstmanNL_2026',
            "/Users/marcel/Downloads/cv/cv_MarcHorstmanNL_2026.docx",
            'cv', 'Marc Horstman')
assert c.naam_uit_bestandsnaam('cv_MarcHorstmanNL_2026', c.normalized_snippet('MarcHorstman werkervaring')) == 'Marc Horstman'

# Geen contactgegevens en geen werkervaring: opleidingen + profiel/vaardigheden volstaan.
beoordeling("Profiel\nErvaren medewerker met leidinggevende taken en verschillende projecten.\n"
            "Opleiding\nHBO bedrijfskunde en verdere cursussen en trainingen.\n"
            "Vaardigheden\nOrganiseren, samenwerken en leidinggeven.",
            'CV - Anne de Boer', '/Users/marcel/Downloads/CVs/CV - Anne de Boer.pdf',
            'cv', 'Anne de Boer')

# Wel een naam, contact en echte CV-kop, maar geen losse herkenbare secties.
beoordeling("Curriculum Vitae\nJacques van Nes\nMail: jacques.vannes@gmail.com\n"
            "Ik werk aan informatiesystemen en heb opleidingen gedaan.",
            "C.V.   Jacques van Nes NL (6)", "/Users/marcel/Downloads/fusion/C.V. Jacques van Nes NL (6).pdf",
            'cv', 'Jacques van Nes', ['jacques.vannes@gmail.com'])

# Een naamloze CV met vier kopjes en meerdere werkperiodes is nog steeds een CV-document.
zonder_naam = ("Profiel\nSoftwareontwikkelaar met jaren ervaring.\n"
              "Opleiding\nHBO Informatica\n"
              "Vaardigheden\nPython, Java, databases\n"
              "Werkervaring\n2014 - 2016 Developer bij Codewerk\n"
              "2016 - 2018 Developer bij Havenbouw\n"
              "2018 - 2020 Developer bij Datafabriek")
beoordeling(zonder_naam, "001b388273", "/Users/marcel/ICTERGEZOCHT CV'S/001b388273.pdf",
            'cv', '', ['kandidaat@privemail.nl'])

# Een enkel kopje + CV-map is onvoldoende, ook met een bestandsnaam.
beoordeling("Profiel\nIk ben een ervaren specialist in verschillende projecten.",
            "CV - Piet Jansen", "/Users/marcel/CVs/CV - Piet Jansen.pdf", 'review')

# Bestandsnaam die een functie is mag nooit worden ingevuld als een persoonsnaam.
beoordeling(body, "CV Java Developer", "/Users/marcel/CVs/CV Java Developer.pdf",
            'review', '')

# Vacatures zijn afwijzing, geen review, zelfs in CV-map.
beoordeling("Vacature Java Developer\nPiet Jansen\n"
            "Opleiding\nHBO Informatica\nProfiel\nKennis van Python vereist.\n"
            "Werkervaring\nMinimaal drie jaar.",
            "CV - Piet Jansen", "/Users/marcel/CVs/CV - Piet Jansen.pdf", 'other')
# OCR: een volledig onleesbare afbeelding wordt niet zonder bewijs als CV goedgekeurd.
beoordeling("", "cv_scan", "/Users/marcel/CVs/cv_scan.pdf", 'other', '')
print('CV-CONTEXT EN BESTANDSNAAM: OK')
