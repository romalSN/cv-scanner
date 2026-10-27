"""Test van regels + lokale AI samen, zoals het in de scanner zou werken.
- AI zegt 'nee'                    -> profiel afgewezen
- AI zegt 'ja' en er is bewijs      -> zeker
- AI 'onzeker' + specifiek bewijs   -> zeker (regels alleen, zoals nu)
- AI 'onzeker' + alleen groot bedrijf/school of geen bewijs -> controleren
Gemeten: foute 'zeker' (gevaarlijk), juiste profielen ten onrechte afgewezen, en wat goed gaat."""
import json, sys, time
from pathlib import Path

model, naam = sys.argv[1], sys.argv[2]
gevallen = json.loads((Path(__file__).parent / 'gevallen.json').read_text(encoding='utf-8'))
from llama_cpp import Llama  # noqa: E402
t0 = time.time()
llm = Llama(model_path=model, n_ctx=4096, n_threads=4, verbose=False)
laden = time.time() - t0

INSTRUCTIE = (
    "Je bent een zorgvuldige recruiter. Je krijgt een cv en een kort stukje tekst van een LinkedIn-profiel met "
    "dezelfde naam. Beslis of het profiel van DEZELFDE persoon is, of van een naamgenoot.\n"
    "Let op:\n"
    "- Dezelfde naam alleen zegt niets.\n"
    "- Hetzelfde bedrijf of dezelfde school alleen is NIET genoeg als de rest niet past. Kijk naar functie, niveau, "
    "vakgebied en periode. Voorbeelden van een ANDERE persoon: cv 'stagiair/kassamedewerker/teamleider' en profiel "
    "'CEO/CFO/directeur/hoogleraar/oprichter sinds 1999' bij hetzelfde bedrijf; cv 'student' en profiel 'docent sinds "
    "1998' op dezelfde school; cv 'developer' en profiel 'marketing director'. Dan is het oordeel 'nee'.\n"
    "- Ander beroep, ander vakgebied, ander land, andere werkgevers of andere opleiding: 'nee'.\n"
    "- 'ja' als werkgever of school overeenkomt EN functie, vakgebied en periode ook passen.\n"
    "- 'onzeker' als het profiel te weinig informatie geeft.\n"
    "Antwoord met JSON: 'past' (wat overeenkomt), 'botst' (wat elkaar tegenspreekt), 'oordeel' (ja/nee/onzeker).")
SCHEMA = {"type": "object", "properties": {
    "past": {"type": "array", "items": {"type": "string"}},
    "botst": {"type": "array", "items": {"type": "string"}},
    "oordeel": {"type": "string", "enum": ["ja", "nee", "onzeker"]}},
    "required": ["past", "botst", "oordeel"]}


def eind(ai, bewijs):
    if ai == 'nee':
        return 'afgewezen'
    if ai == 'ja' and bewijs != 'geen':
        return 'zeker'
    if bewijs == 'specifiek':
        return 'zeker'
    return 'controleren'


res = {'GEVAARLIJK': [], 'ONTERECHT_AFGEWEZEN': [], 'goed': [], 'controleren': []}
ai_ant, tijden, crashes = {}, [], 0
for g in gevallen:
    vraag = f"{INSTRUCTIE}\n\nCV:\n{g['cv']}\n\nLINKEDIN-PROFIEL:\n{g['profiel']}"
    t = time.time()
    try:
        out = llm.create_chat_completion(messages=[{"role": "user", "content": vraag}], temperature=0, max_tokens=220,
                                         response_format={"type": "json_object", "schema": SCHEMA})
        a = json.loads(out['choices'][0]['message']['content'])
        ai = a.get('oordeel', 'onzeker')
    except Exception as exc:
        ai, a = 'onzeker', {'crash': str(exc)[:80]}     # vastgelopen = onzeker (veilig)
        crashes += 1
    tijden.append(time.time() - t)
    e = eind(ai, g['bewijs'])
    ai_ant[g['id']] = ai[:3]
    w = g['waar']
    if e == 'zeker' and w != 'ja':
        res['GEVAARLIJK'].append(g['id'])
    elif e == 'afgewezen' and w == 'ja':
        res['ONTERECHT_AFGEWEZEN'].append(g['id'])
    elif (e == 'zeker' and w == 'ja') or (e == 'afgewezen' and w == 'nee') or (e != 'zeker' and w == 'onzeker'):
        res['goed'].append(g['id'])
    else:
        res['controleren'].append(g['id'])
    print(f"{naam} {g['id']:2d} waar={w:8s} bewijs={g['bewijs']:9s} ai={ai:8s} eind={e:11s} "
          f"{json.dumps(a, ensure_ascii=False)[:180]}")

# Ter vergelijking: alleen regels (zonder AI): specifiek of groot bewijs = zeker, anders controleren.
regels = {'GEVAARLIJK': [g['id'] for g in gevallen if g['bewijs'] != 'geen' and g['waar'] != 'ja'],
          'controleren': [g['id'] for g in gevallen if g['bewijs'] == 'geen']}
n = len(gevallen)
print(f"::notice::{naam} | REGELS+AI: {len(res['goed'])}/{n} goed, FOUTE ZEKER {res['GEVAARLIJK']}, "
      f"ONTERECHT AFGEWEZEN {res['ONTERECHT_AFGEWEZEN']}, nog controleren {res['controleren']}")
print(f"::notice::{naam} | ALLEEN REGELS: foute zeker {regels['GEVAARLIJK']}, controleren {len(regels['controleren'])}")
print(f"::notice::{naam} | antwoorden " + ' '.join(f"{k}:{v}" for k, v in ai_ant.items()))
print(f"::notice::{naam} | {sum(tijden) / n:.1f}s per geval (GitHub-Linux CPU), crashes {crashes}")
