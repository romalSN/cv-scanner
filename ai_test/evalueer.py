"""Laat een lokale AI (llama.cpp) testgevallen beoordelen: is dit LinkedIn-profiel dezelfde persoon als het cv?"""
import json, sys, time
from pathlib import Path

model, naam = sys.argv[1], sys.argv[2]
gevallen = json.loads((Path(__file__).parent / 'gevallen.json').read_text(encoding='utf-8'))
from llama_cpp import Llama
t0 = time.time()
llm = Llama(model_path=model, n_ctx=4096, n_threads=4, verbose=False)
laden = time.time() - t0

SYSTEEM = (
    "Je bent een zorgvuldige recruiter. Je krijgt een cv en een kort stukje tekst van een LinkedIn-profiel met "
    "dezelfde naam. Beslis of het profiel van DEZELFDE persoon is.\n"
    "Regels:\n"
    "- Dezelfde naam alleen is GEEN bewijs.\n"
    "- 'ja' alleen als een concreet, specifiek gegeven overeenkomt: dezelfde werkgever, dezelfde school of hetzelfde "
    "specifieke beroep in dezelfde plaats.\n"
    "- 'nee' als het profiel duidelijk iemand anders is: ander beroep, andere werkgevers, ander land, andere opleiding.\n"
    "- 'onzeker' als het profiel te weinig informatie geeft om het te weten (alleen plaats, alleen een algemene "
    "functie, of bijna geen tekst).\n"
    "Twijfel je, kies dan 'onzeker'. Een fout 'ja' is veel erger dan 'onzeker'.")
SCHEMA = {"type": "object", "properties": {
    "oordeel": {"type": "string", "enum": ["ja", "nee", "onzeker"]},
    "reden": {"type": "string"}}, "required": ["oordeel", "reden"]}

uitslag = {'goed': 0, 'gemist': 0, 'GEVAARLIJK': 0}
tijden = []
for g in gevallen:
    body = {"messages": [{"role": "system", "content": SYSTEEM},
                         {"role": "user", "content": f"CV:\n{g['cv']}\n\nLINKEDIN-PROFIEL:\n{g['profiel']}\n\n"
                                                      "Geef je oordeel als JSON met 'oordeel' en een korte 'reden'."}],
            "temperature": 0, "max_tokens": 120,
            "response_format": {"type": "json_object", "schema": SCHEMA}}
    t = time.time()
    try:
        out = llm.create_chat_completion(messages=body['messages'], temperature=0, max_tokens=120,
                                         response_format={"type": "json_object", "schema": SCHEMA})
        antwoord = json.loads(out['choices'][0]['message']['content'])
        oordeel, reden = antwoord.get('oordeel', '?'), antwoord.get('reden', '')
    except Exception as exc:
        oordeel, reden = 'fout', str(exc)[:80]
    tijden.append(time.time() - t)
    waar = g['waar']
    if oordeel == 'ja' and waar != 'ja':
        u = 'GEVAARLIJK'
    elif (waar == 'ja' and oordeel == 'ja') or (waar == 'nee' and oordeel == 'nee') or \
         (waar == 'onzeker' and oordeel in ('onzeker', 'nee')):
        u = 'goed'
    else:
        u = 'gemist'
    uitslag[u] += 1
    print(f"{naam} geval {g['id']:2d} waar={waar:8s} ai={oordeel:8s} {u:10s} {tijden[-1]:5.1f}s  {reden[:110]}")
gem = sum(tijden) / len(tijden)
regel = (f"{naam}: {uitslag['goed']}/{len(gevallen)} goed, {uitslag['GEVAARLIJK']} fout-ja (gevaarlijk), "
         f"{uitslag['gemist']} gemist/onzeker. Gem. {gem:.1f}s per geval op GitHub-Linux (CPU, 4 kernen), laden {laden:.0f}s")
print('::notice::' + regel)
