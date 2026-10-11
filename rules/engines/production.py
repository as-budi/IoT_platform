"""Model e — Production rules dengan forward chaining (algoritma RETE, seperti Drools).

BELUM DIIMPLEMENTASIKAN. Rencana: "working memory" berisi fakta (nilai terkini
semua variabel, atribut device, fakta turunan). Aturan IF fakta... THEN tambah
fakta/aksi dinilai berulang sampai tidak ada aturan baru yang terpicu; RETE
menghindari pemeriksaan ulang yang tidak perlu.
"""
from ..models import RuleModel
from .base import PlannedEngine


class ProductionEngine(PlannedEngine):
    key = RuleModel.PRODUCTION
    summary = "Rules add facts to a shared working memory; new facts can fire further rules (forward chaining)."
    example = "IF soil dry AND no rain forecast THEN needs_water; IF needs_water AND tank ok THEN pump ON"
    best_for = "Many interrelated business rules."
    state = "Yes (working memory)"
