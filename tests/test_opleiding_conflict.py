"""Opleidingsconflict: nooit automatisch samen en nooit een dubbel-twijfelmelding.
Echte naamgenoten, dezelfde privé-mail en telefoon mogen een bewezen botsing niet opheffen.
"""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CV_AI", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv_crawler as c


def cv(name, mail, telefoon, opleiding, extra=""):
    text = (f"Curriculum Vitae\n{name}\n{mail}\n{telefoon}\n"
            "Werkervaring\n2016 - 2024 Data analist bij Rivierdata.\n"
            f"Opleiding\n{opleiding}\n{extra}")
    return dict(name=name, emails=json.dumps([mail]), phones=json.dumps([telefoon]),
                links="[]", sha=opleiding, path="/tmp/" + opleiding[:10] + ".txt",
                snippet=c.normalized_snippet(text), tokens=json.dumps(c.signature(text)),
                werkgevers=c.werkgevers_from(text), opleidingen=c.opleidingen_from(text),
                woonplaats="")


def apart(label, x, y):
    assert c.opleiding_botst(x, y), f"{label}: opleidingsbotsing niet gezien"
    assert c.harde_blokkade(x, y), f"{label}: geen blokkade"
    auto, review, reason = c.safe_pair(x, y)
    assert not auto and not review, f"{label}: auto={auto}, review={review}: {reason}"


a = cv("Jan de Vries", "jan.devries@gmail.com", "+31611112222",
       "2010 - 2014 HBO Informatica Hogeschool Utrecht")
b = cv("Jan de Vries", "jan.devries@gmail.com", "+31699998888",
       "2010 - 2014 HBO Informatica Hogeschool van Amsterdam")
apart("zelfde naam/mail; andere schoolplaats", a, b)

d = cv("Jan de Vries", "jan.devries@gmail.com", "+31611112222",
       "2004 - 2008 HBO Informatica Hogeschool Utrecht")
apart("zelfde naam/mail/telefoon; ander studiejaar", a, d)

e = cv("Jan de Vries", "jan.devries@gmail.com", "+31622223333",
       "2014 - 2016 Master Data Science Universiteit Utrecht")
assert not c.opleiding_botst(a, e), "bachelor/master is normaal een vervolgopleiding"
assert c.safe_pair(a, e)[0], "zelfde persoonlijke mail en bachelor->master moeten samen kunnen"

# Andere woonplaats of nieuwe contactgegevens is nooit op zichzelf een opleidingsconflict.
f = cv("Jan de Vries", "jan.nieuw@gmail.com", "+31688887777",
       "2010 - 2014 HBO Informatica Hogeschool Utrecht",
       "Woonplaats Amsterdam")
assert not c.opleiding_botst(a, f), "verhuizen mag niet tot blokkade leiden"
print("OPLEIDINGSBOTSING OK")
