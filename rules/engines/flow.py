"""Model b — Flow-based / rule chain (graf berarah, seperti ThingsBoard & Node-RED).

BELUM DIIMPLEMENTASIKAN. Rencana: tabel RuleNode (jenis: filter, enrichment,
transformation, action) dan RuleLink (node asal -> node tujuan, label
"True"/"False"/"Success"/"Failure"). Pesan berjalan dari node input mengikuti
sambungan; on_message() menelusuri graf itu.
"""
from ..models import RuleModel
from .base import PlannedEngine


class FlowEngine(PlannedEngine):
    key = RuleModel.FLOW
    summary = "Each message travels through a graph of nodes (filter → enrich → transform → act)."
    example = "Telemetry → [soil_moisture < 20?] →True→ [add plant threshold] → [set pump ON] → [email]"
    best_for = "Complete processing pipelines with branches and reusable steps."
    state = "Optional, per node"
