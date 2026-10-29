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
print('APIFY OK' if not fout else f'{len(fout)} FOUT(EN)'); sys.exit(1 if fout else 0)
