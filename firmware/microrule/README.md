# Micro rule: schema dan test vector untuk firmware

Folder ini dibuat oleh platform (tahap 26) untuk firmware ESP32 yang nanti menjalankan
micro rule (tahap 27-28). Penjelasan lengkap: `docs/tahap-26-micro-rule-model-dan-compiler.md`.

```
schema/fragment-v1.schema.json   bentuk fragmen yang dikirim platform ke device
vectors/eval/*.json              skenario evaluator (strategi AE dan sinyal lokal SE)
vectors/act/*.json               skenario penerima frame STATE di aktuator (strategi SE)
vectors/wire/*.json              frame DATA/STATE dalam hex dan hasil decode-nya, CRC-16
```

Semua vector dibuat oleh evaluator referensi Python (`rules/edge/`), yang semantiknya
sama dengan mesin aturan di cloud. Jangan diedit tangan; buat ulang dengan:

```
python manage.py export_microrule_vectors
```

## Memakai vector di firmware

Uji native PlatformIO (`pio test -e native`) membaca setiap file, lalu untuk setiap
langkah:

| Langkah | Panggilan di firmware |
|---|---|
| `{"rx": {"mac", "frame"}}` | callback penerimaan ESP-NOW dengan MAC sumber dan byte frame (hex) |
| `{"local": {"key", "value"}}` | pembacaan sensor di device itu sendiri |
| `{"tick": true}` | penilaian berkala (setiap `timing.tick` detik); seperti di cloud, hanya aturan yang PENDING yang dinilai ulang |
| `{"restart": true}` | (aktuator) keadaan dihapus seperti setelah reboot |

Setelah langkah itu (eval: penilaian dengan waktu `t` ms), transisi yang terjadi harus
sama dengan `expect` (`tr` dan `actions`), dan keadaan dengan `status` (eval) atau
`applied` (act: `true`, `false`, atau `null` = belum diketahui).

Catatan float32: ambang dan nilai sudah dibulatkan ke float32 oleh platform; bandingkan
dengan `float`, bukan `double`. Operator `==` memakai toleransi 1e-9 seperti di cloud.
