from django.apps import AppConfig


class MqttBridgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mqtt_bridge"
    verbose_name = "MQTT bridge"
