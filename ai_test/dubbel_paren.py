"""Honderden verzonnen cv-paren (alleen fictieve namen/bedrijven) met bekende waarheid: zelfde persoon of niet.
Gebruikt door evalueer_dubbel.py (AI) en tests (Python)."""
import random

VOOR = ['Jan', 'Piet', 'Kees', 'Mark', 'Tom', 'Lisa', 'Sanne', 'Eva', 'Daan', 'Bram', 'Anouk', 'Sophie', 'Ahmed', 'Mohamed',
        'Fatima', 'Yusuf', 'Rahul', 'Priya', 'Lars', 'Joost', 'Niels', 'Femke', 'Iris', 'Ruben', 'Thijs', 'Koen', 'Ilse']
ACHTER = ['Bakker', 'de Vries', 'Jansen', 'Visser', 'Smit', 'Mulder', 'de Boer', 'Bos', 'Vos', 'Peters', 'Hendriks',
          'van Dijk', 'Kok', 'Meijer', 'de Wit', 'Dekker', 'Brouwer', 'Kaya', 'Yilmaz', 'El Amrani', 'Sharma', 'Willems']
BEDRIJF = ['Codewerk', 'Datafabriek', 'Bitbouw', 'Rekenhuis', 'Havenbouw', 'Spaarnedata', 'Cloudig', 'Testlab',
           'Agilo', 'Duinsoft', 'Fjordsoft', 'Netwerkhuis', 'Brugdata', 'Polderlogic', 'Tulpsoft']
GROOT = ['ING', 'Rabobank', 'Albert Heijn', 'Philips', 'KPN']
FUNCTIE = ['Java developer', 'Data analist', 'Tester', 'Scrum master', 'DevOps engineer', 'Projectleider',
           'Frontend developer', 'Functioneel beheerder']
FUNCTIE_EN = {'Java developer': 'Java developer', 'Data analist': 'Data analyst', 'Tester': 'Test engineer',
              'Scrum master': 'Scrum master', 'DevOps engineer': 'DevOps engineer', 'Projectleider': 'Project manager',
              'Frontend developer': 'Front-end developer', 'Functioneel beheerder': 'Functional administrator'}
SCHOOL = ['Hogeschool Utrecht', 'Hogeschool van Amsterdam', 'Hogeschool Rotterdam', 'Fontys', 'Saxion', 'Avans']
TAAK = ['de backend van het platform', 'rapportages en dashboards', 'testautomatisering', 'de migratie naar de cloud',
        'koppelingen met externe systemen', 'het beheer van de applicaties']


def persoon(r):
    v, a = r.choice(VOOR), r.choice(ACHTER)
    jaar = r.randint(2005, 2012)
    banen, y = [], jaar
    for _ in range(r.randint(2, 4)):
        lengte = r.randint(2, 5)
        banen.append([y, y + lengte, r.choice(FUNCTIE), r.choice(BEDRIJF), r.choice(TAAK)])
        y += lengte
    banen[-1][1] = 'heden'
    mail = f"{v.lower()}.{a.lower().replace(' ', '')}{r.randint(1, 99)}@gmail.com"
    tel = f"06 {r.randint(10000000, 99999999)}"
    return dict(voor=v, achter=a, mail=mail, tel=tel, banen=banen[::-1], school=r.choice(SCHOOL), sjaar=jaar - 4)


def tekst(p, engels=False, naam=None, mail=None, tel=None, banen=None):
    naam = naam or f"{p['voor']} {p['achter']}"
    regels = ['Curriculum Vitae', naam, mail if mail is not None else p['mail'], tel if tel is not None else p['tel'],
              'Experience' if engels else 'Werkervaring']
    for b, e, f, w, t in (banen if banen is not None else p['banen']):
        if engels:
            regels.append(f"{b} - {'present' if e == 'heden' else e} {FUNCTIE_EN[f]} at {w}. Responsible for {t}.")
        else:
            regels.append(f"{b} - {e} {f} bij {w}. Verantwoordelijk voor {t}.")
    regels += ['Education' if engels else 'Opleiding', f"{p['sjaar']} - {p['sjaar'] + 4} HBO Informatica, {p['school']}"]
    return '\n'.join(regels)


