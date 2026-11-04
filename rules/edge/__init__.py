"""
Micro rule (tahap 26): aturan ECA dipecah menjadi fragmen untuk device yang terlibat,
supaya device bisa menjalankan aturan sendiri lewat ESP-NOW saat platform tidak
terjangkau. Tahap 26 hanya membuat RENCANA; device belum menjalankannya.

    ir.py         Rule (database) -> representasi antara (IR) + hash
    analyze.py    bisa dijalankan di device? (FULL / PARTIAL / NONE + alasan)
    placement.py  di device mana aturan dievaluasi: aktuator (AE) atau sensor (SE)
    compile.py    IR -> fragmen per device, ukuran, tabel publikasi
    wire.py       format frame ESP-NOW (DATA, STATE) dan hash key CRC-16
    evaluator.py  evaluator referensi (semantik sama dengan mesin cloud)
    emitter.py    pengirim frame STATE (strategi SE)
    actuator.py   penerima frame STATE di aktuator (strategi SE)
    metrics.py    metrik analitik untuk membandingkan AE dan SE
    sim.py        simulator kejadian diskret (kehilangan frame, device mati)
    vectors.py    test vector untuk firmware (firmware/microrule/vectors/)
    diagram.py    SVG penempatan untuk halaman aturan
    services.py   menyimpan rencana (EdgePlan, EdgeFragment)

Rencana: docs/rencana-tahap-26-micro-rule.md dan docs/rencana-tahap-26b-evaluator-di-sensor.md.
Penjelasan: docs/tahap-26-micro-rule-model-dan-compiler.md.
"""
