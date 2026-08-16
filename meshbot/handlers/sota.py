"""!sota — Gipfel-Nachschlag über die SOTA-API v2."""

from __future__ import annotations

import re
from typing import Any

import httpx

REF = re.compile(r"^\s*(?:([A-Z0-9]{1,3}(?:/[A-Z0-9]{1,3})?)[/\s-]*)?([A-Z]{2})[\s-]?(\d{1,3})\s*$", re.I)


def normalise(arg: str, default_assoc: str) -> str | None:
    """`oe/kt-048`, `kt048`, `KT-48` → `OE/KT-048`."""
    m = REF.match(arg.replace("_", "-"))
    if not m:
        return None
    assoc, region, num = m.groups()
    if assoc and "/" in assoc:
        praefix = assoc.upper()
    elif assoc:
        praefix = f"{assoc.upper()}/{region.upper()}"
        return f"{praefix}-{int(num):03d}"
    else:
        praefix = default_assoc.upper().split("/")[0] + "/" + region.upper()
    if not praefix.endswith(region.upper()):
        praefix = f"{praefix.split('/')[0]}/{region.upper()}"
    return f"{praefix}-{int(num):03d}"


async def fetch(client: httpx.AsyncClient, base_url: str, ref: str) -> dict[str, Any] | None:
    assoc, code = ref.split("/", 1)[0], ref.split("/", 1)[1]
    resp = await client.get(f"{base_url}/{assoc}/{code}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    return data or None


def render(ref: str, gipfel: dict[str, Any] | None, stale: bool = False) -> str:
    if not gipfel:
        return f"SOTA: {ref} nicht gefunden"
    marker = "~" if stale else ""
    name = gipfel.get("name") or gipfel.get("summitName") or "?"
    hoehe = gipfel.get("altM") or gipfel.get("altitudeM")
    punkte = gipfel.get("points")
    akt = gipfel.get("activationCount", gipfel.get("activations"))
    teile = [f"{ref} {marker}{name}"]
    if hoehe:
        teile.append(f"{int(hoehe)}m")
    if punkte:
        teile.append(f"{int(punkte)}Pkt")
    if akt is not None:
        teile.append(f"Akt: {int(akt)}")
    return teile[0] + " " + ", ".join(teile[1:])
