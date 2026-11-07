# Tahap 26 (tambahan) — Perbaikan dari uji langsung dengan ESP32

Sepuluh perbaikan setelah platform diuji dengan ESP32 sungguhan.

| # | Perbaikan | Cara kerja | File utama |
|---|---|---|---|
| 1 | Status command berubah menjadi **Expired** tanpa refresh | Baris command yang masih berjalan membawa `data-check-at` (waktu kedaluwarsa). `auto_refresh.js` memeriksa versi data sesaat setelah waktu itu; endpoint `/devices/<id>/updates/` kini menjalankan `Command.objects.expire_due()`, jadi versinya berubah dan halaman memperbarui diri. | `devices/views.py` (`device_updates`), `static/js/auto_refresh.js`, `device_detail.html` |
| 2 | Tombol hapus di **daftar firmware** | Ikon hapus di akhir baris (membuka konfirmasi). Firmware yang pernah dipakai pembaruan tetap disimpan: ikon nonaktif dengan alasan, halaman konfirmasi tidak menawarkan tombol hapus. | `ota/templates/ota/package_list.html`, `package_confirm_delete.html`, `templates/includes/row_actions.html` (`delete_blocked`) |
| 3 | Tabel **command** lebih proporsional | Parameters dan Response berbagi lebar sama; kolom lain selebar isinya (tidak terpotong); JSON panjang dipatahkan. | `.command-table` di `app.css` |
| 4 | **Data history** menjadi kartu yang bisa dibuka/ditutup | `<details>` seperti kartu JSON aturan: tertutup secara bawaan, judul + jumlah baris + tombol Download selalu terlihat. Terbuka saat berpindah halaman (`?page=`) dan diingat selama tab terbuka (sessionStorage). | `variable_detail.html` |
| 5 | Kaki halaman | Satu teks di kanan: *© Computer Engineering Study Program - Faculty of Computer Science - Universitas Brawijaya*. | `templates/includes/ui_footer.html` |
| 6 | **Download Excel** di log aktivitas | `/activity/export.xlsx?<filter>`: event yang cocok dengan filter (semua halaman), maks. 200.000 baris. Kolom Time berupa tanggal Excel; kolom Owner hanya untuk Admin. | `activity/export.py`, `activity/views.py` (`filtered_events`, `ActivityExportView`) |
| 7 | Pesan **desired** hanya berisi aktuator yang diubah | Lihat di bawah. | `mqtt_bridge/outbox.py` |
| 8 | Chart **tanpa smoothing** + pilihan **moving average** | Garis lurus antar titik (`tension: 0`, sebelumnya kurva Bezier 0,25 yang membuat lonjakan tampak dimulai lebih awal). Pilihan *Moving average · 3/5/10/20/50 points*: rata-rata N titik terakhir (kausal, tidak mendahului data), bacaan asli tetap tampil sebagai garis tipis. Pilihan disimpan browser (localStorage). | `variable_detail.html`, `devices/views.py` (`SMOOTHING_OPTIONS`) |
| 9 | **Warna** garis chart per variabel | Tombol bulat di sebelah tombol zoom: 8 warna siap pilih, warna bebas, atau *Default* (warna tema). Disimpan di `Variable.chart_color` (migrasi devices 0024), berlaku untuk semua yang melihat; mengubahnya perlu peran Editor. `POST /devices/<d>/variables/<v>/color/`. | `devices/models.py`, `VariableColorView`, `variable_detail.html` |
| 10 | Identitas di side menu lebih kecil, garisnya **sejajar** bilah atas | Kepala side menu setinggi bilah atas (`--ui-topbar-h`), garis bawahnya selebar side menu. | `app.css` (`.sidebar-head`) |
| 11 | **RTT** di setiap command | Kolom Response menampilkan *RTT* = `completed_at − sent_at`: dari command dikirim (MQTT publish, atau diambil device lewat HTTP/CoAP) sampai jawaban diterima, termasuk waktu device menjalankannya. Hanya untuk Succeeded/Failed. | `Command.rtt_ms`, filter `duration_ms`, `device_detail.html` |

## Pesan desired: hanya yang berubah (no. 7)

`MQTT_DESIRED_MESSAGE` di `.env`:

- `changes` (bawaan): saat satu aktuator diubah, pesan `<prefix>/<token>/desired` hanya berisi aktuator itu,
  mis. `{"lamp": 0}`. Aktuator lain tidak diperintah ulang. **Semua** aktuator tetap dikirim bila device
  baru online (sesi baru, setelah jeda 2 detik agar device sempat subscribe), saat `mqtt_bridge` (ulang)
  tersambung, dan saat access token diganti.
- `all`: setiap perubahan mengirim semua aktuator (perilaku lama).

Pesan tetap *retained*. Firmware harus membaca hanya kunci yang ada di pesan; template
`firmware/esp32_mqtt` sudah begitu (`if (!desired["lamp"].isNull())`). Endpoint HTTP/CoAP `desired`
tetap mengembalikan semua aktuator (device yang bertanya memang butuh keadaan lengkap).

## Update server

```bash
sudo /srv/iot/app/deploy/scripts/update.sh   # migrasi devices 0024, collectstatic, restart semua layanan (termasuk iot-mqtt)
```
