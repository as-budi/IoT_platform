"""Model d — Complex Event Processing dengan jendela waktu.

BELUM DIIMPLEMENTASIKAN. Rencana: jendela (tumbling/sliding) per device per
aturan, fungsi agregat (avg, min, max, count) dan pola urutan ("A lalu B dalam
5 menit"); state jendela disimpan di Redis atau database.
"""
from ..models import RuleModel
from .base import PlannedEngine


class CepEngine(PlannedEngine):
    key = RuleModel.CEP
    summary = "Rules look at many events together: averages over time windows and event sequences."
    example = "avg(temperature) over 10 min > 35, or door_open followed by motion within 1 min"
    best_for = "Trends, aggregates and patterns across events."
    state = "Yes (time windows)"
