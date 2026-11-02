"""Goma leest een LinkedIn-adres dat de scanner al gevonden heeft.

Goma is geen zoekmachine. Er gaat alleen een profieladres heen, nooit het cv.
Python vergelijkt daarna werk en opleiding en beslist zelf of het profiel klopt.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import linkedin_zoek

API = 'https://trygoma.com/api/v1/person/profile'
PAUZE_SECONDEN = 300
MAX_TEGELIJK = 5

_RECHTSVORM = re.compile(
    r'\b(?:b\.?v\.?|n\.?v\.?|vof|inc|ltd|gmbh|holding|group|groep)\b', re.I)
_JAAR = re.compile(r'(?:19|20)\d{2}')
_GROOT = re.compile(
    r'(?i)^(?:gemeente\b.*|provincie\b.*|rijksoverheid|ministerie\b.*|belastingdienst|defensie|'
    r'politie|uwv|ns|postnl|albert heijn|jumbo|lidl|coolblue|bol(?:\.com)?|ing|rabobank|abn amro|'
    r'kpn|vodafone|ziggo|philips|shell|unilever|heineken|asml|schiphol|klm)$')
_VAAG = re.compile(
    r'(?i)^(?:eigen bedrijf|zzp|freelance|freelancer|zelfstandig(?:e)?|diverse|uitzendbureau|onbekend)$')
_FUNCTIE_VAAG = {
    'software', 'engineer', 'developer', 'manager', 'consultant', 'medewerker', 'specialist',
    'senior', 'junior', 'lead', 'head', 'officer', 'assistant', 'assistent', 'intern', 'stagiair',
}
_TUSSEN = {'de', 'van', 'der', 'den', 'het', 'ten', 'ter', 'te', 'op', 'aan', 'in', 'the', 'of', 'und', 'von'}

_klant = None
aanvragen_deze_run = 0
_aanvraag_lock = threading.Lock()


def verzoek_url(linkedin_url):
    """Alleen het profieladres. Geen cv-tekst."""
    return API + '?' + urllib.parse.urlencode({'url': linkedin_url})


def sleutel():
    return os.environ.get('GOMA_API_KEY', '').strip()


def timeout_seconden():
    try:
        return float(os.environ.get('GOMA_TIMEOUT', '15') or 15)
    except (TypeError, ValueError):
        return 15.0


def max_per_kandidaat():
    try:
        return max(0, int(os.environ.get('GOMA_MAX_PER_KANDIDAAT', '3') or 3))
    except (TypeError, ValueError):
        return 3


def reset():
    """Nieuwe toestand. Tests beginnen hiermee, zodat een eerdere ronde niet blijft hangen."""
    global _klant
    _klant = Klant()
    return _klant


def huidige():
    global _klant
    if _klant is None:
        _klant = Klant()
    return _klant


def _tokens(tekst):
    schoon = _RECHTSVORM.sub(' ', str(tekst or '').lower().replace('&', ' '))
    return re.findall(r'[a-z0-9]{2,}', schoon)


def zelfde_org(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    tekst_a, tekst_b = ' '.join(ta), ' '.join(tb)
    if tekst_a in tekst_b or tekst_b in tekst_a:
        return True
    kort, lang = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    kern = [w for w in kort if len(w) >= 4]
    return bool(kern) and all(w in lang for w in kern) and any(len(w) >= 5 for w in kern)


def zelfde_functie(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    kort, lang = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(' '.join(kort)) < 10 or ' '.join(kort) not in ' '.join(lang):
        return False
    return any(w not in _FUNCTIE_VAAG and len(w) >= 4 for w in kort)


def _groot(naam):
    tekst = ' '.join(_tokens(naam))
    return bool(_GROOT.match(str(naam or '').strip()) or _GROOT.match(tekst) or _VAAG.match(tekst))


def _jaar(waarde):
    if isinstance(waarde, dict):
        waarde = waarde.get('year') or waarde.get('text') or ''
    gevonden = _JAAR.search(str(waarde or ''))
    return int(gevonden.group(0)) if gevonden else None


def _jaren(start, eind, periode=''):
    begin, slot = _jaar(start), _jaar(eind)
    if begin is None and periode:
        jaren = [int(x) for x in _JAAR.findall(str(periode))]
        if jaren:
            begin = jaren[0]
            slot = jaren[-1] if len(jaren) > 1 else slot
    return begin, slot


def _kernnaam(naam):
    return [w for w in re.findall(r'[a-zà-ÿ]+', str(naam or '').lower()) if w not in _TUSSEN]


def naam_klopt(cv_naam, profiel_naam):
    """None als het profiel geen naam heeft. False bij een andere persoon."""
    b = _kernnaam(profiel_naam)
    if not b:
        return None
    a = _kernnaam(cv_naam)
    if len(a) < 2 or len(b) < 2:
        return False
    if a[-1] != b[-1] and a[-1][:5] != b[-1][:5]:
        return False
    return a[0] == b[0] or a[0][:1] == b[0][:1]


def normaliseer(data, bron='goma'):
    data = data or {}
    if isinstance(data, dict) and isinstance(data.get('data'), dict):
        data = data['data']
    banen = []
    blokken = list(data.get('experience') or []) + list(data.get('current_position') or data.get('currentPosition') or [])
    for blok in blokken:
        if not isinstance(blok, dict):
            continue
        begin, slot = _jaren(blok.get('start_date') or blok.get('startDate'),
                             blok.get('end_date') or blok.get('endDate'),
                             blok.get('period') or '')
        bedrijf = blok.get('company_name') or blok.get('companyName') or ''
        functie = blok.get('title') or blok.get('position') or ''
        if bedrijf or functie:
            banen.append({'bedrijf': bedrijf, 'functie': functie, 'start': begin, 'eind': slot})
    opleidingen = []
    for blok in data.get('education') or []:
        if not isinstance(blok, dict):
            continue
        begin, slot = _jaren(blok.get('start_date') or blok.get('startDate'),
                             blok.get('end_date') or blok.get('endDate'),
                             blok.get('period') or '')
        school = blok.get('school') or blok.get('schoolName') or ''
        richting = blok.get('field_of_study') or blok.get('fieldOfStudy') or blok.get('degree') or ''
        if school or richting:
            opleidingen.append({'school': school, 'richting': richting, 'start': begin, 'eind': slot})
    loc = data.get('location') or ''
    if isinstance(loc, dict):
        loc = loc.get('linkedinText') or loc.get('default') or loc.get('city') or ''
    voor = data.get('first_name') or data.get('firstName') or ''
    achter = data.get('last_name') or data.get('lastName') or ''
    return {
        'naam': (data.get('naam') or f'{voor} {achter}').strip(),
        'banen': banen,
        'opleidingen': opleidingen,
        'locatie': str(loc or ''),
        'bron': bron,
    }


def uit_apify(p):
    if not isinstance(p, dict) or p.get('fout'):
        return None
    profiel = normaliseer(p, bron='apify')
    if profiel['banen'] or profiel['opleidingen']:
        return profiel
    return None


def _jaren_dicht(cv, org, begin):
    if not begin:
        return False
    for periode in cv.get('perioden') or []:
        if not zelfde_org(periode.get('org') or '', org):
            continue
        start = periode.get('start')
        if start and abs(int(start) - int(begin)) <= 2:
            return True
    return False


def vergelijk(cv_naam, cv, profiel):
    """Eén profiel tegen het cv. Een extra baan of opleiding is geen tegenbewijs.

    Dezelfde naam alleen is nooit genoeg. Een ontbrekend veld is geen tegenstrijdigheid.
    """
    cv = cv or {}
    profiel = profiel or {}
    naam = naam_klopt(cv_naam, profiel.get('naam'))
    if naam is False:
        return {'oordeel': 'afwijzen', 'sterk_n': 0, 'middel_n': 0, 'hits': [], 'bewijs': []}
    if not profiel.get('banen') and not profiel.get('opleidingen'):
        return {'oordeel': 'geen gegevens', 'sterk_n': 0, 'middel_n': 0, 'hits': [], 'bewijs': []}
    sterk, middel = [], []
    gezien = set()

    def voeg(lijst, label, shown, gewicht):
        sleutel = (label, ' '.join(_tokens(shown)))
        if not shown or sleutel in gezien:
            return
        gezien.add(sleutel)
        lijst.append((label, shown, gewicht))

    for baan in profiel.get('banen') or []:
        for org in cv.get('werkgevers') or []:
            if not zelfde_org(org, baan.get('bedrijf')):
                continue
            if _groot(org) or _groot(baan.get('bedrijf')):
                voeg(middel, 'werkgever', baan.get('bedrijf') or org, 'middel')
            else:
                voeg(sterk, 'werkgever', baan.get('bedrijf') or org, 'sterk')
            if _jaren_dicht(cv, baan.get('bedrijf') or org, baan.get('start')):
                voeg(middel, 'jaren', f"{baan.get('bedrijf')} {baan.get('start')}", 'middel')
            break
        for functie in cv.get('functies') or []:
            if zelfde_functie(functie, baan.get('functie')):
                voeg(middel, 'functie', baan.get('functie') or functie, 'middel')
    for opleiding in profiel.get('opleidingen') or []:
        for school in cv.get('opleidingen') or []:
            if zelfde_org(school, opleiding.get('school')) or zelfde_org(school, opleiding.get('richting')):
                voeg(sterk, 'school', opleiding.get('school') or school, 'sterk')
                if opleiding.get('start') and _jaren_dicht(cv, opleiding.get('school') or school, opleiding.get('start')):
                    voeg(middel, 'studiejaren', f"{opleiding.get('school')} {opleiding.get('start')}", 'middel')
                break
    bewijs = [f'{label} {shown}' for label, shown, _w in sterk + middel]
    if sterk or len(middel) >= 2:
        oordeel = 'steun'
    elif naam:
        oordeel = 'onvoldoende'
    else:
        oordeel = 'geen gegevens'
    return {
        'oordeel': oordeel,
        'sterk_n': len(sterk),
        'middel_n': len(middel),
        'hits': sterk + middel,
        'bewijs': bewijs,
    }


def _nodig(opties):
    if len(opties) >= 2:
        return True
    if len(opties) != 1:
        return False
    info = opties[0][1]
    if info.get('apify_bevestigd') or info.get('goma_bevestigd'):
        return False
    hits = list(info.get('hits') or ())
    if not any(h[2] == 'sterk' for h in hits):
        return True
    labels = [str(h[0]).split(' (')[0] for h in hits if len(h) >= 3 and h[2] in ('sterk', 'middel')]

    def werkgever(label):
        return label.startswith(('werkgever', 'stagebedrijf', 'organisatie', 'bijbaan'))

    werk = {label for label in labels if werkgever(label)}
    ander = [label for label in labels if not werkgever(label)]
    # Naam plus één werkgever uit een zoekresultaat is niet genoeg. Dan Goma.
    return len(werk) == 1 and not ander


def _al_bekend(url, cache, klant):
    canon = linkedin_zoek.persoonlijke_url(url)
    if not canon or not isinstance(cache, dict):
        return False
    slug = canon.rsplit('/', 1)[-1]
    if uit_apify(cache.get('ap|' + slug)):
        return True
    bestaand = cache.get('goma|' + slug)
    if not isinstance(bestaand, dict) or not klant._vers(bestaand):
        return False
    return bool(bestaand.get('profiel') or bestaand.get('fout'))


def beoordeel(candidates, naam, cv, cache, klant=None, teller=None):
    """Vergelijk de kansrijkste gevonden profielen. Kies alleen bij een duidelijke winnaar."""
    opties = [(v, info) for (t, v), info in list(candidates.items()) if t == 'linkedin']
    if not _nodig(opties):
        return
    klant = klant or huidige()
    cache = cache if isinstance(cache, dict) else {}
    heeft_apify = any(uit_apify(cache.get('ap|' + (linkedin_zoek.persoonlijke_url(url).rsplit('/', 1)[-1] if linkedin_zoek.persoonlijke_url(url) else '')))
                      for url, _info in opties)
    if not sleutel() and not heeft_apify:
        return
    opties.sort(key=lambda paar: -len(paar[1].get('hits') or ()))
    if teller is None:
        gekozen = opties[:max_per_kandidaat()]
    else:
        ruimte = max(0, max_per_kandidaat() - int(teller.get('n') or 0))
        gekozen = []
        vers = 0
        for paar in opties:
            if _al_bekend(paar[0], cache, klant):
                gekozen.append(paar)
            elif vers < ruimte:
                gekozen.append(paar)
                vers += 1
    gevonden = {}
    voor = getattr(klant, 'nieuwe', 0)

    def taak(paar):
        url = paar[0]
        try:
            return url, klant.ophalen(url, cache)
        except Exception:
            return url, None

    if gekozen:
        with ThreadPoolExecutor(max_workers=min(MAX_TEGELIJK, len(gekozen))) as ex:
            for url, profiel in ex.map(taak, gekozen):
                gevonden[url] = profiel
    if teller is not None:
        teller['n'] = int(teller.get('n') or 0) + max(0, getattr(klant, 'nieuwe', 0) - voor)
    steun = []
    for url, info in gekozen:
        profiel = gevonden.get(url)
        if not profiel:
            continue
        oordeel = vergelijk(naam, cv, profiel)
        info['goma'] = oordeel['oordeel']
        if oordeel['oordeel'] == 'afwijzen':
            candidates.pop(('linkedin', url), None)
            continue
        if oordeel['oordeel'] == 'steun':
            steun.append((url, info, oordeel, profiel))
        elif oordeel['oordeel'] == 'onvoldoende':
            info['goma_onvoldoende'] = True
    if not steun:
        return
    beste = max(steun, key=lambda item: (item[2]['sterk_n'], item[2]['middel_n']))
    score = (beste[2]['sterk_n'], beste[2]['middel_n'])
    gelijk = [item for item in steun if (item[2]['sterk_n'], item[2]['middel_n']) == score]
    niet_bekeken = [url for url, _info in opties if url not in gevonden and ('linkedin', url) in candidates]
    if len(gelijk) >= 2 or niet_bekeken:
        for url, info, oordeel, profiel in gelijk:
            info['goma_geen_winnaar'] = True
            info['goma_bewijs'] = _bewijs(oordeel, profiel)
        return
    url, info, oordeel, profiel = beste
    info['goma_bevestigd'] = True
    info['goma_bewijs'] = _bewijs(oordeel, profiel)
    bron = profiel.get('bron') or 'goma'
    for label, shown, gewicht in oordeel['hits']:
        info.setdefault('hits', set()).add((label, f'{shown} ({bron})', gewicht))
        info.setdefault('match', set()).add(f'{label}: {shown}')


def _bewijs(oordeel, profiel):
    return {
        'bron': profiel.get('bron') or 'goma',
        'tijd': time.strftime('%Y-%m-%d', time.localtime()),
        'bewijs': list(oordeel.get('bewijs') or []),
    }


class Klant:
    def __init__(self, http=None, klok=None, slaap=None):
        self.http = http or _http
        self.klok = klok or time.time
        self.slaap = slaap or time.sleep
        self.op = False
        self.ongeldig = False
        self.timeouts = 0
        self.pauze_tot = 0.0
        self._gemeld = set()
        self._hek = threading.Semaphore(MAX_TEGELIJK)
        self._sloten = {}
        self._slot_lock = threading.Lock()
        try:
            self.wacht = float(os.environ.get('GOMA_RETRY_WACHT', '0.4') or 0)
        except (TypeError, ValueError):
            self.wacht = 0.4
        self.timeout = timeout_seconden()
        self.nieuwe = 0
        self._tel_lock = threading.Lock()

    def _meld(self, tekst):
        if tekst not in self._gemeld:
            self._gemeld.add(tekst)
            print(tekst, flush=True)

    def _slot(self, slug):
        with self._slot_lock:
            if slug not in self._sloten:
                self._sloten[slug] = threading.Lock()
            return self._sloten[slug]

    def _vers(self, entry):
        try:
            dagen = float(os.environ.get('GOMA_VERVERS_DAGEN', '90') or 90)
        except (TypeError, ValueError):
            dagen = 90.0
        try:
            moment = float(entry.get('op') or 0)
        except (TypeError, ValueError):
            return True
        if not moment:
            return True
        return (self.klok() - moment) < dagen * 86400

    def _uit_cache(self, cache, slug):
        bestaand = cache.get('goma|' + slug)
        if not isinstance(bestaand, dict) or not self._vers(bestaand):
            return False, None
        if bestaand.get('profiel'):
            return True, bestaand['profiel']
        if bestaand.get('fout'):
            return True, None
        return False, None

    def ophalen(self, url, cache):
        canon = linkedin_zoek.persoonlijke_url(url)
        if not canon:
            return None
        slug = canon.rsplit('/', 1)[-1]
        apify = uit_apify(cache.get('ap|' + slug) if isinstance(cache, dict) else None)
        if apify:
            return apify
        gelezen, profiel = self._uit_cache(cache, slug)
        if gelezen:
            return profiel
        if self.op or self.ongeldig or self.klok() < self.pauze_tot or not sleutel():
            return None
        with self._slot(slug):
            apify = uit_apify(cache.get('ap|' + slug))
            if apify:
                return apify
            gelezen, profiel = self._uit_cache(cache, slug)
            if gelezen:
                return profiel
            with self._hek:
                return self._vraag(canon, slug, cache)

    def _vraag(self, canon, slug, cache):
        api = verzoek_url(canon)
        laatste = None
        for poging in (1, 2):
            with self._tel_lock:
                global aanvragen_deze_run
                self.nieuwe += 1
                aanvragen_deze_run += 1
            try:
                status, headers, body = linkedin_zoek.met_deadline(
                    lambda: self.http(api, sleutel(), self.timeout), self.timeout)
            except TimeoutError:
                self.timeouts += 1
                if self.timeouts >= 2:
                    self.pauze_tot = self.klok() + PAUZE_SECONDEN
                    self.timeouts = 0
                    self._meld('GOMA: twee keer geen antwoord, vijf minuten overgeslagen')
                return None
            except (urllib.error.URLError, OSError, ValueError):
                if poging == 1:
                    self._pauze()
                    continue
                return None
            self.timeouts = 0
            laatste = self._verwerk(status, headers, body, slug, cache)
            if laatste == 'opnieuw' and poging == 1:
                self._pauze()
                continue
            if laatste == 'opnieuw':
                return None
            return laatste
        return None

    def _pauze(self):
        if self.wacht > 0:
            self.slaap(self.wacht)

    def _verwerk(self, status, headers, body, slug, cache):
        data = _json(body)
        code = ''
        if isinstance(data, dict) and isinstance(data.get('error'), dict):
            code = str(data['error'].get('code') or '')
        if status == 401 or code == 'unauthenticated':
            self.ongeldig = True
            self._meld('GOMA: sleutel ongeldig, deze ronde overgeslagen')
            return None
        if status == 402 or code == 'insufficient_credits':
            self.op = True
            self._meld('GOMA: tegoed op, verdere profielen deze ronde overgeslagen')
            return None
        if status == 404 or code == 'profile_not_found':
            cache['goma|' + slug] = {'fout': '404', 'op': self.klok(), 'bron': 'goma'}
            return None
        if status == 429 or code == 'rate_limited':
            wacht = _retry_after(headers)
            if wacht is not None and 0 <= wacht <= 2:
                self.slaap(wacht)
                return 'opnieuw'
            self.pauze_tot = self.klok() + min(wacht if wacht and wacht > 0 else PAUZE_SECONDEN, PAUZE_SECONDEN)
            self._meld('GOMA: te veel aanvragen, even overgeslagen')
            return None
        if status in (500, 502, 503, 504) or code in ('service_unavailable', 'internal_error'):
            return 'opnieuw'
        if status != 200 or not isinstance(data, dict):
            return None
        profiel = normaliseer(data, bron='goma')
        cache['goma|' + slug] = {'profiel': profiel, 'op': self.klok(), 'bron': 'goma', 'fout': ''}
        meta = data.get('meta') if isinstance(data.get('meta'), dict) else {}
        over = meta.get('credits_remaining')
        if over == 0:
            self.op = True
            self._meld('GOMA: tegoed op, verdere profielen deze ronde overgeslagen')
        return profiel


def _json(body):
    if isinstance(body, dict):
        return body
    try:
        return json.loads(body or '')
    except (TypeError, ValueError):
        return None


def _retry_after(headers):
    if not headers:
        return None
    raw = headers.get('Retry-After') if hasattr(headers, 'get') else None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _http(api, key, timeout):
    req = urllib.request.Request(api, headers={
        'Authorization': 'Bearer ' + key,
        'Accept': 'application/json',
        'User-Agent': 'CV-Scanner/12',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        try:
            tekst = exc.read().decode('utf-8', 'replace')
        except Exception:
            tekst = ''
        return exc.code, dict(getattr(exc, 'headers', {}) or {}), tekst
