# Tahap 10: Profil dan ubah password

Sebelum tahap ini, data akun hanya bisa diubah Admin lewat halaman **Users**. Pengguna biasa
tidak bisa memperbaiki namanya sendiri, apalagi mengganti password (misalnya setelah akun
dibuatkan Admin atau setelah `seed_demo`).

Tahap ini menambahkan dua halaman yang bisa dibuka **setiap pengguna yang login**:

| URL | Nama url | View | Isi |
|---|---|---|---|
| `/accounts/profile/` | `accounts:profile` | `ProfileView` | ringkasan akun + form nama depan, nama belakang, email |
| `/accounts/profile/password/` | `accounts:password_change` | `PasswordChangeView` | password lama, password baru dua kali |

Cara membukanya: klik nama Anda di kiri bawah sidebar.

## File yang berubah

| File | Perubahan |
|---|---|
| `accounts/forms.py` | `ProfileForm`, `PasswordChangeBootstrapForm` |
| `accounts/views.py` | `ProfileView`, `PasswordChangeView` |
| `accounts/urls.py` | dua URL di atas |
| `accounts/templates/accounts/profile.html` | halaman profil |
| `accounts/templates/accounts/password_change.html` | halaman ubah password |
| `templates/base.html` | nama pengguna di sidebar menjadi tautan ke profil |
| `static/css/app.css` | gaya tautan nama pengguna |
| `accounts/tests.py` | `ProfileTests`, `PasswordChangeTests` |

Tidak ada perubahan model, jadi **tidak ada migrasi**.

## Konsep 1: "profil milik sendiri" tanpa `<pk>` di URL

Halaman Admin untuk mengubah pengguna memakai URL `/accounts/users/<pk>/edit/`. Kalau pola
itu dipakai untuk profil, setiap view harus memeriksa "apakah `pk` ini milik pengguna yang
login?". Lupa memeriksa sekali saja, budi bisa mengubah akun siti hanya dengan mengganti
angka di address bar (celah yang disebut IDOR, *Insecure Direct Object Reference*).

`ProfileView` menghindari masalah itu dari akarnya: URL-nya tidak memuat `pk`, dan objek
yang diubah selalu diambil dari sesi login.

```python
class ProfileView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    form_class = ProfileForm

    def get_object(self, queryset=None):
        return self.request.user      # selalu akun sendiri
```

## Konsep 2: daftar field adalah batas keamanan

```python
class ProfileForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
```

`ModelForm` hanya membaca dan menyimpan field yang tercantum di `fields`. Walaupun seseorang
menyisipkan `role=ADMIN` atau `is_superuser=on` ke data POST (misalnya lewat DevTools browser),
nilai itu diabaikan. Prinsip yang sama sudah dipakai `RegisterForm` di tahap 1. Username dan
role tetap hanya bisa diubah Admin.

Tes `test_cannot_raise_own_privileges` membuktikan hal ini dengan benar-benar mengirim
field-field berbahaya tersebut.

## Konsep 3: ubah password dengan view bawaan Django

`PasswordChangeView` di project ini hanya mewarisi view bawaan Django dan mengganti form,
template, dan tujuan setelah berhasil. Semua bagian penting sudah dikerjakan Django:

1. **Wajib login**: view bawaan sudah memakai `login_required`.
2. **Wajib password lama** (`old_password`). Orang yang kebetulan memakai komputer yang masih
   login tidak bisa mengambil alih akun dengan mengganti password.
3. **Validasi password baru** dengan aturan `AUTH_PASSWORD_VALIDATORS` di `config/settings.py`:
   minimal 8 karakter, tidak mirip username/email, tidak termasuk password umum, tidak
   seluruhnya angka. Kedua isian password baru juga harus sama.
4. **Disimpan sebagai hash** (`set_password`), bukan teks asli.
5. **Sesi lain dikeluarkan.** Lihat bagian berikut.

### Mengapa sesi di browser lain ikut keluar?

Setiap sesi login Django menyimpan *session auth hash*, yaitu sidik jari yang diturunkan dari
hash password. Pada setiap request, Django membandingkan sidik jari di sesi dengan sidik jari
password saat ini.

```
Login di laptop dan HP           session_auth_hash = H(password lama)   (keduanya)
Ganti password di laptop         password -> hash baru
  update_session_auth_hash()     sesi laptop diperbarui = H(password baru)
Request berikutnya dari HP       H(password lama) != H(password baru) -> sesi dibuang -> login ulang
```

Jadi bila password Anda bocor dan seseorang sempat login, mengganti password langsung
mengeluarkan orang itu. Tes `test_change_password` memeriksa kedua sisi: browser yang
mengganti password tetap login, browser lain diarahkan ke halaman login.

## Mencoba

1. Login sebagai `budi`, klik nama **budi** di kiri bawah sidebar.
2. Ubah nama depan, klik **Save**. Muncul pesan *Your profile was updated.*
3. Klik **Change password**. Coba password lama yang salah, lalu password baru `12345678`;
   keduanya ditolak dengan pesan yang jelas.
4. Buka jendela *private* browser dan login sebagai budi. Ganti password di jendela pertama,
   lalu muat ulang jendela private: Anda diminta login lagi.

Tes otomatis:

```bash
python manage.py test accounts
```

## Catatan untuk tahap berikutnya

- Email belum diverifikasi dan belum wajib unik. Keduanya dikerjakan bersama fitur
  verifikasi email saat signup; saat itu mengganti email di halaman profil juga akan meminta
  verifikasi ke alamat baru.
- Begitu pengiriman email berfungsi, fitur **lupa password** (reset lewat email) bisa
  ditambahkan dengan view bawaan Django (`PasswordResetView` dan kawan-kawan).
