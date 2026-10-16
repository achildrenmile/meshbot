import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from meshbot.handlers.wissen import Wissen


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def datei(tmp_path, eintraege):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"eintraege": eintraege}), encoding="utf-8")
    return Wissen(p)


E = {"thema": "Repeater benennen", "stichworte": ["repeater", "name"], "text": "AT-BEZIRK-NAME", "quelle": "meshcore.at"}


def test_treffer_mit_quelle(tmp_path):
    w = datei(tmp_path, [E])
    assert run(w.handler("repeater benennen", "x")) == "AT-BEZIRK-NAME (meshcore.at)"


def test_kein_treffer(tmp_path):
    assert run(datei(tmp_path, [E]).handler("wetter", "x")) is None


def test_fehlende_datei_ist_leer(tmp_path):
    assert run(Wissen(tmp_path / "nein.json").handler("repeater", "x")) is None


def test_kaputte_datei_behaelt_letzten_stand(tmp_path):
    w = datei(tmp_path, [E])
    assert w.aktuell()
    p = w.pfad
    p.write_text("{kaputt", encoding="utf-8")
    os.utime(p, (1, 1))
    assert w.aktuell()


def test_wortformen_und_eszett(tmp_path):
    e = dict(E, stichworte=["repeater", "benennen", "nennen", "heissen"])
    w = datei(tmp_path, [e])
    for frage in ["Wie benenne ich meinen Repeater?", "Benennung für Repeater",
                  "Wie nenne ich meinen Repeater?", "Wie soll der Repeater heißen?"]:
        assert w.suche(frage), frage
    assert not w.suche("Wie ist das Wetter?")
    assert not w.suche("Repeater")
