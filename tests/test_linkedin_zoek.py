"""LinkedIn zoeken: volgorde Serper, SerpApi, Tavily, Linkup.
Geen echt internet. Bestaande scannerfuncties blijven hier buiten de zoekketen.
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI'] = '0'
os.environ['LINKEDIN_RETRY_WACHT'] = '0'
os.environ.pop('APIFY_TOKEN', None)
os.environ.pop('LINKUP_API_KEY', None)
os.environ.pop('GOMA_API_KEY', None)

import cv_crawler as c  # noqa: E402
import linkedin_zoek as z  # noqa: E402

FOUT = []


def check(naam, ok, info=''):
    print(('OK   ' if ok else 'FOUT ') + naam + ('' if ok else f'  -> {info}'))
    if not ok:
        FOUT.append(naam)


def http(code, retry_after=None):
    headers = EmailMessage()
    if retry_after is not None:
        headers['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError('https://voorbeeld.test/zoek', code, 'fout', headers, None)


def profiel(slug, title='Jan de Vries | LinkedIn', content='ASML'):
    return {'url': f'https://www.linkedin.com/in/{slug}', 'title': title, 'content': content}


def bronnen(aanroep, timeout=None):
    return z.Bronnen(aanroep, timeout=timeout or {}, wacht=0, slaap=lambda s: aangeroepen.setdefault('slaap', []).append(s))


aangeroepen = {}


def vers():
    aangeroepen.clear()


# --- persoonlijke link, geen zoekopdracht ---
check('bedrijfslink is geen persoonlijk profiel', z.persoonlijke_url('https://www.linkedin.com/company/asml') == '')
check('recruiter-slug is geen persoonlijk profiel', z.persoonlijke_url('https://www.linkedin.com/in/recruiter') == '')
check('referentielink is geen persoonlijk profiel', z.persoonlijke_url('https://www.linkedin.com/in/referentie') == '')
check('persoonlijke link blijft behouden',
      z.persoonlijke_url('https://nl.linkedin.com/in/Jan-de-Vries/') == 'https://www.linkedin.com/in/jan-de-vries')

vers()
os.environ['SERPER_API_KEY'] = 'test'
os.environ['TAVILY_API_KEY'] = 'test'


def _verboden(*a, **k):
    raise AssertionError('er is gezocht terwijl de link al in het cv staat')


_serper, _serpapi, _tavily, _google = c.serper_search, c.serpapi_search, c.tavily_search, c.google_search
c.serper_search = _verboden
c.serpapi_search = _verboden
c.tavily_search = _verboden
c.google_search = lambda q: ([], 'Serper')
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
try:
    c.online_suggestions([dict(naam='Jan de Vries', persoon_id='p-cv', email='', telefoon='',
                               linkedin='https://www.linkedin.com/in/jan-de-vries',
                               huidige_functie='Software Engineer', laatst_bekende_werkgever='ASML', woonplaats='')],
                         _out, db=_db, delay=0)
    check('LinkedIn staat al in het cv: geen zoekopdracht', True)
except AssertionError as exc:
    check('LinkedIn staat al in het cv: geen zoekopdracht', False, exc)
finally:
    c.serper_search, c.serpapi_search, c.tavily_search, c.google_search = _serper, _serpapi, _tavily, _google
    shutil.rmtree(_out, ignore_errors=True)

# --- Serper leeg -> SerpApi ---
vers()
volgorde = []


def leeg_serper(q):
    volgorde.append('Serper')
    return []


def serp_raak(q):
    volgorde.append('SerpApi')
    return [profiel('jan-de-vries')]


keten = bronnen({'Serper': leeg_serper, 'SerpApi': serp_raak, 'Tavily': lambda q: volgorde.append('Tavily'),
                 'Google': lambda q: volgorde.append('Google')})
rijen, bron, toestand = keten.zoek('site:linkedin.com/in/ "Jan de Vries"')
check('Serper leeg gaat door naar SerpApi', volgorde == ['Serper', 'SerpApi'] and bron == 'SerpApi'
      and toestand == 'gevonden' and rijen[0]['url'].endswith('/jan-de-vries'), (volgorde, bron, rijen))

# --- Serper timeout -> SerpApi ---
vers()
volgorde = []


def serper_traag(q):
    volgorde.append('Serper')
    raise TimeoutError('timeout')


keten = bronnen({'Serper': serper_traag, 'SerpApi': serp_raak, 'Tavily': lambda q: (_ for _ in ()).throw(AssertionError('Tavily')),
                 'Google': lambda q: None})
rijen, bron, toestand = keten.zoek('q')
check('Serper timeout gaat door naar SerpApi', volgorde == ['Serper', 'SerpApi'] and bron == 'SerpApi' and toestand == 'gevonden',
      (volgorde, bron))

# --- twee timeouts: pauze, de derde keer niet opnieuw Serper ---
vers()
tellen = {'Serper': 0}


def serper_altijd_traag(q):
    tellen['Serper'] += 1
    raise TimeoutError('timeout')


def serp_leeg(q):
    return []


keten = bronnen({'Serper': serper_altijd_traag, 'SerpApi': serp_leeg, 'Tavily': lambda q: [], 'Google': lambda q: []})
keten.zoek('q')
keten.zoek('q')
voor = tellen['Serper']
keten.zoek('q')
check('twee timeouts zetten Serper op pauze', voor == 2 and tellen['Serper'] == 2, tellen)

# --- SerpApi-tegoed op -> Tavily, sleutel blijft ---
vers()
os.environ['SERPAPI_API_KEY'] = 'serp-bewaard'
volgorde = []


def serp_op(q):
    volgorde.append('SerpApi')
    raise http(402)


def tavily_raak(q):
    volgorde.append('Tavily')
    return [profiel('jan-de-vries', content='TU Eindhoven')]


keten = bronnen({'Serper': leeg_serper, 'SerpApi': serp_op, 'Tavily': tavily_raak, 'Google': lambda q: []})
rijen, bron, toestand = keten.zoek('q')
check('SerpApi tegoed op gaat door naar Tavily', 'SerpApi' in volgorde and bron == 'Tavily' and toestand == 'gevonden',
      (volgorde, bron))
check('sleutel blijft bewaard als het tegoed op is', os.environ.get('SERPAPI_API_KEY') == 'serp-bewaard')
keten.zoek('nog-een')
check('SerpApi wordt niet bij iedere volgende kandidaat opnieuw geprobeerd', volgorde.count('SerpApi') == 1, volgorde)

# --- alle diensten op: de keten stopt netjes ---
vers()


def op(q):
    raise http(402)


keten = bronnen({'Serper': op, 'SerpApi': op, 'Tavily': op,
                 'Google': lambda q: (_ for _ in ()).throw(z.NietBeschikbaar('geen google'))})
rijen, bron, toestand = keten.zoek('q')
check('alle zoekdiensten op: geen crash, geen resultaat', rijen is None and toestand == 'geen bron', (rijen, toestand))
check('daarna is er geen bron meer', keten.geen_bronnen_meer())

# --- rate limit en tijdelijke storing ---
vers()
slaap = []
poging = {'n': 0}


def eerst_storing(q):
    poging['n'] += 1
    if poging['n'] == 1:
        raise http(503)
    return [profiel('jan-de-vries')]


keten = z.Bronnen({'Serper': eerst_storing, 'SerpApi': lambda q: [], 'Tavily': lambda q: [], 'Google': lambda q: []},
                  wacht=0, slaap=lambda s: slaap.append(s))
rijen, bron, toestand = keten.zoek('q')
check('tijdelijke storing: één keer opnieuw, daarna het antwoord', poging['n'] == 2 and bron == 'Serper' and toestand == 'gevonden',
      (poging, bron))

vers()
slaap = []


def te_snel(q):
    raise http(429, retry_after=30)


gezien = []
keten = z.Bronnen({'Serper': te_snel, 'SerpApi': lambda q: gezien.append('SerpApi') or [profiel('jan-de-vries')],
                   'Tavily': lambda q: [], 'Google': lambda q: []},
                  wacht=0, slaap=lambda s: slaap.append(s))
rijen, bron, toestand = keten.zoek('q')
check('rate limit met lange wachttijd: meteen de volgende dienst', bron == 'SerpApi' and 30 not in slaap and toestand == 'gevonden',
      (bron, slaap))

# --- snelle reactie wacht de rest van de timeout niet af ---
start = time.time()
keten = z.Bronnen({'Serper': lambda q: [profiel('jan-de-vries')], 'SerpApi': lambda q: [], 'Tavily': lambda q: [],
                   'Google': lambda q: []},
                  timeout={'Serper': 10}, wacht=0, slaap=lambda s: None)
keten.zoek('q')
check('antwoord binnen de timeout gaat meteen verder', time.time() - start < 1)

start = time.time()


def slaapt(q):
    time.sleep(1)
    return []


keten = z.Bronnen({'Serper': slaapt, 'SerpApi': lambda q: [profiel('jan-de-vries')], 'Tavily': lambda q: [],
                   'Google': lambda q: []},
                  timeout={'Serper': 0.2, 'SerpApi': 2}, wacht=0, slaap=lambda s: None)
rijen, bron, toestand = keten.zoek('q')
check('maximale aanvraagduur kapt een trage dienst af', time.time() - start < 0.8 and bron == 'SerpApi',
      (time.time() - start, bron))

# --- meerdere zoekopdrachten met AND/OR ---
qs = z.maak_zoekopdrachten('Jan de Vries', ['ASML', 'Philips'], ['Software Engineer'], ['TU Eindhoven'], ['Eindhoven'])
check('hooguit 4 zoekopdrachten', 1 <= len(qs) <= 4, qs)
check('sterkste opdracht: naam en werkgevers met OR',
      qs[0] == 'site:linkedin.com/in/ ("Jan de Vries" OR "J. de Vries" OR "J de Vries") ("ASML" OR "Philips")', qs[0])
check('functie staat apart, met de naam', '"Software Engineer"' in qs[1] and 'ASML' not in qs[1], qs[1])
check('opleiding staat apart', '"TU Eindhoven"' in qs[2] and 'ASML' not in qs[2], qs[2])
check('niet alle cv-gegevens in één opdracht', 'TU Eindhoven' not in qs[0] and 'Software Engineer' not in qs[0], qs[0])
check('bredere opdracht blijft staan', 'Eindhoven' in qs[-1], qs)
klein = z.maak_zoekopdrachten('Jan de Vries', ['ASML', 'Philips'], ['Software Engineer'], ['TU Eindhoven'], ['Eindhoven'], maximum=2)
check('beperkt budget duwt de bredere opdracht niet weg', 'ASML' in klein[0] and 'Eindhoven' in klein[-1] and len(klein) == 2, klein)
meer = z.maak_zoekopdrachten('Jan de Vries', ['ASML', 'Philips'], ['Software Engineer', 'Monteur'], ['TU Eindhoven', 'Fontys'], ['Eindhoven', 'Veldhoven'])
check('meerdere functies en opleidingen in hun eigen opdracht',
      '"Software Engineer" OR "Monteur"' in meer[1] and '"TU Eindhoven" OR "Fontys"' in meer[2], meer)

# --- meerdere naamgenoten, dubbele adressen weg ---
rijen = z.profielrijen([
    profiel('jan-de-vries-1', 'Jan de Vries - ASML'),
    profiel('jan-de-vries-2', 'Jan de Vries - Philips'),
    {'url': 'https://www.linkedin.com/in/jan-de-vries-1?trk=x', 'title': 'dubbel', 'content': ''},
    {'url': 'https://www.linkedin.com/company/asml', 'title': 'ASML', 'content': ''},
])
check('twee naamgenoten blijven, dubbel adres en bedrijf vallen weg',
      [r['url'] for r in rijen] == ['https://www.linkedin.com/in/jan-de-vries-1',
                                    'https://www.linkedin.com/in/jan-de-vries-2'], rijen)

# --- cache voorkomt een nieuwe aanvraag ---
vers()
tellen = {'n': 0}


def tel(q):
    tellen['n'] += 1
    return [profiel('jan-de-vries')]


cache = {}
keten = bronnen({'Serper': tel, 'SerpApi': lambda q: [], 'Tavily': lambda q: [], 'Google': lambda q: []})
een, stand = z.zoek_profielen(qs[:1], cache, keten, 'jan de vries')
twee, stand2 = z.zoek_profielen(qs[:1], cache, keten, 'jan de vries')
check('cache voorkomt een tweede aanvraag', tellen['n'] == 1 and stand == 'af' and stand2 == 'af'
      and een[0]['url'] == twee[0]['url'], tellen)

check('geen rechtstreekse Google-zoekfunctie',
      not hasattr(c, 'google_xray_direct') and not hasattr(z, 'google_xray_mogelijk')
      and not hasattr(z, 'GOOGLE_XRAY_REDEN'))
check('zoekvolgorde eindigt bij Linkup', z.Bronnen.VOLGORDE == ('Serper', 'SerpApi', 'Tavily', 'Linkup'))

keten = bronnen({'Serper': lambda q: [], 'SerpApi': lambda q: [], 'Tavily': lambda q: [],
                 'Linkup': lambda q: [], 'Google': lambda q: [profiel('jan-de-vries', content='ASML')]})
rijen, bron, toestand = keten.zoek('site:linkedin.com/in/ "Jan de Vries"')
check('Google wordt niet aangeroepen als de andere diensten leeg zijn',
      bron == '' and toestand == 'leeg' and rijen == [], (bron, toestand, rijen))

# --- geen internet: scanner en Excel gaan door, kandidaat blijft open ---
vers()


def weg(*a, **k):
    raise urllib.error.URLError('geen internet')


c.serper_search = weg
c.serpapi_search = weg
c.tavily_search = weg
os.environ['SERPER_API_KEY'] = 'test'
os.environ['TAVILY_API_KEY'] = 'test'
_out = Path(tempfile.mkdtemp())
_db = c.db_open(_out)
try:
    klaar = c.online_suggestions([dict(naam='Jan de Vries', persoon_id='p-open', email='jan@voorbeeld.nl',
                                       telefoon='+31612345678', linkedin='',
                                       huidige_functie='Software Engineer', laatst_bekende_werkgever='ASML',
                                       woonplaats='Eindhoven')],
                                 _out, db=_db, delay=0)
    excel, _cols = c.personen_overzicht([dict(naam='Jan de Vries', persoon_id='p-open', email='jan@voorbeeld.nl',
                                              telefoon='+31612345678', linkedin='', kandidaat_id='SNK', cv_id='SNCV',
                                              cv_bestand='', cv_pad='', cv_archief='')])
    open_ids = z.lees_open(_out / z.OPEN_BESTAND)
    check('geen internet: online stap blijft heel', klaar is True)
    check('geen internet: Excel wordt nog opgebouwd', excel and excel[0].get('naam') == 'Jan de Vries', excel[:1])
    check('geen internet: kandidaat blijft bewaard voor de volgende ronde', 'p-open' in open_ids, open_ids)
finally:
    c.serper_search, c.serpapi_search, c.tavily_search, c.google_search = _serper, _serpapi, _tavily, _google
    shutil.rmtree(_out, ignore_errors=True)

# configureerbare timeout
os.environ['LINKEDIN_TIMEOUT_SERPER'] = '3.5'
check('timeout is configureerbaar', z.Bronnen({}).timeout_voor('Serper') == 3.5)
os.environ.pop('LINKEDIN_TIMEOUT_SERPER', None)
check('standaardtimeout is 10 seconden', z.Bronnen({}).timeout_voor('Tavily') == 10)
check('Linkup wacht standaard hooguit 10 seconden', z.Bronnen({}).timeout_voor('Linkup') == 10)

# --- Linkup pas na Serper, SerpApi en Tavily ---
vers()
volgorde = []


def leeg_bron(naam):
    def fn(q):
        volgorde.append(naam)
        return []
    return fn


def linkup_raak(q):
    volgorde.append('Linkup')
    return [profiel('jan-de-vries', content='ASML')]


keten = bronnen({'Serper': leeg_bron('Serper'), 'SerpApi': leeg_bron('SerpApi'), 'Tavily': leeg_bron('Tavily'),
                 'Linkup': linkup_raak, 'Google': lambda q: (_ for _ in ()).throw(AssertionError('Google te vroeg'))})
rijen, bron, toestand = keten.zoek('site:linkedin.com/in/ "Jan de Vries"')
check('Linkup komt na Serper, SerpApi en Tavily',
      volgorde == ['Serper', 'SerpApi', 'Tavily', 'Linkup'] and bron == 'Linkup' and toestand == 'gevonden', volgorde)

vers()
os.environ['LINKUP_API_KEY'] = 'test-linkup'
linkup_pogingen = {'n': 0}


def linkup_op(q):
    linkup_pogingen['n'] += 1
    raise http(402)


keten = bronnen({'Serper': leeg_bron('Serper'), 'SerpApi': leeg_bron('SerpApi'), 'Tavily': leeg_bron('Tavily'),
                 'Linkup': linkup_op, 'Google': lambda q: (_ for _ in ()).throw(AssertionError('Google werd aangeroepen'))})
rijen, bron, toestand = keten.zoek('q')
check('Linkup zonder tegoed stopt die dienst en roept Google niet aan',
      bron == '' and toestand == 'leeg' and keten.op == {'Linkup'}, (bron, toestand, keten.op))
check('Linkup-sleutel blijft bewaard', os.environ.get('LINKUP_API_KEY') == 'test-linkup')
keten.zoek('nog-een')
check('Linkup wordt niet bij iedere volgende kandidaat opnieuw geprobeerd', linkup_pogingen['n'] == 1, linkup_pogingen)
os.environ.pop('LINKUP_API_KEY', None)

gezien = {}


class LinkupAntwoord:
    def read(self, *a):
        return (b'{"results":[{"type":"text","name":"Jan de Vries","url":"https://www.linkedin.com/in/jan-de-vries",'
                b'"content":"ASML"}]}')

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def nep_linkup(req, timeout=10):
    gezien['url'] = req.full_url
    gezien['body'] = req.data.decode('utf-8')
    gezien['auth'] = req.headers.get('Authorization', '')
    return LinkupAntwoord()


_open = c.urllib.request.urlopen
c.urllib.request.urlopen = nep_linkup
try:
    gevonden = c.linkup_search('site:linkedin.com/in/ "Jan de Vries"', 'test-linkup')
finally:
    c.urllib.request.urlopen = _open
check('Linkup-aanvraag gaat naar de officiële zoek-API',
      gezien.get('url') == 'https://api.linkup.so/v1/search'
      and '"outputType": "searchResults"' in gezien.get('body', '')
      and '"depth": "fast"' in gezien.get('body', '')
      and 'site:linkedin.com' not in gezien.get('body', '')
      and 'LinkedIn profile' in gezien.get('body', '')
      and 'linkedin.com' in gezien.get('body', '')
      and 'Datafabriek' not in gezien.get('body', '')
      and gezien.get('auth') == 'Bearer test-linkup'
      and gevonden and gevonden[0]['url'].endswith('/jan-de-vries'), gezien.get('url'))
check('zoekbudget is begrensd', z.zoekbudget() == 4)
os.environ['LINKEDIN_MAX_ZOEKOPDRACHTEN'] = '2'
check('zoekbudget is instelbaar', z.zoekbudget() == 2 and len(z.maak_zoekopdrachten(
    'Jan de Vries', ['ASML', 'Philips'], ['Software Engineer'], ['TU Eindhoven'], ['Eindhoven'], z.zoekbudget())) <= 2)
os.environ.pop('LINKEDIN_MAX_ZOEKOPDRACHTEN', None)

print()
print('LINKEDIN ZOEKEN OK' if not FOUT else f'{len(FOUT)} FOUT(EN): ' + ', '.join(FOUT))
sys.exit(1 if FOUT else 0)
