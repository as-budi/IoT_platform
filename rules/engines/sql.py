"""Model c — Kueri SQL atas aliran pesan (seperti AWS IoT Rules).

BELUM DIIMPLEMENTASIKAN. Rencana: satu kolom teks berisi kueri, mis.
    SELECT soil_moisture, device FROM 'telemetry' WHERE soil_moisture < 20
diurai ke pohon ekspresi yang aman (bukan dikirim ke database), lalu dinilai
untuk setiap pesan; hasil SELECT diteruskan ke aksi.
"""
from ..models import RuleModel
from .base import PlannedEngine


class SqlEngine(PlannedEngine):
    key = RuleModel.SQL
    summary = "A SQL-like query is evaluated against every message; matching rows go to the actions."
    example = "SELECT soil_moisture FROM 'telemetry' WHERE soil_moisture < 20 → set pump ON"
    best_for = "Filtering, reshaping and routing messages."
    state = "None"
