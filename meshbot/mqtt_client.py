"""MQTT connection with automatic reconnect.

paho runs in a thread of its own, the bot in the event loop — which is why every
incoming message travels back into the loop via `run_coroutine_threadsafe`.
"""

from __future__ import annotations

import asyncio
import time
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
        # Since when the connection has been gone, as a monotonic timestamp.
        # `None` while connected. The health check needs the duration, not just
        # the flag: a reconnect takes seconds, a dead broker takes hours, and
        # only one of the two is worth reporting as unhealthy.
        self.getrennt_seit: float | None = time.monotonic()

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

    # --- Callbacks (paho thread) -----------------------------------------

    def _handle_connect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        if rc != 0:
            log.error("mqtt_abgelehnt", rc=str(rc))
            return
        self.connected = True
        self.getrennt_seit = None
        client.subscribe(self.settings.topic_rx, qos=0)
        client.subscribe(self.settings.topic_admin, qos=1)
        if self._on_quota is not None:
            client.subscribe(self.settings.topic_quota, qos=1)
        log.info("mqtt_verbunden", rx=self.settings.topic_rx, admin=self.settings.topic_admin)

    def _handle_disconnect(self, client: Any, userdata: Any, flags: Any, rc: Any, properties: Any = None) -> None:
        self.connected = False
        # Only on the first disconnect: paho may report repeatedly while
        # retrying, and each report would otherwise reset the clock and keep
        # the outage permanently below the grace period.
        if self.getrennt_seit is None:
            self.getrennt_seit = time.monotonic()
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
