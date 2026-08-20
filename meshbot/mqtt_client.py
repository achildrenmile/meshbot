"""MQTT-Anbindung mit automatischem Wiederverbinden.

paho läuft in einem eigenen Thread, der Bot im Eventloop — deshalb geht jede
eingehende Nachricht über `run_coroutine_threadsafe` zurück in den Loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import paho.mqtt.client as mqtt
import structlog

from .config import Settings

log = structlog.get_logger(__name__)


class MqttClient:
    def __init__(self, settings: Settings,
                 on_message: Callable[[bytes], Awaitable[None]],
                 on_admin: Callable[[bytes], None],
                 on_quota: Callable[[bytes], None] | None = None) -> None:
        self.settings = settings
        self._on_message = on_message
        self._on_admin = on_admin
        self._on_quota = on_quota
        self._loop: asyncio.AbstractEventLoop | None = None
        self.connected = False

        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                   client_id=settings.mqtt_client_id, clean_session=True)
        if settings.mqtt_user:
            self._client.username_pw_set(settings.mqtt_user, settings.mqtt_pass)
        if settings.mqtt_tls:
            self._client.tls_set()
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._handle_connect
        self._client.on_disconnect = self._handle_disconnect
        self._client.on_message = self._handle_message

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: str) -> None:
        self._client.publish(topic, payload, qos=1, retain=False)

    # --- Callbacks (paho-Thread) -----------------------------------------

    def _handle_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        if rc != 0:
            log.error("mqtt_abgelehnt", rc=str(rc))
            return
        self.connected = True
        client.subscribe(self.settings.topic_rx, qos=0)
        client.subscribe(self.settings.topic_admin, qos=1)
        if self._on_quota is not None:
            client.subscribe(self.settings.topic_quota, qos=1)
        log.info("mqtt_verbunden", rx=self.settings.topic_rx, admin=self.settings.topic_admin)

    def _handle_disconnect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        self.connected = False
        log.warning("mqtt_getrennt", rc=str(rc))

    def _handle_message(self, client: Any, userdata: Any, msg: mqtt.MQTTMessage) -> None:
        if msg.topic == self.settings.topic_admin:
            self._on_admin(msg.payload)
            return
        if self._on_quota is not None and msg.topic == self.settings.topic_quota:
            self._on_quota(msg.payload)
            return
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._on_message(msg.payload), self._loop)
