"""LinkedIn-profielen zoeken: gerichte zoekopdrachten en doorschakelen tussen zoekdiensten.

Volgorde: Serper, SerpApi, Tavily, Linkup.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
from pathlib import Path

OPEN_BESTAND = 'linkedin_nog_zoeken.json'
MAX_ZOEKOPDRACHTEN = 4
PAUZE_SECONDEN = 300

_IN = re.compile(r'linkedin\.com/in/([^/?#]+)', re.I)
_NIET_PERSOON = re.compile(
    r'linkedin\.com/(?:company|school|showcase|jobs|learning|posts|pub|feed|search|recruiter)/',
    re.I)
_SLUG_NEE = re.compile(
    r'^(?:recruiter|recruitment|talent|talentacquisition|hiring|jobs|careers|career|hr|'
    r'humanresources|company|bedrijf|referentie|reference|about|sales)$',
    re.I)


class GeenSleutel(Exception):
    pass


class NietBeschikbaar(Exception):
    """Deze zoekdienst mag of kan nu niet automatisch gebruikt worden."""


def zoekbudget():
    ruw = os.environ.get('LINKEDIN_MAX_ZOEKOPDRACHTEN', '').strip()
    try:
        aantal = int(ruw) if ruw else MAX_ZOEKOPDRACHTEN
    except ValueError:
        aantal = MAX_ZOEKOPDRACHTEN
    return max(1, min(aantal, 8))


def persoonlijke_url(url):
    """Persoonlijk profieladres, of '' als het bedrijf, recruiter of referentie is."""
    tekst = str(url or '').strip()
    if not tekst or _NIET_PERSOON.search(tekst):
        return ''
    m = _IN.search(tekst)
    if not m:
        return ''
    slug = urllib.parse.unquote(m.group(1)).strip('/')
    if not slug or _SLUG_NEE.match(slug.replace('-', '').replace('_', '')):
        return ''
    return 'https://www.linkedin.com/in/' + slug.lower()


def naam_varianten(naam):
    """Volledige naam plus initialen. 'Jan de Vries' -> ook 'J. de Vries'."""
    naam = ' '.join(str(naam or '').replace('"', '').split())
    if not naam:
        return []
    out = [naam]
    parts = naam.split()
    if len(parts) >= 2 and len(parts[0]) > 1:
        rest = ' '.join(parts[1:])
        letter = parts[0][0].upper()
        out.append(f'{letter}. {rest}')
        out.append(f'{letter} {rest}')
    gezien = set()
    uniek = []
    for item in out:
        sleutel = item.lower()
        if sleutel not in gezien:
            gezien.add(sleutel)
            uniek.append(item)
    return uniek[:3]


def _quote(term):
    term = ' '.join(str(term or '').replace('"', '').split())
    return f'"{term}"' if term else ''


def _of(terms):
    delen = []
    gezien = set()
    for term in terms or []:
        stuk = _quote(term)
        sleutel = stuk.lower()
        if stuk and sleutel not in gezien:
            gezien.add(sleutel)
            delen.append(stuk)
    if not delen:
        return ''
    if len(delen) == 1:
        return delen[0]
    return '(' + ' OR '.join(delen) + ')'


def maak_zoekopdrachten(naam, werkgevers=(), functies=(), opleidingen=(), plaatsen=(), maximum=MAX_ZOEKOPDRACHTEN):
    """Sterke combinaties eerst. De laatste plek blijft de bredere opdracht.

    Nooit alles in één zoekopdracht. Meerdere werkgevers, functies, opleidingen of plaatsen
    gaan als OR in hun eigen opdracht. Het budget laat de bredere opdracht niet vallen.
    """
    varianten = naam_varianten(naam)
    if not varianten:
        return []
    try:
        maximum = max(1, int(maximum))
    except (TypeError, ValueError):
        maximum = MAX_ZOEKOPDRACHTEN
    naam_deel = _of(varianten)
    basis = f'site:linkedin.com/in/ {naam_deel}'

    def opdracht(rest):
        return basis + (f' {rest}' if rest else '')

    specifiek = []
    if werkgevers:
        specifiek.append(opdracht(_of(list(werkgevers)[:3])))
    if functies:
        specifiek.append(opdracht(_of(list(functies)[:3])))
    if opleidingen:
        specifiek.append(opdracht(_of(list(opleidingen)[:3])))
    if plaatsen:
        breder = opdracht(_of(list(plaatsen)[:2]))
    else:
        breder = basis
    if maximum == 1:
        return [specifiek[0] if specifiek else breder]
    gekozen = []
    for q in specifiek:
        if q not in gekozen:
            gekozen.append(q)
    if breder not in gekozen:
        gekozen.append(breder)
    if len(gekozen) <= maximum:
        return gekozen
    # Specifieke opdrachten inkorten, de bredere opdracht achteraan houden.
    return gekozen[:maximum - 1] + [gekozen[-1]]


def profielrijen(rows):
    """Alleen persoonlijke /in/-adressen, elk adres één keer."""
    out = []
    gezien = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        url = persoonlijke_url(row.get('url') or '')
        if not url or url in gezien:
            continue
        gezien.add(url)
        nieuw = dict(row)
        nieuw['url'] = url
        out.append(nieuw)
    return out


def met_deadline(fn, timeout):
    """Echte maximale wachttijd. Een snel antwoord komt meteen terug."""
    if timeout <= 0:
        raise TimeoutError('timeout')
    doos = {}
    klaar = threading.Event()

    def run():
        try:
            doos['waarde'] = fn()
        except BaseException as exc:
            doos['fout'] = exc
        finally:
            klaar.set()

    threading.Thread(target=run, daemon=True).start()
    if not klaar.wait(timeout):
        raise TimeoutError('timeout')
    if 'fout' in doos:
        raise doos['fout']
    return doos.get('waarde')


def _retry_after(exc):
    hdrs = getattr(exc, 'headers', None)
    if not hdrs or not hasattr(hdrs, 'get'):
        return None
    raw = hdrs.get('Retry-After')
    if raw is None or raw == '':
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class Bronnen:
    """Serper, dan SerpApi, dan Tavily, dan Linkup."""

    VOLGORDE = ('Serper', 'SerpApi', 'Tavily', 'Linkup')

    def __init__(self, aanroep, timeout=None, wacht=None, klok=None, slaap=None):
        self.aanroep = dict(aanroep or {})
        self.timeout = dict(timeout or {})
        self.wacht = 0.4 if wacht is None else float(wacht)
        self.klok = klok or time.time
        self.slaap = slaap or time.sleep
        self.op = set()
        self.pauze_tot = {}
        self.timeout_reeks = {}
        self.gemeld = set()
        self.overgeslagen = set()
        self.aanvragen = 0
        self.per_bron = {}
        self.geprobeerd = set()

    def timeout_voor(self, naam):
        if naam in self.timeout:
            try:
                return float(self.timeout[naam])
            except (TypeError, ValueError):
                return 10.0
        ruw = os.environ.get('LINKEDIN_TIMEOUT_' + naam.upper(), '').strip()
        try:
            return float(ruw) if ruw else 10.0
        except ValueError:
            return 10.0

    def beschikbaar(self, naam):
        if naam in self.op or naam in self.overgeslagen:
            return False
        tot = self.pauze_tot.get(naam) or 0
        if tot and self.klok() < tot:
            return False
        return self.aanroep.get(naam) is not None

    def geen_bronnen_meer(self):
        return not any(self.beschikbaar(naam) for naam in self.VOLGORDE)

    def _meld(self, tekst):
        if tekst not in self.gemeld:
            self.gemeld.add(tekst)
            print(tekst, flush=True)

    def zoek(self, query, na_resultaat=None, na_bron=None):
        """Geeft (rijen, bron, toestand).

        toestand is gevonden, bevestigd, leeg, fout of geen bron.
        Zonder na_resultaat stopt de eerste dienst die een profiel teruggeeft.
        Met na_resultaat gaat de volgende dienst door zolang het profiel niet klopt.
        na_resultaat(rijen) geeft 'stop' of 'verder'.
        na_bron slaat die dienst en alles ervoor over.
        """
        geantwoord = False
        gefaald = False
        verzameld = []
        gezien = set()
        laatste = ''
        begonnen = na_bron is None
        for naam in self.VOLGORDE:
            if not begonnen:
                if naam == na_bron:
                    begonnen = True
                continue
            if not self.beschikbaar(naam):
                continue
            self.geprobeerd.add(naam)
            poging = 0
            while True:
                poging += 1
                try:
                    rows = met_deadline(lambda: self.aanroep[naam](query), self.timeout_voor(naam))
                except GeenSleutel:
                    self.overgeslagen.add(naam)
                    self._meld(f'LINKEDIN: {naam} overgeslagen (geen sleutel). De sleutel blijft bewaard.')
                    break
                except NietBeschikbaar as exc:
                    self.overgeslagen.add(naam)
                    self._meld('LINKEDIN: ' + (str(exc) or f'{naam} is niet beschikbaar. Deze stap wordt overgeslagen.'))
                    break
                except TimeoutError:
                    self.aanvragen += 1
                    self.per_bron[naam] = self.per_bron.get(naam, 0) + 1
                    self.timeout_reeks[naam] = self.timeout_reeks.get(naam, 0) + 1
                    if self.timeout_reeks[naam] >= 2:
                        self.pauze_tot[naam] = self.klok() + PAUZE_SECONDEN
                        self.timeout_reeks[naam] = 0
                        self._meld(f'LINKEDIN: {naam} twee keer te laat. Vijf minuten overgeslagen, de volgende zoekdienst gaat verder.')
                    gefaald = True
                    break
                except urllib.error.HTTPError as exc:
                    self.aanvragen += 1
                    self.per_bron[naam] = self.per_bron.get(naam, 0) + 1
                    if exc.code in (400, 401, 402, 403, 432, 433):
                        self.op.add(naam)
                        self._meld(f'LINKEDIN: {naam} stopt deze ronde (sleutel of tegoed). De sleutel blijft bewaard.')
                        break
                    if exc.code == 429:
                        wacht = _retry_after(exc)
                        if wacht is not None and 0 <= wacht <= 2 and poging == 1:
                            self.slaap(wacht)
                            continue
                        duren = wacht if wacht is not None and wacht > 0 else PAUZE_SECONDEN
                        self.pauze_tot[naam] = self.klok() + min(duren, PAUZE_SECONDEN)
                        self._meld(f'LINKEDIN: {naam} vraagt om langzamer te zoeken. Verder met de volgende zoekdienst.')
                        break
                    if exc.code in (500, 502, 503, 504) and poging == 1:
                        self.slaap(self.wacht)
                        continue
                    gefaald = True
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    self.aanvragen += 1
                    self.per_bron[naam] = self.per_bron.get(naam, 0) + 1
                    if poging == 1:
                        self.slaap(self.wacht)
                        continue
                    gefaald = True
                    break
                self.aanvragen += 1
                self.per_bron[naam] = self.per_bron.get(naam, 0) + 1
                self.timeout_reeks[naam] = 0
                geantwoord = True
                bruikbaar = profielrijen(rows)
                if not bruikbaar:
                    break
                for row in bruikbaar:
                    row['bron'] = naam
                if na_resultaat is None:
                    return bruikbaar, naam, 'gevonden'
                nieuw = False
                for row in bruikbaar:
                    url = row.get('url') or ''
                    if url and url not in gezien:
                        gezien.add(url)
                        verzameld.append(row)
                        nieuw = True
                laatste = naam
                if nieuw and na_resultaat(list(verzameld)) == 'stop':
                    return list(verzameld), naam, 'bevestigd'
                break
        if verzameld:
            return verzameld, laatste, 'gevonden'
        if geantwoord:
            return [], '', 'leeg'
        if gefaald:
            return None, '', 'fout'
        return None, '', 'geen bron'


def laatste_bron(keten):
    """Verste zoekdienst die in deze ronde echt is geprobeerd."""
    laatste = ''
    geprobeerd = getattr(keten, 'geprobeerd', ())
    for naam in Bronnen.VOLGORDE:
        if naam in geprobeerd:
            laatste = naam
    return laatste


def zoek_profielen(queries, cache, keten, sleutel, na_resultaat=None, na_bron=None):
    """Zoekopdrachten achter elkaar. Cache per opdracht. Geeft (rijen, 'af' of 'open').

    na_resultaat krijgt alle profielen tot nu toe. 'stop' betekent: dit profiel klopt,
    de rest van de zoekopdrachten en zoekdiensten blijft achterwege.
    """
    samen = []
    gezien = set()
    af = True

    def tot_nu(extra):
        uit = []
        lokaal = set()
        for row in list(samen) + list(extra or []):
            url = persoonlijke_url((row or {}).get('url') or '')
            if not url or url in lokaal:
                continue
            lokaal.add(url)
            item = dict(row)
            item['url'] = url
            uit.append(item)
        return uit

    def bewaker(rijen):
        if na_resultaat is None:
            return 'verder'
        return na_resultaat(tot_nu(rijen))

    for query in queries or []:
        stuk = ('|' + na_bron) if na_bron else ''
        qkey = 'xq|' + str(sleutel) + stuk + '|' + hashlib.sha1(query.encode('utf-8')).hexdigest()[:12]
        if qkey in cache:
            rows = cache.get(qkey) or []
            toestand = 'gevonden' if rows else 'leeg'
        else:
            rows, _bron, toestand = keten.zoek(
                query, na_resultaat=bewaker if na_resultaat else None, na_bron=na_bron)
            if toestand in ('gevonden', 'leeg', 'bevestigd'):
                cache[qkey] = rows or []
                cache['qq|' + qkey] = query
                rows = rows or []
            else:
                af = False
                break
        for row in rows or []:
            url = persoonlijke_url((row or {}).get('url') or '')
            if not url or url in gezien:
                continue
            gezien.add(url)
            item = dict(row)
            item['url'] = url
            samen.append(item)
        if toestand == 'bevestigd':
            return samen, 'af'
    return samen, ('af' if af else 'open')


def lees_open(pad):
    try:
        data = json.loads(Path(pad).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return []
    if isinstance(data, dict):
        return [str(x) for x in data.get('open') or [] if x]
    if isinstance(data, list):
        return [str(x) for x in data if x]
    return []


def schrijf_open(pad, ids):
    try:
        Path(pad).write_text(json.dumps({'open': sorted({str(x) for x in ids if x})}, ensure_ascii=False),
                             encoding='utf-8')
    except OSError:
        pass
