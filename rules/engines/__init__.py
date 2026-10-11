"""
Daftar semua model rule engine.

    ENGINES[RuleModel.ECA].implemented   -> True
    ENGINES[RuleModel.CEP].implemented   -> False (pilihan tersedia, belum bisa dipakai)

Menambah implementasi model baru nanti cukup: ganti PlannedEngine di file
model itu dengan implementasi sungguhan, set implemented = True, lalu buat
form/halaman untuk spesifikasinya. Worker otomatis ikut memanggilnya.
"""
from ..models import RuleModel
from .cep import CepEngine
from .eca import EcaEngine
from .flow import FlowEngine
from .production import ProductionEngine
from .sql import SqlEngine
from .state_machine import StateMachineEngine

# Urutan sama dengan urutan pilihan di halaman "New rule".
ENGINES = {
    engine.key: engine
    for engine in (
        EcaEngine(),
        FlowEngine(),
        SqlEngine(),
        CepEngine(),
        ProductionEngine(),
        StateMachineEngine(),
    )
}
assert set(ENGINES) == set(RuleModel.values), "Every RuleModel needs an engine entry"


def implemented_engines():
    return [engine for engine in ENGINES.values() if engine.implemented]
