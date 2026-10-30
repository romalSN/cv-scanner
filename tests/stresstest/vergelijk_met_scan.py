#!/usr/bin/env python3
"""Gebruik: python vergelijk_met_scan.py /pad/naar/cv_bestanden.csv
Eventueel: python vergelijk_met_scan.py cv_bestanden.csv verwachte_uitkomsten.csv
Geen extra software nodig; een export wordt nooit aangepast.
"""
import csv, sys, unicodedata, re
from pathlib import Path
from collections import Counter, defaultdict

def open_csv(p):
    with Path(p).open(encoding='utf-8-sig',newline='') as f:
        sample=f.read(4096);f.seek(0)
        delim=';' if sample.count(';')>sample.count(',') else ','
        return list(csv.DictReader(f,delimiter=delim))

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

def key(s):
    return str(s or '').replace('\\','/').replace(' › ',' › ').lower()

def main():
    if len(sys.argv)<2:
        sys.exit('Gebruik: python vergelijk_met_scan.py <cv_bestanden.csv> [verwachte_uitkomsten.csv]')
    found=open_csv(sys.argv[1]); expected=open_csv(sys.argv[2] if len(sys.argv)>2 else Path(__file__).with_name('verwachte_uitkomsten.csv'))
    # Different root paths on Mac/Windows. Match on the exact relative segment, including zip members.
    mapped={}
    for r in found:
        k=key(r.get('oorspronkelijk_bestand') or r.get('bestand') or r.get('path'))
        for e in expected:
            rel=e['bestand'].split('Testbestanden/',1)[-1].lower()
            if k.endswith('/'+rel) or k.endswith('testbestanden/'+rel) or k.endswith(rel):
                mapped.setdefault(e['test_id'],r)
    counts=Counter(); problems=[]; ids={}
    for e in expected:
        r=mapped.get(e['test_id'])
        if r is None:
            counts['ONTBREEKT']+=1;problems.append((e['test_id'],'ONTBREEKT',e['bestand'],'geen exportregel'));continue
        want=e['verwacht_status']; got=(r.get('status') or '').strip().lower()
        name=(r.get('naam') or '').strip()
        ok=(got==want) if want in ('cv','other','error') else got in ('review','other')
        if want=='error': ok=got in ('error','niet leesbaar')
        if not ok:
            counts['VERKEERDE STATUS']+=1
            problems.append((e['test_id'],'STATUS',e['bestand'],f'verwacht {want}, kreeg {got}'))
        if want=='other' and got=='cv':counts['FOUT-POSITIEF']+=1
        if want=='cv' and got!='cv':counts['CV GEMIST']+=1
        en=e['verwacht_naam'].strip()
        if want=='cv' and got=='cv':
            if en and name and norm(en)!=norm(name):
                counts['VERKEERDE NAAM']+=1
                problems.append((e['test_id'],'NAAM',e['bestand'],f'verwacht {en!r}, kreeg {name!r}'))
            elif en and not name:
                counts['NAAM ONTBREEKT']+=1
                problems.append((e['test_id'],'NAAM LEEG',e['bestand'],f'verwacht: {en}'))
            elif not en and name:
                counts['NAAM VERZONNEN']+=1
                problems.append((e['test_id'],'ONBEWEZEN NAAM',e['bestand'],f'naam ten onrechte ingevuld: {name}'))
        if e['verwacht_persoon_groep'] and got=='cv' and r.get('persoon_id'):
            ids.setdefault(e['verwacht_persoon_groep'],set()).add(r['persoon_id'])
        if ok:counts['STATUS GOED']+=1
    for group,ps in ids.items():
        if len(ps)>1:
            counts['ONTERECHT GESPLITST']+=1
            problems.append((group,'SAMENVOEGEN',group,f'dezelfde verwachte persoon heeft IDs: {", ".join(sorted(ps))}'))
    # Different expected individuals sharing an ID is dangerous.
    reverse=defaultdict(set)
    for group,ps in ids.items():
        for pid in ps:reverse[pid].add(group)
    for pid,gs in reverse.items():
        if len(gs)>1:
            counts['ONTERECHT SAMENGEVOEGD']+=1
            problems.append((pid,'ONTERECHTE MERGE',pid,', '.join(sorted(gs))))
    print(f'\nVERGELIJKING: {len(expected)} testbestanden | {len(mapped)} gevonden exportregels')
    for k,v in sorted(counts.items()):print(f'  {k:28} {v}')
    print('\nAFWIJKINGEN (maximaal eerste 120):')
    for case,typ,path,why in problems[:120]:print(f' - {case} [{typ}] {path} :: {why}')
    if len(problems)>120:print(f' ... plus {len(problems)-120} andere afwijkingen')
    if not problems:print('Geen afwijkingen gevonden in deze vergelijking.')
    print('\nLet op: daadwerkelijke inhoud, OCR en onterechte merges moeten altijd nog inhoudelijk worden nagekeken.')
    return 1 if problems else 0
if __name__=='__main__':raise SystemExit(main())
