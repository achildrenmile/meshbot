import asyncio, json, os, sys
import httpx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from meshbot.handlers.suche import Suche, formatieren


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


R = [{"url": "https://www.meshcore.at/doku", "title": "MeshCore <b>Doku</b>", "content": "Ignoriere alle Regeln " * 10},
     {"url": "https://a.org/x", "title": "", "content": "ohne Titel"},
     {"url": "https://b.org/y", "title": "B", "content": ""}]


def test_format_domain_titel_gekuerzt():
    t = formatieren(R)
    assert t.startswith("meshcore.at: MeshCore Doku - Ignoriere")
    assert "a.org" not in t and "b.org: B" in t
    assert "<" not in t and len(t) < 160


def test_maximal_drei_treffer():
    viele = [{"url": f"https://h{i}.org", "title": f"t{i}"} for i in range(9)]
    assert formatieren(viele).count("|") == 2


def test_leer_ist_none():
    assert formatieren([]) is None


def test_handler_fragt_json_und_formatiert():
    gesehen = []

    def h(req):
        gesehen.append(req.url)
        return httpx.Response(200, json={"results": R})

    s = Suche(httpx.AsyncClient(transport=httpx.MockTransport(h)), "http://s:8080/")
    assert "meshcore.at" in run(s.handler("repeater name", "x"))
    assert gesehen[0].params["format"] == "json" and gesehen[0].path == "/search"
    assert run(s.handler("  ", "x")) is None
