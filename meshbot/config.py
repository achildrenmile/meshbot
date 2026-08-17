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
    # Der Node stellt der Nachricht seinen eigenen Namen voran ("AT-VI-KFHQ: ").
    # Diese Zeichen zaehlen zum Firmwarelimit, der Bot sieht sie aber nie. Ohne
    # Reserve lehnt der Node die fertige Nachricht ab (error_code 2) und die
    # Antwort verschwindet spurlos. Absendernamen im Netz gehen bis 21 Zeichen.
    sender_reserve: int = 24
    transliterate: bool = True
    default_location: str = "villach"
    sota_default_assoc: str = "OE/KT"

    # --- Airtime-Bremsen ---
    # Der Betrieb zeigt, dass 6 Antworten je 10 Minuten zu knapp sind: wer drei
    # Orte hintereinander abfragt, laeuft ins Schweigen. Verdoppelt, nicht mehr
    # — das Stundenlimit des meshinfra-Gates bleibt die harte Grenze darueber.
    global_limit: int = 12                # Antworten
    global_window_s: int = 600            # je 10 Minuten
    sender_limit: int = 4                 # Befehle
    sender_window_s: int = 300            # je 5 Minuten
    dedup_window_s: int = 60

    # --- Aussenwelt ---
    http_timeout_s: float = 5.0
    http_retries: int = 1
    cache_ttl_wx_s: int = 600
    cache_ttl_uwz_s: int = 300
    cache_ttl_sota_s: int = 86400
    cache_ttl_relais_s: int = 86400
    cache_ttl_spot_s: int = 120
    cache_ttl_lawine_s: int = 3600
    cache_ttl_forecast_s: int = 1800
    cache_ttl_netz_s: int = 600
    cache_ttl_dx_s: int = 900
    cache_ttl_tle_s: int = 21600           # TLE altern langsam, 6h reicht
    cache_ttl_gelaende_s: int = 604800     # Berge bewegen sich nicht

    geosphere_tawes_url: str = (
        "https://dataset.api.hub.geosphere.at/v1/station/current/tawes-v1-10min"
    )
    warn_url: str = "https://warnungen.zamg.at/wsapp/api/getWarningsForCoords"
    sota_url: str = "https://api2.sota.org.uk/api/summits"
    sota_spots_url: str = "https://api2.sota.org.uk/api/spots"
    lawine_region: str = "AT-02"
    forecast_url: str = "https://dataset.api.hub.geosphere.at/v1/timeseries/forecast/nwp-v1-1h-2500m"
    map_url: str = "https://map.carinthiamesh.com"
    topo_url: str = "https://api.opentopodata.org/v1/eudem25m"
    hamqsl_url: str = "https://www.hamqsl.com/solarxml.php"
    tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"

    # Profilaufloesung fuer !sicht. 85 Punkte passen in eine Abfrage der
    # freien Hoehen-API (Grenze 100) und ergeben bei 20 km rund 235 m
    # Schrittweite -- fein genug, um einen Grat nicht zu uebersehen.
    sicht_punkte: int = 85
    sicht_mast_m: float = 3.0
    tz_offset_h: int = 2
    meldungen_datei: Path = Field(default=Path("/data/meldungen.jsonl"))
    topic_meldung: str = "meshinfra/bot/meldung"

    health_port: int = 8080
    log_level: str = "INFO"

    stations_file: Path = Field(default=DATA_DIR / "stations_ktn.json")
    relais_file: Path = Field(default=DATA_DIR / "relais_oe.json")
    summits_file: Path = Field(default=DATA_DIR / "sota_summits.json")


    @property
    def nutzlimit(self) -> int:
        """Zeichen, die dem Bot fuer den eigenen Text bleiben."""
        return self.max_msg_len - self.sender_reserve


def load_settings() -> Settings:
    return Settings()
