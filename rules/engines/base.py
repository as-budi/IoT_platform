"""
Kerangka bersama semua model rule engine.

Setiap model (ECA, rule chain, SQL, CEP, production rules, state machine) adalah
satu class turunan RuleEngine. Worker (`manage.py rule_engine`) tidak perlu tahu
model mana yang ada: ia hanya memanggil dua method ini untuk setiap mesin yang
sudah diimplementasikan:

    on_message(message, now)  ada pesan baru (mis. telemetri) -> nilai aturan
    tick(now)                 dipanggil berkala tanpa pesan -> aturan berbasis waktu
                              ("selama 60 detik", "device diam 5 menit")

Bentuk pesan (lihat rules/queue.py), mengikuti pola ThingsBoard:
    {"type": "TELEMETRY",
     "originator": {"device": 3},
     "data": [{"variable": 7, "key": "soil_moisture", "value": 18.5, "ts": "..."}],
     "metadata": {"protocol": "MQTT", "received_at": "..."}}
"""
from ..models import RuleModel


class RuleEngine:
    key = None              # RuleModel.X
    implemented = False
    summary = ""            # satu kalimat: cara kerjanya
    example = ""            # contoh aturan
    best_for = ""           # cocok untuk apa
    state = ""              # butuh state atau tidak

    @property
    def label(self):
        return RuleModel(self.key).label

    def on_message(self, message, now):
        raise NotImplementedError(f"The {self.label} rule model is not implemented yet.")

    def tick(self, now):
        raise NotImplementedError(f"The {self.label} rule model is not implemented yet.")

    def describe(self, rule):
        """Ringkasan satu aturan dalam kalimat, untuk halaman daftar/detail."""
        return ""


class PlannedEngine(RuleEngine):
    """Model yang sudah terdaftar sebagai pilihan tetapi belum diimplementasikan."""

    implemented = False

    def on_message(self, message, now):
        return None  # sengaja diam: belum ada aturan model ini yang bisa dibuat

    def tick(self, now):
        return None
