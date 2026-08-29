"""Configuration. Everything via environment variables, nothing compiled in.

MQTT topics and the message format are configurable on purpose: the bridge into
the mesh dictates the format, not this service.
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

    # --- Topics. Defaults match the meshinfra stack. ---
    topic_rx: str = "meshinfra/message/channel/3"
    topic_tx: str = "meshinfra/tx/chan"
    topic_admin: str = "meshinfra/bot/admin"
    # The gate publishes its quota state retained, so subscribing delivers the
    # last value immediately, without asking.
    topic_quota: str = "meshinfra/gate/quota"

    # --- Payload format ---
    payload_format: str = "json"          # json | text
    json_path_text: str = "text"
    json_path_sender: str = "sender"
    json_path_channel: str = "channel_idx"
    tx_template: str = '{{"channel": {channel}, "message": "{text}"}}'
    tx_channel: int = 3

    # --- Operation ---
    bot_enabled: bool = True
    bot_name: str = "MeshBot"
    channel_filter: str = ""              # empty = no filter
    max_msg_len: int = 140
    hard_msg_len: int = 150
    # The node prefixes the message with its own name ("AT-VI-KFHQ: "). Those
    # characters count towards the firmware limit, but the bot never sees them.
    # Without a reserve the node rejects the finished message (error_code 2) and
    # the answer vanishes without a trace. Sender names on the network run to 21
    # characters.
    sender_reserve: int = 24
    # Umlauts may go on the air. The displays on the network handle them, and
    # "Nötsch" is the name of the place -- "Noetsch" was a stopgap from the time
    # when that was not settled.
    #
    # What it costs: in UTF-8 an umlaut is two bytes, an ASCII character one.
    # The usable limit counts characters, the air counts bytes -- an answer with
    # three umlauts is three bytes longer than the character count suggests. At
    # the measured threshold (up to 59 characters 92 % arrive, from 60 only
    # 47 %) that is worth knowing, but no reason to mangle place names.
    #
    # SOTA association codes are untouched by this: "OE" is a code, not a
    # mistreated "Ö", and is never translated back.
    transliterate: bool = False
    default_location: str = "villach"
    sota_default_assoc: str = "OE/KT"

    # --- Airtime brakes ---
    # Operation showed that 6 answers per 10 minutes is too tight: querying
    # three places in a row runs into silence. Doubled, no more than that — the
    # meshinfra gate's hourly limit stays the hard ceiling above it.
    global_limit: int = 12                # answers
    global_window_s: int = 600            # per 10 minutes
    sender_limit: int = 4                 # commands
    sender_window_s: int = 300            # per 5 minutes
    dedup_window_s: int = 60

    # --- Outside world ---
    http_timeout_s: float = 5.0
    http_retries: int = 1
    cache_ttl_wx_s: int = 600
    cache_ttl_warn_s: int = 300
    cache_ttl_sota_s: int = 86400
    cache_ttl_relais_s: int = 86400
    cache_ttl_spot_s: int = 120
    cache_ttl_lawine_s: int = 3600
    cache_ttl_forecast_s: int = 1800
    cache_ttl_netz_s: int = 600
    cache_ttl_dx_s: int = 900
    cache_ttl_tle_s: int = 21600           # TLEs age slowly, 6h is enough
    cache_ttl_gelaende_s: int = 604800     # mountains do not move
    cache_ttl_geo_s: int = 604800          # neither do places
    cache_ttl_az_s: int = 2592000          # SOTLAS zone polygons, 30 days

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
    # Summit weather: model values for mountains where no station stands and
    # where GeoSphere ends anyway -- that is, outside Austria.
    berg_url: str = "https://api.open-meteo.com/v1/forecast"
    # Place lookup for everything outside the 34 Carinthian stations. Same
    # source as the summit weather, so an outage takes out one and not two.
    geocode_url: str = "https://geocoding-api.open-meteo.com/v1/search"
    hamqsl_url: str = "https://www.hamqsl.com/solarxml.php"
    tle_url: str = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE"

    # Profile resolution for !sicht. 85 points fit into one request of the free
    # elevation API (limit 100) and give roughly 235 m of step size over 20 km
    # -- fine enough not to miss a ridge.
    sicht_punkte: int = 85
    sicht_mast_m: float = 3.0
    tz_offset_h: int = 2
    meldungen_datei: Path = Field(default=Path("/data/meldungen.jsonl"))
    topic_meldung: str = "meshinfra/bot/meldung"

    # --- !frag: language model, local ---
    # Off by default. The first deployment must be able to change nothing, so
    # that a failure afterwards can only come from the switch being flipped.
    frag_enabled: bool = False
    # rag-node-01. Same LAN, no tunnel, no authentication -- whoever reaches the
    # port has the model, which is why it stays inside 192.168.1.0/24.
    ollama_url: str = "http://192.168.1.32:30434/api/chat"
    # Measured on rag-node-01 against the production system prompt, five
    # questions each: gemma3:4b kept to the character budget five times out of
    # five at ~1.5 s per answer, and deflected both fact questions to the
    # measured commands instead of inventing a number. gemma2:2b managed three
    # of five, llama3.2:3b one of three, llama3.1:8b ran at half the speed and
    # still invented, and gpt-oss:20b is killed by the OOM killer there.
    #
    # qwen3:4b is unusable and worth naming: it ignores `think: false` and
    # writes its reasoning into the answer as plain prose ("Okay, I need to
    # answer the question..."), 320+ characters every time. There are no
    # <think> tags, so DENKBLOCK in the handler cannot strip it either.
    frag_model: str = "gemma3:4b"
    # Its own timeout: http_timeout_s (5s) is sized for web APIs, and CPU
    # inference of eighty tokens takes longer than that.
    frag_timeout_s: float = 20.0
    # Cost ceiling in both senses -- length of the answer and seconds of CPU.
    frag_num_predict: int = 80
    frag_frage_max: int = 160
    # Brakes of its own, on top of the ones every command passes. An AI answer
    # costs a multiple of a weather lookup, so it gets a stricter allowance.
    frag_tageslimit: int = 100
    frag_sender_limit: int = 2
    frag_sender_window_s: int = 900
    cache_ttl_frag_s: int = 3600
    # How often to ask the inference host whether it is alive. Without this a
    # dead Ollama is invisible: !frag simply stays silent, and silence looks
    # exactly like "nobody asked". Five minutes is often enough to notice and
    # rare enough to cost nothing -- the probe loads no model.
    frag_probe_s: float = 300.0
    # Marks the answer as coming from a model rather than from a measurement.
    # Four characters off the budget, deliberately spent.
    frag_praefix: str = "KI:"

    health_port: int = 8080
    # How long the MQTT connection may be gone before the health check reports
    # unhealthy. A reconnect takes seconds; two minutes distinguishes that from
    # a broker that is actually down. Docker adds its own margin on top
    # (interval 60s, retries 3).
    health_mqtt_grace_s: float = 120.0
    log_level: str = "INFO"

    stations_file: Path = Field(default=DATA_DIR / "stations_ktn.json")
    relais_file: Path = Field(default=DATA_DIR / "relais_oe.json")
    summits_file: Path = Field(default=DATA_DIR / "sota_summits.json")


    @property
    def nutzlimit(self) -> int:
        """Characters left to the bot for its own text."""
        return self.max_msg_len - self.sender_reserve


def load_settings() -> Settings:
    return Settings()
