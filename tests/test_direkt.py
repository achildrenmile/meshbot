"""Direktnachrichten — der Weg, der das Netz nicht flutet.

Eine Kanalantwort flutet: jeder der 35 Kärntner Repeater sendet sie einmal aus.
Eine Direktnachricht über einen bekannten Pfad belastet nur die Repeater dieser
Kette. Ein Austausch kostet damit rund 70 Aussendungen statt rund 8.

Was hier **nicht** geht und deshalb auch nicht getestet wird: auf dem Kanal
fragen und per DM antworten. Eine Kanalnachricht trägt keinerlei
Absenderkennung — nur einen Namen im Text, und der ist weder ein Schlüssel noch
vertrauenswürdig. Der Bot kann eine DM beantworten, aber keine beginnen.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from meshbot.config import Settings  # noqa: E402
from meshbot.main import Bot  # noqa: E402
from meshbot.router import parse_payload  # noqa: E402

PUBKEY = "a1b2c3d4e5f6"          # 6 Bytes als 12 Hex-Zeichen, wie vom Node geliefert


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def settings() -> Settings:
    return Settings(mqtt_host="test", bot_name="MeshBot", max_msg_len=124,
                    dm_enabled=True,
                    json_path_text="payload.text", json_path_sender="payload.sender",
                    json_path_channel="payload.channel_idx")


def dm(text: str, key: str = PUBKEY) -> str:
    """Nutzlast einer empfangenen Direktnachricht, wie die Brücke sie baut."""
    return json.dumps({
        "type": "EventType.CONTACT_MSG_RECV",
        "payload": {"type": "PRIV", "pubkey_prefix": key, "path_len": 2,
                    "txt_type": 0, "sender_timestamp": 1756500000, "text": text},
    })


def kanal(text: str, sender: str = "OE8TEST") -> str:
    return json.dumps({
        "type": "EventType.CHANNEL_MSG_RECV",
        "payload": {"type": "CHAN", "channel_idx": 3, "path_len": 2,
                    "txt_type": 0, "sender_timestamp": 1756500000,
                    "text": f"{sender}: {text}"},
    })


# --- Erkennen ---------------------------------------------------------------


def test_dm_wird_erkannt_und_der_schluessel_ist_der_absender(settings):
    """Der stille Gewinn gegenüber dem Kanal.

    Auf dem Kanal ist der Absendername freier Text: wer das Absenderlimit
    umgehen will, benennt sein Gerät um. Ein Pubkey-Präfix lässt sich nicht
    umbenennen.
    """
    e = parse_payload(dm("!ping"), settings)
    assert e.direkt == PUBKEY
    assert e.sender == PUBKEY
    assert e.text == "!ping"
    assert e.channel is None


def test_kanalnachricht_bleibt_wie_bisher(settings):
    e = parse_payload(kanal("!ping"), settings)
    assert e.direkt is None
    assert e.sender == "OE8TEST"
    assert e.text == "!ping"


def test_doppelpunkt_im_dm_text_bleibt_stehen(settings):
    """In einer DM steht kein Name vor dem Text.

    `split_sender_prefix` würde an einem beliebigen ": " trennen und den Anfang
    der Frage verschlucken — aus `!frag was heisst das: ein test` würde
    `ein test`.
    """
    e = parse_payload(dm("!frag was heisst das: ein test"), settings)
    assert e.text == "!frag was heisst das: ein test"


def test_ohne_dm_betrieb_gilt_die_nutzlast_als_kanalnachricht(settings):
    """Der Schalter wirkt auch dann, wenn doch eine DM hereinkäme."""
    aus = settings.model_copy(update={"dm_enabled": False})
    e = parse_payload(dm("!ping"), aus)
    assert e.direkt is None


def test_kanalfilter_blockiert_keine_dm(settings):
    """Eine DM hat keinen Kanal — sie darf daran nicht scheitern."""
    mit_filter = settings.model_copy(update={"channel_filter": "3"})
    b = Bot(mit_filter)
    assert run(b.router.handle(dm("!ping"))) is not None


# --- Antwortweg -------------------------------------------------------------


def _gesendet(b: Bot) -> list[tuple[str, str]]:
    raus: list[tuple[str, str]] = []
    b.mqtt.publish = lambda topic, payload: raus.append((topic, payload))
    return raus


def test_antwort_auf_eine_dm_geht_direkt_zurueck(settings):
    b = Bot(settings)
    raus = _gesendet(b)
    run(b.on_message(dm("!ping").encode()))

    topic, payload = raus[0]
    assert topic == settings.topic_tx_direct
    daten = json.loads(payload)
    assert daten["destination"] == PUBKEY
    assert daten["message"].startswith("MeshBot OK")
    assert "channel" not in daten


def test_antwort_auf_dem_kanal_bleibt_auf_dem_kanal(settings):
    b = Bot(settings)
    raus = _gesendet(b)
    run(b.on_message(kanal("!ping").encode()))

    topic, payload = raus[0]
    assert topic == settings.topic_tx
    daten = json.loads(payload)
    assert daten["channel"] == settings.tx_channel
    assert "destination" not in daten


def test_anfuehrungszeichen_werden_auch_im_dm_ersetzt(settings):
    """Sonst zerlegt eine Antwort mit " die JSON-Vorlage."""
    b = Bot(settings)
    raus = _gesendet(b)

    async def echo(arg, sender):
        return 'Er sagte "hallo"'

    b.router.handlers["ping"] = echo
    run(b.on_message(dm("!ping").encode()))
    assert json.loads(raus[0][1])["message"] == "Er sagte 'hallo'"


# --- Bremsen ----------------------------------------------------------------


def test_absenderlimit_greift_pro_schluessel(settings):
    """Das Limit haengt am Schluessel, und zwei Geraete teilen es sich nicht."""
    b = Bot(settings)
    einer, anderer = PUBKEY, "ffeeddccbbaa"

    # Verschiedene Texte, sonst greift die Duplikaterkennung statt des Limits.
    for i in range(settings.sender_limit):
        assert run(b.router.handle(dm(f"!qth JN76h{i}", einer))) is not None, f"Runde {i}"
    # Eine mehr als erlaubt: Stille.
    assert run(b.router.handle(dm("!qth JN76hz", einer))) is None
    # Ein anderes Geraet kommt weiterhin durch.
    assert run(b.router.handle(dm("!ping", anderer))) is not None
