"""Optional MQTT / Home Assistant integration (stdlib MQTT 3.1.1 client). Audio never depends on it."""
from .bridge import MqttBridge
from .client import MqttClient

__all__ = ["MqttBridge", "MqttClient"]
