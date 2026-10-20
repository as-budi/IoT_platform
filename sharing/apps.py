from django.apps import AppConfig


class SharingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sharing"
    verbose_name = "Device sharing"

    def ready(self):
        # Log aktivitas untuk berbagi device dan keanggotaan grup.
        from . import signals  # noqa: F401
