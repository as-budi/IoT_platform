"""Model f — Mesin keadaan (state machine).

BELUM DIIMPLEMENTASIKAN sebagai model aturan umum. Contoh kecilnya sudah ada:
siklus hidup Alarm (active/cleared × acknowledged) di rules/models.py.
Rencana: tabel State dan Transition (dari, ke, kondisi, aksi saat masuk/keluar)
per aturan; satu keadaan aktif per device.
"""
from ..models import RuleModel
from .base import PlannedEngine


class StateMachineEngine(PlannedEngine):
    key = RuleModel.STATE_MACHINE
    summary = "Each device is in one named state; conditions move it between states and run entry/exit actions."
    example = "NORMAL →(temp > 35)→ WARNING →(temp > 45)→ CRITICAL →(temp < 30)→ NORMAL"
    best_for = "Alarms with stages and device life cycles."
    state = "Yes (current state)"
