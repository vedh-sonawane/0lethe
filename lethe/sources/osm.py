"""OpenStreetMap: the real world, surveyed.

The planet is already mapped --- real building footprints, real heights, real
streets and water, for essentially everywhere. For any lost thing that had a
place, this reads the actual geometry of that exact spot and hands back a compact,
real 3D model of it: the ground truth to stand a world on. Not invented shapes ---
the world's own survey.

Uses the free OSM map API (XML, no key). One bounded bbox per place, cached.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

from ..http_util import fetch

API = "https://api.openstreetmap.org/api/0.6/map"
TERMS = ("OpenStreetMap map data via the free OSM API (ODbL) --- one bounded "
         "area per place, read politely and cached.")

# metres per degree, near enough at city scale
_M_LAT = 110540.0
def _m_lon(lat): return 111320.0 * math.cos(math.radians(lat))


def _height(tags: dict) -> float:
    h = tags.get("height") or tags.get("building:height")
    if h:
        try:
            return max(2.0, float(str(h).split()[0].replace(",", ".")))
        except ValueError:
            pass
    lv = tags.get("building:levels") or tags.get("levels")
    if lv:
        try:
            return max(2.5, float(str(lv).split(";")[0]) * 3.2 + 1.0)
        except ValueError:
            pass
    return 0.0          # unknown -> let the renderer pick a modest default


def world_at(lat0: float, lon0: float, qid: str | None = None, *,
             radius_m: float = 320.0, max_buildings: int = 600, log=print) -> dict | None:
    """The real 3D world around a point: every mapped building as a footprint in
    local metres (east = +x, north = -z) with its real height, plus water and the
    road network. The building tagged with `qid` is flagged as the lost thing
    itself. Returns None if the area can't be read."""
    dlat = radius_m / _M_LAT
    dlon = radius_m / _m_lon(lat0)
    bbox = f"{lon0 - dlon},{lat0 - dlat},{lon0 + dlon},{lat0 + dlat}"
    res = fetch(f"{API}?{urlencode({'bbox': bbox})}", respect_robots=False, timeout=40,
                max_bytes=12_000_000)
    if not res.ok:
        log(f"  OSM unreachable for {lat0:.4f},{lon0:.4f}: {res.status or res.error}")
        return None
    try:
        root = ET.fromstring(res.body)
    except Exception as e:
        log(f"  OSM parse failed: {type(e).__name__}")
        return None

    mlon = _m_lon(lat0)
    nodes: dict[str, tuple[float, float]] = {}
    for n in root.iter("node"):
        nid = n.get("id")
        la, lo = n.get("lat"), n.get("lon")
        if nid and la and lo:
            x = (float(lo) - lon0) * mlon
            z = -(float(la) - lat0) * _M_LAT          # north is -z
            nodes[nid] = (round(x, 1), round(z, 1))

    buildings, water, roads = [], [], []
    for w in root.iter("way"):
        refs = [nd.get("ref") for nd in w.findall("nd")]
        pts = [nodes[r] for r in refs if r in nodes]
        if len(pts) < 2:
            continue
        tags = {t.get("k"): t.get("v") for t in w.findall("tag")}
        if "building" in tags or "building:part" in tags:
            if len(pts) < 3:
                continue
            mine = bool(qid) and tags.get("wikidata") == qid
            buildings.append({"p": pts[:64], "h": round(_height(tags), 1), "me": mine})
        elif tags.get("natural") == "water" or "water" in tags or tags.get("waterway"):
            water.append(pts[:80])
        elif "highway" in tags:
            roads.append(pts[:80])

    if not buildings and not roads:
        log(f"  OSM returned no structures at {lat0:.4f},{lon0:.4f}")
        return None
    buildings.sort(key=lambda b: -b["h"])
    buildings = buildings[:max_buildings]
    log(f"  OSM world: {len(buildings)} buildings, {len(roads)} roads, {len(water)} water")
    return {"center": [lat0, lon0], "radius": radius_m,
            "buildings": buildings, "roads": roads[:400], "water": water[:200]}
