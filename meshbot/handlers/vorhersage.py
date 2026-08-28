"""!vorhersage — weather for the coming hours from the GeoSphere model.

Dataset `nwp-v1-1h-2500m`, hourly, point-accurate via coordinates. What is
returned is the range until tomorrow morning rather than an hourly series: in
140 characters, a temperature range, a precipitation total and a peak gust are
the most information that still stays comprehensible.
"""

from __future__ import annotations

from typing import Any

import httpx

PARAMS = "t2m,rr_acc,ugust"


async def fetch(client: httpx.AsyncClient, url: str, lat: float, lon: float,
                stunden: int = 24) -> dict[str, Any]:
    resp = await client.get(url, params={"parameters": PARAMS, "lat_lon": f"{lat},{lon}",
                                         "output_format": "geojson"})
    resp.raise_for_status()
    daten = resp.json()
    p = daten["features"][0]["properties"]["parameters"]

    def reihe(name: str) -> list[float]:
        werte = (p.get(name) or {}).get("data") or []
        return [w for w in werte[:stunden] if w is not None]

    temp = reihe("t2m")
    regen = reihe("rr_acc")
    boe = [abs(w) for w in reihe("ugust")]
    return {
        "tmin": min(temp) if temp else None,
        "tmax": max(temp) if temp else None,
        # rr_acc is cumulative: the difference between end and start is the gain.
        "regen": (max(regen) - min(regen)) if regen else None,
        "boe": max(boe) if boe else None,
        "stunden": stunden,
    }


def render(ort: str, w: dict[str, Any], stale: bool = False) -> str:
    marker = "~" if stale else ""
    teile = [f"{w['stunden']}h {ort.title()}: {marker}"]
    if w.get("tmin") is not None:
        teile.append(f"{w['tmin']:.0f} bis {w['tmax']:.0f}C")
    if w.get("regen") is not None:
        teile.append("kein Regen" if w["regen"] < 0.2 else f"{w['regen']:.0f}mm Regen")
    if w.get("boe") is not None:
        teile.append(f"Boeen {w['boe']*3.6:.0f}km/h")
    return teile[0] + ", ".join(teile[1:])
