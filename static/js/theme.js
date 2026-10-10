/*
 * Mode tampilan: Light, Dark, atau Auto (mengikuti pengaturan sistem operasi).
 *
 * Bootstrap 5.3 sudah punya tema gelap bawaan: cukup pasang atribut
 *     <html data-bs-theme="dark">
 * dan semua komponen Bootstrap (card, tabel, form, tombol) berganti warna.
 * Warna tambahan milik kita sendiri (latar halaman, chart) didefinisikan
 * sebagai variabel CSS di static/css/app.css untuk kedua mode.
 *
 * Skrip ini dimuat di <head> (bukan di akhir <body>) supaya tema sudah
 * terpasang SEBELUM halaman digambar; kalau tidak, halaman sempat "berkedip"
 * putih sebelum menjadi gelap.
 *
 * Pilihan pengguna disimpan di localStorage browser. Setiap kali tema berubah,
 * event "themechange" dikirim; chart mendengarkannya untuk mengganti warnanya.
 */
(function () {
  "use strict";

  const KEY = "theme";
  const systemDark = window.matchMedia("(prefers-color-scheme: dark)");

  function load() {
    try { return localStorage.getItem(KEY) || "auto"; } catch (err) { return "auto"; }
  }
  function save(choice) {
    try {
      if (choice === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, choice);
    } catch (err) { /* mode privat: pilihan tidak diingat, tetapi tetap berlaku */ }
  }

  // "auto" diterjemahkan menjadi light/dark sesuai sistem operasi.
  function resolve(choice) {
    if (choice === "light" || choice === "dark") return choice;
    return systemDark.matches ? "dark" : "light";
  }

  let choice = load();

  function apply() {
    const theme = resolve(choice);
    document.documentElement.setAttribute("data-bs-theme", theme);
    // Tandai tombol yang sedang aktif (bila tombolnya sudah ada di halaman).
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      const active = button.dataset.themeChoice === choice;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    document.dispatchEvent(new CustomEvent("themechange", { detail: { theme, choice } }));
  }

  apply();

  // Mode Auto: ikut berubah ketika pengaturan sistem operasi berubah.
  systemDark.addEventListener("change", () => {
    if (choice === "auto") apply();
  });

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      button.addEventListener("click", () => {
        choice = button.dataset.themeChoice;
        save(choice);
        apply();
      });
    });
    apply();  // sinkronkan status tombol
  });

  window.Theme = {
    current: () => document.documentElement.getAttribute("data-bs-theme"),
  };
})();
