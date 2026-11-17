"""Wikidata: the structured memory of things that had a place and a time.

A demolished building is a lost thing with a very particular kind of shadow: a
pin on a map, a date it rose and a date it fell, a surviving photograph. Wikidata
records exactly that for tens of thousands of structures the world has torn down.
This reads them --- generically, the whole class at once --- so the machine can
stand them back up at their real coordinates, in the years they existed.

Free SPARQL endpoint, no key. It is sometimes heavily rate-limited, so this does
ONE bulk query and is patient with 429s.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..http_util import USER_AGENT

# QLever: a free, fast, independent SPARQL endpoint over all of Wikidata ---
# used in place of the official WDQS, which is prone to outages and aggressive
# rate limits. QLever needs explicit PREFIX declarations and plain rdfs:label.
ENDPOINT = "https://qlever.cs.uni-freiburg.de/api/wikidata"
TERMS = ("QLever public SPARQL over Wikidata (+ images via Wikimedia Commons) --- "
         "free, queried in single bulk pulls.")

_QUERY = """
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX psv: <http://www.wikidata.org/prop/statement/value/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
SELECT ?item ?itemLabel ?itemDescription ?lat ?lon ?inception ?demolished ?image ?article WHERE {
  ?item wdt:P576 ?demolished.
  ?item wdt:P18 ?image.
  ?item wdt:P31/wdt:P279* wd:Q811979 .
  ?item p:P625 ?cs. ?cs psv:P625 ?cn.
  ?cn wikibase:geoLatitude ?lat; wikibase:geoLongitude ?lon.
  ?item rdfs:label ?itemLabel. FILTER(LANG(?itemLabel)="en")
  OPTIONAL { ?item wdt:P571 ?inception. }
  OPTIONAL { ?item schema:description ?itemDescription. FILTER(LANG(?itemDescription)="en") }
  OPTIONAL { ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }
}
ORDER BY ?item
LIMIT %d OFFSET %d
"""

# Ancient figures whose words survive only quoted in others' books --- discovered
# generically (everyone who died before ~100 AD and has a Wikiquote page), never
# a hand-written list. Their death date gives the era, with no hardcoded years.
_PEOPLE_QUERY = """
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX schema: <http://schema.org/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?item ?name ?death WHERE {
  ?item wdt:P570 ?death. FILTER(YEAR(?death) < 100)
  ?wq schema:about ?item; schema:isPartOf <https://en.wikiquote.org/>.
  ?item rdfs:label ?name. FILTER(LANG(?name)="en")
}
ORDER BY DESC(?death)
LIMIT %d OFFSET %d
"""

# THE GHOST LIBRARY: works humanity knows existed and no longer has --- lost
# books, poems, operas, paintings. The holes in the record, catalogued by
# Wikidata as 'lost work' (Q21752591). Each is a ghost to be rebuilt from the
# surviving traces: its description, its author, and what others wrote about it.
_LOST_WORKS_QUERY = """
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX schema: <http://schema.org/>
SELECT ?item ?name ?authorName ?authorDeath ?desc ?inception ?article ?image WHERE {
  ?item wdt:P31/wdt:P279* wd:Q21752591 .
  ?item rdfs:label ?name. FILTER(LANG(?name)="en")
  OPTIONAL { ?item wdt:P50 ?a. ?a rdfs:label ?authorName. FILTER(LANG(?authorName)="en")
             OPTIONAL { ?a wdt:P570 ?authorDeath. } }
  OPTIONAL { ?item schema:description ?desc. FILTER(LANG(?desc)="en") }
  OPTIONAL { ?item wdt:P571 ?inception. }
  OPTIONAL { ?article schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>. }
  OPTIONAL { ?item wdt:P18 ?image. }
}
ORDER BY ?item
LIMIT %d OFFSET %d
"""


@dataclass
class LostWork:
    qid: str
    name: str
    author: str | None
    author_death: str | None
    description: str | None
    inception: str | None
    article: str | None          # the surviving Wikipedia account of the lost thing
    image: str | None            # a surviving depiction/derivative, if any


def lost_works(limit: int = 60, offset: int = 0, *, log=print) -> list[LostWork]:
    """A page of the things humanity knows it lost --- the Ghost Library."""
    data = _run(_LOST_WORKS_QUERY % (limit, offset), log=log)
    if not data:
        return []
    out, seen = [], set()
    for r in data.get("results", {}).get("bindings", []):
        qid = r.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        name = r.get("name", {}).get("value")
        if not qid or not name or qid in seen:
            continue
        seen.add(qid)
        out.append(LostWork(
            qid=qid, name=name,
            author=r.get("authorName", {}).get("value"),
            author_death=r.get("authorDeath", {}).get("value"),
            description=r.get("desc", {}).get("value"),
            inception=r.get("inception", {}).get("value"),
            article=r.get("article", {}).get("value"),
            image=r.get("image", {}).get("value"),
        ))
    return out


@dataclass
class Place:
    qid: str
    label: str
    description: str | None
    lat: float
    lon: float
    inception: str | None     # ISO
    demolished: str           # ISO
    image: str                # a real surviving photograph (Commons)
    article: str | None


def _year(iso: str | None) -> float | None:
    if not iso:
        return None
    neg = iso.startswith("-")
    s = iso.lstrip("-")
    try:
        y = int(s.split("-")[0])
        return -y if neg else y
    except ValueError:
        return None


def _run(query: str, *, patience: int = 2, log=print) -> dict | None:
    """Run one SPARQL query against QLever, patient with transient failures."""
    url = f"{ENDPOINT}?" + urlencode({"query": query})
    req = Request(url, headers={"User-Agent": USER_AGENT,
                                "Accept": "application/sparql-results+json"})
    for attempt in range(patience + 1):
        try:
            with urlopen(req, timeout=75) as resp:
                return json.loads(resp.read())
        except Exception as e:
            if attempt < patience:
                log(f"  query retry ({type(e).__name__}); waiting 10s")
                time.sleep(10)
                continue
            log(f"  SPARQL query failed: {type(e).__name__} {e}")
            return None
    return None


def ancient_quoted_people(limit: int = 20, offset: int = 0, *, log=print) -> list[tuple[str, float]]:
    """Generically discovered ancient figures (name, era-year) whose words survive
    only as quotations. Paginates through all of them --- no hand-written list."""
    data = _run(_PEOPLE_QUERY % (limit, offset), log=log)
    if not data:
        return []
    out, seen = [], set()
    for r in data.get("results", {}).get("bindings", []):
        name = r.get("name", {}).get("value")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append((name, _year(r.get("death", {}).get("value")) or -500))
    return out


def demolished_structures(limit: int = 400, offset: int = 0, *, log=print) -> list[Place]:
    """A page of lost structures, pulled generically. offset paginates through
    every demolished building Wikidata knows --- the whole class, not a subset."""
    data = _run(_QUERY % (limit, offset), log=log)
    if not data:
        return []
    out: list[Place] = []
    seen: set[str] = set()
    for r in data.get("results", {}).get("bindings", []):
        qid = r.get("item", {}).get("value", "").rsplit("/", 1)[-1]
        if not qid or qid in seen:
            continue
        try:
            lat = float(r["lat"]["value"]); lon = float(r["lon"]["value"])
        except (KeyError, ValueError):
            continue
        seen.add(qid)
        label = r.get("itemLabel", {}).get("value", qid)
        if label == qid:        # unlabeled items are noise
            continue
        out.append(Place(
            qid=qid, label=label,
            description=r.get("itemDescription", {}).get("value"),
            lat=lat, lon=lon,
            inception=r.get("inception", {}).get("value"),
            demolished=r.get("demolished", {}).get("value", ""),
            image=r.get("image", {}).get("value", ""),
            article=r.get("article", {}).get("value"),
        ))
    return out
