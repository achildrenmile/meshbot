import asyncio

import httpx
import pytest

from meshbot.handlers import dx


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

XML = """<?xml version="1.0"?><solar><solardata>
<solarflux>117</solarflux><aindex> 6</aindex><kindex> 0</kindex>
<kindexnt>No Report</kindexnt><xray>C1.3</xray><sunspots>83</sunspots>
</solardata></solar>"""


def test_felder_werden_gelesen():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=XML)))
    werte = run(dx.fetch(client, "https://x"))
    assert werte["solarflux"] == "117" and werte["kindex"] == "0" and werte["xray"] == "C1.3"
    assert dx.render(werte) == "DX: SFI 117, A6, K0, SN 83, Xray C1.3"


def test_leeres_dokument_wirft():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<solar/>")))
    with pytest.raises(ValueError):
        run(dx.fetch(client, "https://x"))


def test_teildokument_liefert_was_da_ist():
    """Ein fehlendes Feld darf nicht die ganze Antwort kosten."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, text="<solar><solarflux>90</solarflux></solar>")))
    assert dx.render(run(dx.fetch(client, "https://x"))) == "DX: SFI 90"


def test_sturm_wird_benannt():
    assert "STURM" in dx.render({"kindex": "6"})
    assert "unruhig" in dx.render({"kindex": "4"})
    assert "STURM" not in dx.render({"kindex": "2"})
    assert dx.render({"kindex": "kaputt"}) == "DX: Kkaputt"
