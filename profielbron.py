"""LinkedIn-profielen ophalen: eerst Apify, wat daar niet lukt (limiet, fout, niet klaar) via Bright Data.
Daarna (in de scanner) Serper -> SerpApi -> Tavily voor zoeken en extra bewijs."""
import apify_linkedin
import brightdata

brightdata.PREFIX = 'bd|'
brightdata.NAAM = 'Bright Data'
slug = brightdata.slug
BRONNEN = (apify_linkedin, brightdata)


def sleutel():
    return any(m.sleutel() for m in BRONNEN)


def NAAM_():
    return ' + '.join(m.NAAM for m in BRONNEN if m.sleutel())


def ophalen(urls, cache, log=print, **kw):
    """Apify eerst; alleen de adressen zonder (goed) Apify-resultaat gaan door naar Bright Data."""
    totaal = 0
    rest = list(urls)
    for m in BRONNEN:
        if not m.sleutel() or not rest:
            continue
        n = m.ophalen(rest, cache, log=log, **kw)
        totaal += max(n, 0)
        rest = [u for u in rest if not _goed(cache, m, slug(u))]
    return totaal


def _goed(cache, m, s):
    p = cache.get(m.PREFIX + s)
    return bool(p) and not p.get('fout')


def _profiel(cache, s):
    for m in BRONNEN:
        if _goed(cache, m, s):
            return m, cache[m.PREFIX + s]
    return None, None


def tekst_van(cache, s):
    m, p = _profiel(cache, s)
    return m.tekst(p) if m else ''


def gegevens_van(cache, s):
    m, p = _profiel(cache, s)
    return m.gegevens(p) if m else {}