def paren(n=300, seed=7):
    r = random.Random(seed)
    uit = []
    while len(uit) < n:
        p = persoon(r)
        soort = r.randrange(18)
        if soort == 0:   # oudere versie: nieuwste baan ontbreekt
            uit.append(('zelfde', 'oude versie', tekst(p), tekst(p, banen=p['banen'][1:])))
        elif soort == 1:  # nieuwere versie met extra baan
            nieuw = [[p['banen'][0][1] if p['banen'][0][1] != 'heden' else 2025, 'heden', r.choice(FUNCTIE), r.choice(BEDRIJF), r.choice(TAAK)]]
            oud = [list(x) for x in p['banen']]
            oud[0][1] = 2025
            uit.append(('zelfde', 'nieuwe versie', tekst(p, banen=oud), tekst(p, banen=nieuw + oud)))
        elif soort == 2:
            uit.append(('zelfde', 'engelse versie', tekst(p), tekst(p, engels=True)))
        elif soort == 3:
            uit.append(('zelfde', 'voorletter', tekst(p), tekst(p, naam=f"{p['voor'][0]}. {p['achter']}")))
        elif soort == 4:
            uit.append(('zelfde', 'kort cv', tekst(p), tekst(p, banen=p['banen'][:2])))
        elif soort == 5:  # nieuw mailadres, zelfde nummer
            uit.append(('zelfde', 'ander mailadres', tekst(p), tekst(p, mail=f"{p['voor'].lower()}{r.randint(100, 999)}@hotmail.com")))
        elif soort == 6:  # nieuw nummer, zelfde mail
            uit.append(('zelfde', 'ander nummer', tekst(p), tekst(p, tel=f"06 {r.randint(10000000, 99999999)}", banen=p['banen'][1:])))
        elif soort == 7:  # zelfde naam, totaal ander persoon
            q = persoon(r); q.update(voor=p['voor'], achter=p['achter'])
            uit.append(('anders', 'naamgenoot', tekst(p), tekst(q)))
        elif soort == 8:  # naamgenoot bij hetzelfde grote bedrijf
            q = persoon(r); q.update(voor=p['voor'], achter=p['achter'])
            g = r.choice(GROOT)
            p['banen'][0][3] = g; q['banen'][0][3] = g
            uit.append(('anders', 'naamgenoot groot bedrijf', tekst(p), tekst(q)))
        elif soort == 9:  # referentie: mail van p staat in cv van q (zelfde voornaam)
            q = persoon(r); q.update(voor=p['voor'])
            uit.append(('anders', 'referentie-mail', tekst(p), tekst(q, mail=p['mail'])))
        elif soort == 10:  # broer/zus: zelfde achternaam en vaste nummer
            q = persoon(r); q.update(achter=p['achter'])
            if q['voor'] == p['voor']:
                continue
            vast = f"0{r.randint(10, 99)} {r.randint(1000000, 9999999)}"
            uit.append(('anders', 'familie zelfde nummer', tekst(p, tel=vast), tekst(q, tel=vast)))
        elif soort == 11:  # bureau: zelfde recruitercontact, andere kandidaat
            q = persoon(r)
            rm, rt = 'jobs@bureauvoorbeeld.nl', '020 1234567'
            uit.append(('anders', 'bureau-contact', tekst(p, mail=rm, tel=rt), tekst(q, mail=rm, tel=rt)))
        elif soort == 12:  # naamgenoot, zelfde specifiek bedrijf maar andere jaren en andere opleiding
            q = persoon(r); q.update(voor=p['voor'], achter=p['achter'])
            q['banen'][-1][3] = p['banen'][-1][3]
            q['banen'][-1][0] = p['banen'][-1][0] + 4
            uit.append(('anders', 'naamgenoot zelfde bedrijf andere jaren', tekst(p), tekst(q)))
        elif soort == 13:  # zelfde naam en mail (gezamenlijk/oud adres) maar compleet andere loopbaan
            q = persoon(r); q.update(voor=p['voor'], achter=p['achter'])
            uit.append(('anders', 'zelfde naam+mail, andere loopbaan', tekst(p), tekst(q, mail=p['mail'])))
        elif soort == 14:  # Engelse versie + voorletter (moeilijk)
            uit.append(('zelfde', 'engels + voorletter', tekst(p), tekst(p, engels=True, naam=f"{p['voor'][0]}. {p['achter']}")))
        elif soort == 16:  # verhuisd/nieuw: ander mailadres én ander nummer, nieuwste baan erbij
            nieuw = [[2025, 'heden', r.choice(FUNCTIE), r.choice(BEDRIJF), r.choice(TAAK)]]
            oud = [list(x) for x in p['banen']]
            oud[0][1] = 2025
            uit.append(('zelfde', 'nieuw mail+nummer', tekst(p, banen=oud),
                        tekst(p, banen=nieuw + oud, mail=f"{p['voor'].lower()}{r.randint(100, 999)}@outlook.com",
                              tel=f"06 {r.randint(10000000, 99999999)}")))
        elif soort == 17:  # rommelig: OCR-achtig, kolommen door elkaar, maar zelfde persoon
            t = tekst(p).replace('Verantwoordelijk voor', 'Verantw. v.').replace(' - ', '–')
            uit.append(('zelfde', 'rommelige tekst', tekst(p), t.replace('\n', '  ')))
        else:   # naamgenoot met dezelfde school
            q = persoon(r); q.update(voor=p['voor'], achter=p['achter'], school=p['school'])
            uit.append(('anders', 'naamgenoot zelfde school', tekst(p), tekst(q)))
    return uit
