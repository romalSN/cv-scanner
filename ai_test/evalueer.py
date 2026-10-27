"""Laat een lokale AI (llama.cpp) testgevallen beoordelen: is dit LinkedIn-profiel dezelfde persoon als het cv?
Twee uitslagen: 'AI alleen' en 'AI + controle' (de scanner controleert of het genoemde bewijs echt in beide teksten staat)."""
import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv_crawler as c  # noqa: E402

model, naam = sys.argv[1], sys.argv[2]
gevallen = json.loads((Path(__file__).parent / 'gevallen.json').read_text(encoding='utf-8'))
from llama_cpp import Llama  # noqa: E402
t0 = time.time()
llm = Llama(model_path=model, n_ctx=4096, n_threads=4, verbose=False)
laden = time.time() - t0

INSTRUCTIE = (
    "Je bent een zorgvuldige recruiter. Je krijgt een cv en een kort stukje tekst van een LinkedIn-profiel met "
    "dezelfde naam. Beslis of het profiel van DEZELFDE persoon is.\n"
    "Werkwijze:\n"
    "1. 'overeenkomsten': noem alleen concrete, specifieke dingen die LETTERLIJK in allebei staan: naam van een "
    "werkgever/bedrijf of naam van een school/universiteit. Geen plaatsnamen, geen algemene functies, niet de naam.\n"
    "2. 'verschillen': dingen die elkaar tegenspreken (ander beroep, andere werkgevers, ander land, andere opleiding).\n"
    "3. 'oordeel': 'ja' alleen als er minstens één overeenkomst is en geen duidelijk verschil; 'nee' als er duidelijke "
    "verschillen zijn; anders 'onzeker'. Een fout 'ja' is veel erger dan 'onzeker'.")
SCHEMA = {"type": "object", "properties": {
    "overeenkomsten": {"type": "array", "items": {"type": "string"}},
    "verschillen": {"type": "array", "items": {"type": "string"}},
    "oordeel": {"type": "string", "enum": ["ja", "nee", "onzeker"]}},
    "required": ["overeenkomsten", "verschillen", "oordeel"]}


def echt_bewijs(items, cv, profiel):
    """Alleen overeenkomsten die echt in cv EN profiel staan, en meer zijn dan een plaats of algemene functie."""
    ok = []
    ncv, npr = c.normalized_name(cv), c.normalized_name(profiel)
    for it in items:
        t = c.normalized_name(re.sub(r'(?i)^(werkgever|bedrijf|school|universiteit|opleiding|employer|company)\s*[:\-]?\s*', '', it))
        if not t or t not in ncv or t not in npr:
            continue
        if not (c.evidence_term_ok('werkgever', t) or c.evidence_term_ok('school', t)):
            continue
        ok.append(t)
    return ok


def score(waar, oordeel):
    if oordeel == 'ja' and waar != 'ja':
        return 'GEVAARLIJK'
    if (waar == 'ja' and oordeel == 'ja') or (waar == 'nee' and oordeel == 'nee') or \
            (waar == 'onzeker' and oordeel in ('onzeker', 'nee')):
        return 'goed'
    return 'gemist'


uit = {'ai': {'goed': [], 'gemist': [], 'GEVAARLIJK': []}, 'samen': {'goed': [], 'gemist': [], 'GEVAARLIJK': []}}
tijden, fouten = [], 0
for g in gevallen:
    vraag = (INSTRUCTIE + f"\n\nCV:\n{g['cv']}\n\nLINKEDIN-PROFIEL:\n{g['profiel']}\n\n"
             "Antwoord alleen met JSON: overeenkomsten, verschillen, oordeel.")
    t = time.time()
    try:
        out = llm.create_chat_completion(messages=[{"role": "user", "content": vraag}], temperature=0, max_tokens=200,
                                         response_format={"type": "json_object", "schema": SCHEMA})
        a = json.loads(out['choices'][0]['message']['content'])
        oordeel = a.get('oordeel', '?')
        bewijs = echt_bewijs(a.get('overeenkomsten') or [], g['cv'], g['profiel'])
        samen = oordeel if oordeel != 'ja' else ('ja' if bewijs and not a.get('verschillen') else 'onzeker')
    except Exception as exc:
        oordeel = samen = 'fout'
        a, bewijs = {'fout': str(exc)[:100]}, []
        fouten += 1
    tijden.append(time.time() - t)
    uit['ai'][score(g['waar'], oordeel)].append(g['id'])
    uit['samen'][score(g['waar'], samen)].append(g['id'])
    print(f"{naam} {g['id']:2d} waar={g['waar']:8s} ai={oordeel:8s} samen={samen:8s} {tijden[-1]:5.1f}s "
          f"bewijs={bewijs} {json.dumps(a, ensure_ascii=False)[:200]}")
gem = sum(tijden) / len(tijden)
n = len(gevallen)
for k, label in (('ai', 'AI alleen'), ('samen', 'AI + controle')):
    u = uit[k]
    print(f"::notice::{naam} | {label}: {len(u['goed'])}/{n} goed, fout-ja {len(u['GEVAARLIJK'])} {u['GEVAARLIJK']}, "
          f"gemist {len(u['gemist'])} {u['gemist']}")
print(f"::notice::{naam} | {gem:.1f}s per geval (GitHub-Linux CPU), laden {laden:.0f}s, crashes {fouten}")
