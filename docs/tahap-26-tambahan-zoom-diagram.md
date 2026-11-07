# Tahap 26 (tambahan) — Zoom diagram rule flow dan state machine

Diagram rule chain (model FLOW) dan state machine kini dapat diperbesar, diperkecil, dan digeser,
di halaman detail aturan maupun di formulir aturan.

## Cara pakai

| Kontrol | Fungsi |
|---|---|
| **−** / **+** | perkecil / perbesar (×1,25), batas 25–400 % |
| angka persen | kembali ke ukuran asli (100 %) |
| **Fit** | seluruh diagram muat di kotaknya |
| ikon layar penuh | diagram memenuhi layar (Esc untuk keluar); tidak muncul bila browser tidak mendukung |
| Ctrl/⌘ + scroll, pinch di trackpad | zoom di titik kursor |
| seret dengan mouse | geser diagram yang lebih besar dari kotaknya (di layar sentuh: geser dengan jari) |

Kotak diagram setinggi maksimal 75 % layar; diagram yang lebih besar digulir di dalam kotak.
Zoom dan posisi gulir tetap dipertahankan saat kotak *Last run* / *Devices* diperbarui langsung.
**Export PNG** tetap menghasilkan gambar ukuran asli, berapa pun zoom yang dipakai.

## File

| File | Isi |
|---|---|
| `rules/templates/rules/includes/diagram_zoom.html` | tombol zoom (dipakai `flow_diagram.html` dan `machine_diagram.html`) |
| `static/js/diagram_zoom.js` | logika zoom/geser; zoom mengubah style width/height `<svg>` (vektor, tetap tajam) |
| `static/css/app.css` | `.flow-diagram-wrap` (gulir dua arah, kursor geser), `.flow-zoom`, tampilan layar penuh |
| `rules/templates/rules/rule_{detail,form}_{flow,machine}.html` | memuat `diagram_zoom.js` |

## Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh   # collectstatic
```
