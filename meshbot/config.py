"""Konfiguration. Alles ueber Umgebungsvariablen, nichts einkompiliert.

Die MQTT-Topics und das Nachrichtenformat sind bewusst konfigurierbar: Die Bruecke
ins Mesh gibt das Format vor, nicht dieser Dienst.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Broker ---
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_pass: str = ""
    mqtt_tls: bool = False
    mqtt_client_id: str = "meshbot"

    # --- Topics. Vorgabe passt zum meshinfra-Stack. ---
    topic_rx: str = "meshinfra/message/channel/3"
    topic_tx: str = "meshinfra/tx/chan"
    topic_admin: str = "meshinfra/bot/admin"

    # --- Nutzlastformat ---
    payload_format: str = "json"          # json | text
    json_path_text: str = "text"
    json_path_sender: str = "sender"
    json_path_channel: str = "channel_idx"
    tx_template: str = '{{"channel": {channel}, "message": "{text}"}}'
    tx_channel: int = 3

    # --- Betrieb ---
    bot_enabled: bool = True
    bot_name: str = "MeshBot"
    channel_filter: str = ""              # leer = kein Filter
    max_msg_len: int = 140
    hard_msg_len: int = 150
    transliterate: bool = True
    default_location: str = "villach"
    sota_default_assoc: str = "OE/KT"

    # --- Airtime-Bremsen ---
    global_limit: int = 6                 # Antworten
    global_window_s: int = 600            # je 10 Minuten
    sender_limit: int = 2                 # Befehle
    sender_window_s: int = 300            # je 5 Minuten
    dedup_window_s: int = 60

    # --- Aussenwelt ---
    http_timeout_s: float = 5.0
    http_retries: int = 1
    cache_ttl_wx_s: int = 600
    cache_ttl_uwz_s: int = 300
    cache_ttl_sota_s: int = 86400
    cache_ttl_relais_s: int = 86400

    geosphere_tawes_url: str = (
        "https://dataset.api.hub.geosphere.at/v1/station/current/tawes-v1-10min"
    )
    warn_url: str = "https://warnungen.zamg.at/wsapp/api/getWarningsForCoords"
    sota_url: str = "https://api2.sota.org.uk/api/summits"

    health_port: int = 8080
    log_level: str = "INFO"

    stations_file: Path = Field(default=DATA_DIR / "stations_ktn.json")
    relais_file: Path = Field(default=DATA_DIR / "relais_oe.json")
    summits_file: Path = Field(default=DATA_DIR / "sota_summits.json")


def load_settings() -> Settings:
    return Settings()
