"""AI bij twijfelgevallen: drie vraagvormen naast elkaar. Gemeten: geen-cv als 'cv' (gevaarlijk), cv als 'geen cv'
(ook fout: dan verdwijnt een cv), en of de naam klopt."""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['CV_AI_MODEL'] = sys.argv[1]
naam_model = sys.argv[2]
import ai_rechter  # noqa: E402
gevallen = json.loads((Path(__file__).parent / 'twijfel_gevallen.json').read_text(encoding='utf-8'))
llm = ai_rechter._load() if ai_rechter.model_ok() else None
if llm is None:
    from llama_cpp import Llama
    llm = Llama(model_path=sys.argv[1], n_ctx=4096, n_threads=4, verbose=False)

B_TEKST = (
    "Hieronder staat de tekst van een document (automatisch uitgelezen: kleine letters, zonder leestekens).\n"
    "Vraag 1 'ander_document': is dit een van deze soorten? sollicitatiebrief, vacature, referentie (iemand anders "
    "schrijft over de persoon), certificaat, contract, offerte, verslag, leeg_formulier, lijst_meerdere_personen, "
    "korte_notitie. Zo niet: geen_van_deze.\n"
    "Vraag 2 'eigen_loopbaan': staan er banen, opdrachten of opleidingen van de persoon ZELF in, met jaartallen of "
    "werkgevers? ja of nee.\n"
    "Vraag 3 'naam': de naam van die persoon, of leeg.")
B_SCHEMA = {"type": "object", "properties": {
    "ander_document": {"type": "string", "enum": ["geen_van_deze", "sollicitatiebrief", "vacature", "referentie",
                                                  "certificaat", "contract", "offerte", "verslag", "leeg_formulier",
                                                  "lijst_meerdere_personen", "korte_notitie"]},
    "eigen_loopbaan": {"type": "string", "enum": ["ja", "nee"]},
    "naam": {"type": "string"}}, "required": ["ander_document", "eigen_loopbaan", "naam"]}

C_TEKST = (
    "Is dit document een CV (overzicht van de eigen loopbaan van één persoon)? Antwoord cv, geen_cv of twijfel.\n"
    "Voorbeelden:\n"
    "- 'jan smit jan smit gmail com werkervaring 2015 heden developer bij codewerk opleiding hbo ict' -> cv\n"
    "- 'profielschets karin veld recente opdrachten opdrachtgever codewerk 2019 2024 java developer' -> cv\n"
    "- 'geachte heer mevrouw met veel interesse las ik uw vacature met vriendelijke groet' -> geen_cv (brief)\n"
    "- 'vacature developer wat ga je doen wat vragen wij wat bieden wij' -> geen_cv (vacature)\n"
    "- 'hierbij verklaar ik dat jan smit bij ons werkte hij was betrouwbaar' -> geen_cv (referentie)\n"
    "- 'certificaat hierbij verklaren wij dat jan smit de training heeft afgerond' -> geen_cv\n"
    "- 'overzicht kandidaten 1 jan 2 lisa 3 tom' -> geen_cv (lijst)\n"
    "Geef ook de naam van de persoon als het een cv is.")
C_SCHEMA = {"type": "object", "properties": {
    "antwoord": {"type": "string", "enum": ["cv", "geen_cv", "twijfel"]}, "naam": {"type": "string"}},
    "required": ["antwoord", "naam"]}


def ask(tekst, schema, doc):
    out = llm.create_chat_completion(messages=[{"role": "user", "content": f"{tekst}\n\nDOCUMENT:\n{doc[:3000]}"}],
                                     temperature=0, max_tokens=120,
                                     response_format={"type": "json_object", "schema": schema})
    return json.loads(out['choices'][0]['message']['content'])


def b(doc):
    a = ask(B_TEKST, B_SCHEMA, doc)
    if a['ander_document'] != 'geen_van_deze':
        return 'geen_cv', a
    return ('cv' if a['eigen_loopbaan'] == 'ja' else 'onzeker'), a


def cvar(doc):
    a = ask(C_TEKST, C_SCHEMA, doc)
    return {'cv': 'cv', 'geen_cv': 'geen_cv'}.get(a['antwoord'], 'onzeker'), a


for label, fn in (('B', b), ('C', cvar), ('A', lambda d: (ai_rechter.classify(d) or {}).get('soort', 'onzeker'))):
    fout_cv, gemist, goed, ant, tijden = [], [], [], {}, []
    for g in gevallen:
        t = time.time()
        r = fn(g['tekst'])
        soort = r[0] if isinstance(r, tuple) else r
        tijden.append(time.time() - t)
        ant[g['id']] = soort[:3]
        if soort == 'cv' and g['waar'] != 'cv':
            fout_cv.append(g['id'])
        elif g['waar'] == 'cv' and soort == 'geen_cv':
            gemist.append(g['id'])
        elif (g['waar'] == 'cv' and soort == 'cv') or (g['waar'] != 'cv' and soort == 'geen_cv') or \
                (g['waar'] == 'onzeker' and soort != 'cv'):
            goed.append(g['id'])
        print(naam_model, label, g['id'], g['waar'], soort, r)
    print(f"::notice::{naam_model} | TWIJFEL {label}: {len(goed)}/{len(gevallen)} goed, FOUT 'cv' {fout_cv}, "
          f"cv weggegooid {gemist}, {sum(tijden)/len(tijden):.1f}s | " + ' '.join(f'{k}:{v}' for k, v in ant.items()))
