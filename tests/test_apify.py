"""Apify-koppeling met nagebootste API (geen echt token, verzonnen profielen)."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['APIFY_TOKEN'] = 'nep'
import apify_linkedin as ap  # noqa: E402
fout = []
def check(n, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + n + ('' if ok else f'  -> {info}'))
    if not ok: fout.append(n)
PROF = {'jan-bakker-1': {'publicIdentifier': 'jan-bakker-1', 'linkedinUrl': 'https://www.linkedin.com/in/jan-bakker-1',
        'firstName': 'Jan', 'lastName': 'Bakker', 'headline': 'Java developer', 'about': 'Backend.',
        'location': {'linkedinText': 'Utrecht, Netherlands'}, 'currentPosition': [{'companyName': 'Datafabriek Noord'}],
        'experience': [{'position': 'Java developer', 'companyName': 'Datafabriek Noord', 'startDate': {'text': 'Mar 2016'}, 'endDate': {'text': 'Present'}},
                       {'position': 'Tester', 'companyName': 'Codewerk', 'startDate': {'text': '2012'}, 'endDate': {'text': '2016'}}],
        'education': [{'schoolName': 'Hogeschool Utrecht', 'degree': 'Bachelor', 'period': '2008 - 2012'}],
        'certifications': [{'title': 'OCP Java 17'}], 'skills': [{'name': 'Java'}],
        'moreProfiles': [{'firstName': 'Ander', 'lastName': 'Persoon'}], 'status': 200}}
runs, peil = [], {'n': 0}
def nep(methode, pad, body=None, timeout=60):
    if pad.endswith('/runs'):
        runs.append(body['urls']); return json.dumps({'data': {'id': f'r{len(runs)}', 'defaultDatasetId': f'd{len(runs)}'}})
    if pad.startswith('/actor-runs/'):
        peil['n'] += 1; rid = pad.split('/')[-1]
        return json.dumps({'data': {'status': 'RUNNING' if peil['n'] % 2 else 'SUCCEEDED', 'defaultDatasetId': 'd' + rid[1:]}})
    if pad.startswith('/datasets/'):
        n = int(pad.split('/')[2][1:])
        return json.dumps([PROF.get(ap.slug(u), {'query': {'publicIdentifier': ap.slug(u)}, 'status': 404}) for u in runs[n - 1]])
ap._verzoek = nep
cache = {}
n = ap.ophalen(['https://nl.linkedin.com/in/jan-bakker-1', 'https://www.linkedin.com/in/Jan-Bakker-1/', 'https://www.linkedin.com/in/weg'],
               cache, log=lambda m: None, slaap=lambda s: None)
check('1 run met 2 unieke URL\'s', runs == [['https://www.linkedin.com/in/jan-bakker-1/', 'https://www.linkedin.com/in/weg/']], runs)
check('1 profiel opgehaald, niet-bestaand als fout', n == 1 and cache['ap|weg'].get('fout'), cache.get('ap|weg'))
check('andere mensen (moreProfiles) niet bewaard', 'moreProfiles' not in cache['ap|jan-bakker-1'])
g = ap.gegevens(cache['ap|jan-bakker-1'])
check('werkervaring met jaren', g['werkervaring'] == ['Java developer · Datafabriek Noord · Mar 2016 - Present', 'Tester · Codewerk · 2012 - 2016'], g['werkervaring'])
check('opleiding met jaren', g['opleidingen'] == ['Hogeschool Utrecht · Bachelor · 2008 - 2012'], g['opleidingen'])
check('huidige werkgever + functie', g['werkgever'] == 'Datafabriek Noord' and g['functie'] == 'Java developer', g)
t = ap.tekst(cache['ap|jan-bakker-1'])
check('profieltekst "at bedrijf" voor het bewijs', 'Tester at Codewerk' in t and 'Persoon' not in t, t)
runs.clear(); ap.ophalen(['https://www.linkedin.com/in/jan-bakker-1'], cache, log=lambda m: None, slaap=lambda s: None)
check('cache: niet opnieuw betalen', runs == [])
import profielbron
check('profielbron: Apify eerst', profielbron.BRONNEN[0] is ap and profielbron.sleutel())

# --- In de online stap: Apify eerst; e-mail/telefoon uit 'about', daarna Google X-ray (Serper) voor wat ontbreekt ---
import csv, shutil, tempfile
import cv_crawler as c
os.environ['SERPER_API_KEY'] = 'nep'; os.environ.pop('TAVILY_API_KEY', None)
PROF['jan-bakker-1']['about'] = 'Backend developer. Bel gerust: 06-12345678.'
PROF['jan-bakker-1']['headline'] = 'Java developer | jan.bakker90 [at] gmail [dot] com'
runs.clear(); peil['n'] = 0
google = []
def nep_google(q):
    google.append(q)
    if 'site:linkedin.com/in' in q and '"Jan Bakker"' in q and 'mail' in q:
        return ([{'url': 'https://nl.linkedin.com/in/jan-bakker-1', 'title': 'Jan Bakker - Java developer - LinkedIn',
                  'content': 'Info: mail mij op jan.bakker@voorbeeldmail.nl'},
                 {'url': 'https://nl.linkedin.com/in/jan-bakker-99', 'title': 'Jan Bakker - Kok - LinkedIn',
                  'content': 'jan.bakker@anderepersoon.nl'}], 'Serper')
    if 'Jan Bakker' in q:
        return ([{'url': 'https://nl.linkedin.com/in/jan-bakker-1', 'title': 'Jan Bakker - Java developer - LinkedIn', 'content': 'Utrecht'}], 'Serper')
    return ([], 'Serper')
c.google_search = nep_google
c.tavily_search = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Tavily mag niet'))
out = Path(tempfile.mkdtemp()); db = c.db_open(out)
tekst_cv = 'Jan Bakker\nWerkervaring\n2016 - heden Java developer bij Datafabriek Noord, Utrecht\nOpleiding\n2008 - 2012 Hogeschool Utrecht'
row = dict(path=str(out / 'cv.docx'), name='Jan Bakker', status='cv', snippet=c.normalized_snippet(tekst_cv),
           employer='Datafabriek Noord', woonplaats='utrecht', opleidingen='Hogeschool Utrecht', stage='', sha='jb',
           classifier_version=c.CLASSIFIER_VERSION)
db.execute(f"insert into docs({','.join(row)}) values({','.join('?' * len(row))})", list(row.values())); db.commit()
mensen = [dict(naam='Jan Bakker', persoon_id='p1', kandidaat_id='SNK-1', email='', telefoon='', linkedin='',
               huidige_functie='Java developer', laatst_bekende_werkgever='Datafabriek Noord', woonplaats='utrecht')]
c.online_suggestions(mensen, out, db=db, delay=0)
rows = list(csv.DictReader(open(out / 'online_suggesties.csv', encoding='utf-8-sig'), delimiter=';'))
best = next((r for r in rows if r['type'] == 'linkedin' and r['keuze'] == 'beste'), {})
check('LinkedIn via Apify-profiel herkend', 'jan-bakker-1' in best.get('gevonden_waarde', ''), best)
tel = [r['gevonden_waarde'] for r in rows if r['type'] == 'telefoon']
mail = [r['gevonden_waarde'] for r in rows if r['type'] == 'email']
check('telefoon uit about van het eigen profiel', any('612345678' in t.replace(' ', '') for t in tel), rows)
check('geen telefoon-X-ray nodig (al gevonden)', not any('+31 6' in q for q in google), google)
check('verstopte e-mail uit de kopregel van het eigen profiel', mail == ['jan.bakker90@gmail.com'], mail)
check('geen mail-X-ray nodig (al gevonden)', not any('"@"' in q for q in google), google)
shutil.rmtree(out, ignore_errors=True)
print('APIFY OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
