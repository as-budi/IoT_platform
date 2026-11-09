from django.apps import AppConfig


class CamerasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cameras"
    verbose_name = "cameras"

    def ready(self):
        from . import signals  # noqa: F401  (hapus file gambar bersama barisnya)
