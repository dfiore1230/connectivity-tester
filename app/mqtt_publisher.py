from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LOG_PATH = Path(os.environ.get("LOG_FILE", "/logs/connectivity.log"))


@dataclass
class MqttSettings:
    enabled: bool
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    tls: bool
    topic_prefix: str


class MqttPublisher(threading.Thread):
    """Tail connectivity.log and publish new entries to MQTT if enabled."""

    daemon = True

    def __init__(self, settings: MqttSettings):
        super().__init__(name="mqtt-publisher")
        self.settings = settings
        self.client = None
        self._stop_event = threading.Event()
        self._last_size = 0
        self._last_inode = None
        self._warned_missing = False

    def stop(self) -> None:
        self._stop_event.set()
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass

    def _connect(self):
        try:
            import paho.mqtt.client as mqtt
        except Exception:
            if not self._warned_missing:
                print("MQTT requested but paho-mqtt is not installed; skipping publisher")
                self._warned_missing = True
            return None

        client = mqtt.Client()
        if self.settings.username:
            client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.tls:
            client.tls_set()
        try:
            client.connect(self.settings.host, self.settings.port, keepalive=60)
        except Exception as exc:
            print(f"MQTT connection failed: {exc}")
            return None
        client.loop_start()
        return client

    def _ensure_client(self):
        if self.client is None:
            self.client = self._connect()
        return self.client

    def _publish(self, topic: str, payload: str):
        client = self._ensure_client()
        if client is None:
            return
        try:
            client.publish(topic, payload, qos=0, retain=False)
        except Exception as exc:
            print(f"MQTT publish failed: {exc}")

    def run(self):
        if not self.settings.enabled:
            return

        while not self._stop_event.is_set():
            if not LOG_PATH.exists():
                time.sleep(2)
                continue

            try:
                stat = LOG_PATH.stat()
            except FileNotFoundError:
                time.sleep(2)
                continue

            if self._last_inode != stat.st_ino or stat.st_size < self._last_size:
                self._last_size = 0
                self._last_inode = stat.st_ino

            try:
                with LOG_PATH.open("r") as handle:
                    handle.seek(self._last_size)
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        self._last_size = handle.tell()
                        topic = f"{self.settings.topic_prefix}/measurements"
                        self._publish(topic, line)
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        loss = record.get("loss_pct")
                        status_topic = f"{self.settings.topic_prefix}/status"
                        try:
                            internet_up = loss is not None and float(loss) < 100
                        except (TypeError, ValueError):
                            internet_up = None

                        status_payload = json.dumps(
                            {
                                "timestamp": record.get("timestamp"),
                                "target": record.get("target") or record.get("dst_host"),
                                "loss_pct": loss,
                                "rtt_avg_ms": record.get("rtt_avg_ms"),
                                "internet_up": internet_up,
                            }
                        )
                        self._publish(status_topic, status_payload)
            except Exception:
                # Avoid tight loop on read errors
                time.sleep(2)
                continue

            time.sleep(1)


def build_settings_from_env() -> MqttSettings:
    enabled = os.environ.get("ENABLE_MQTT", "0") == "1"
    host = os.environ.get("MQTT_HOST", "localhost")
    port = int(os.environ.get("MQTT_PORT", "1883"))
    username = os.environ.get("MQTT_USERNAME")
    password = os.environ.get("MQTT_PASSWORD")
    tls = os.environ.get("MQTT_TLS", "0") == "1"
    topic_prefix = os.environ.get("MQTT_TOPIC_PREFIX", "connectivity")
    return MqttSettings(
        enabled=enabled,
        host=host,
        port=port,
        username=username,
        password=password,
        tls=tls,
        topic_prefix=topic_prefix,
    )
