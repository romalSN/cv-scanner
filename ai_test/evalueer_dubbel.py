"""AI: zijn twee cv's van dezelfde persoon? Alleen verzonnen personen. Gevaarlijk = 'zelfde' bij twee VERSCHILLENDE
personen (dan worden twee mensen één kandidaat). Telt ook hoeveel echte dubbelen de AI herkent."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI_MODEL'] = sys.argv[1]
os.environ['CV_AI'] = '1'
naam = sys.argv[2]
import ai_rechter  # noqa: E402
import cv_crawler as c  # noqa: E402


def cv(naam, mail, tel, banen, opl, extra=''):
    regels = [naam, mail, tel, 'Werkervaring'] + [f'{a} - {b} {f} bij {w}. Verantwoordelijk voor {t}.' for a, b, f, w, t in banen]
    regels += ['Opleiding'] + opl + ([extra] if extra else [])
    return c.normalized_snippet('\n'.join(regels))


B1 = [('2021', 'heden', 'Senior Java developer', 'Codewerk', 'de backend van het betaalplatform'),
      ('2017', '2021', 'Java developer', 'Datafabriek', 'koppelingen met Kafka en Spring Boot')]
B1_oud = B1[1:]
B1_en = [('2021', 'present', 'Senior Java developer', 'Codewerk', 'the backend of the payment platform'),
         ('2017', '2021', 'Java developer', 'Datafabriek', 'integrations with Kafka and Spring Boot')]
B2 = [('2020', 'heden', 'Java developer', 'ING', 'de mobiele app'),
      ('2016', '2020', 'Developer', 'Ordina', 'projecten bij klanten')]
B3 = [('2019', 'heden', 'Senior Java developer', 'Codewerk', 'de backend van de webshop'),
      ('2014', '2019', 'Java developer', 'Bitbouw', 'microservices')]
O1 = ['2013 - 2017 HBO Informatica, Hogeschool Utrecht']
O2 = ['2012 - 2016 HBO Informatica, Hogeschool Rotterdam']
G = [  # (cv1, cv2, waar: zelfde/anders)
    (cv('Chris Pahlmann', 'chris.pahlmann@gmail.com', '06 12345678', B1, O1), cv('Christopher Pahlmann', 'chris.pahlmann@gmail.com', '06 12345678', B1_oud, O1), 'zelfde'),
    (cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B1, O1), cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B1_en, ['2013 - 2017 Bachelor Computer Science, Hogeschool Utrecht']), 'zelfde'),
    (cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B1, O1), cv('J. Bakker', 'jan.bakker@gmail.com', '', B1_oud, O1), 'zelfde'),
    (cv('Sanne de Wit', 'sanne.dewit@hotmail.com', '06 22222222', B2, O2), cv('Sanne de Wit', 'sanne.dewit@hotmail.com', '06 22222222', B2[1:], O2), 'zelfde'),
    (cv('Mohamed El Amrani', 'm.elamrani@gmail.com', '06 33333333', B3, O2), cv('Mohamed Amrani', 'm.elamrani@gmail.com', '06 33333333', B3, O2, 'Rijbewijs B'), 'zelfde'),
    (cv('Kees Mol', 'kees.mol@ziggo.nl', '06 44444444', B2, O1), cv('Kees Mol', 'kees.mol@ziggo.nl', '06 44444444', B2 + [('2014', '2016', 'Stagiair', 'Bol', 'testen')], O1), 'zelfde'),
    # verschillende personen die op elkaar lijken
    (cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B1, O1), cv('Jan Bakker', 'jbakker77@hotmail.com', '06 99999999', B2, O2), 'anders'),
    (cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B1, O1), cv('Jan Bakker', 'jan.bakker@gmail.com', '06 11111111', B3, O2), 'anders'),
    (cv('Mark Visser', 'mark.visser@gmail.com', '06 55555555', B1, O1), cv('Mark Visser', 'mark.visser@gmail.com', '06 55555555', B2, O2), 'anders'),
    (cv('Lisa Jansen', 'lisa.jansen@gmail.com', '06 66666666', B3, O1), cv('Lisa Jansen', 'lisa.jansen@gmail.com', '06 66666666', B1, O2), 'anders'),
    (cv('Ahmed Hassan', 'ahmed.hassan@gmail.com', '06 77777777', B2, O1), cv('Ahmed Hassan', 'ahmed.hassan@gmail.com', '', B3, O2), 'anders'),
    (cv('Tom Smit', 'tom.smit@gmail.com', '06 88888888', B1, O1), cv('Tom Smit', 'tom.smit@gmail.com', '06 88888888', B3, O1), 'anders'),
    (cv('Eva Bos', 'eva.bos@gmail.com', '06 12121212', B2, O2), cv('Eva Bos', 'eva.bos@gmail.com', '06 12121212', [('2020', 'heden', 'Verpleegkundige', 'Spaarne Gasthuis', 'zorg op de afdeling')], ['2015 - 2019 HBO Verpleegkunde']), 'anders'),
    (cv('Daan Willems', 'daan.willems@gmail.com', '06 13131313', B1, O1), cv('Daan Willems', 'daan.willems@gmail.com', '06 13131313', B1[:1] + [('2015', '2021', 'Consultant', 'Capgemini', 'adviestrajecten')], O2), 'anders'),
]
gevaarlijk, gemist, t0 = [], [], time.time()
regel = []
for i, (a, b, waar) in enumerate(G, 1):
    r = ai_rechter.zelfde_persoon(a, b) or {'oordeel': 'geen-ai'}
    if r['oordeel'] == 'zelfde' and waar == 'anders':
        gevaarlijk.append(i)
    if waar == 'zelfde' and r['oordeel'] != 'zelfde':
        gemist.append(i)
    regel.append(f"{i}:{waar[:3]}/{r['oordeel'][:3]}")
print(f"::notice::{naam} | DUBBEL: FOUT SAMEN {gevaarlijk} | dubbel niet herkend (blijft twijfel) {gemist} | "
      f"{(time.time() - t0) / len(G):.1f}s per paar")
print(f"::notice::{naam} | " + ' '.join(regel))
