from django.apps import AppConfig


class RulesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rules"
    verbose_name = "Rule engine"

    def ready(self):
        # Sinyal: aturan dihapus -> JSON yang pernah di-publish dihapus dari broker.
        from . import publish  # noqa: F401
