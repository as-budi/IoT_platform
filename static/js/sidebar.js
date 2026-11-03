/*
 * Tombol menyembunyikan / menampilkan menu utama (side menu) di layar lebar.
 *
 * Pilihan disimpan di localStorage, jadi tetap berlaku di halaman lain dan setelah
 * browser dibuka lagi. Seperti theme.js, skrip ini dimuat di <head>: kelas
 * "sidebar-collapsed" dipasang di <html> SEBELUM halaman digambar, supaya menu tidak
 * sempat muncul lalu hilang. Di layar kecil menu sudah tersembunyi (offcanvas),
 * jadi kelas ini hanya berpengaruh mulai 992 px (lihat static/css/app.css).
 *
 * Tombol mana pun dengan atribut data-sidebar-toggle membalik keadaannya.
 */
(function () {
  "use strict";

  const KEY = "sidebar";
  const CLASS = "sidebar-collapsed";
  const root = document.documentElement;

  function load() {
    try { return localStorage.getItem(KEY) === "hidden"; } catch (err) { return false; }
  }
  function save(hidden) {
    try {
      if (hidden) localStorage.setItem(KEY, "hidden"); else localStorage.removeItem(KEY);
    } catch (err) { /* mode privat: pilihan tidak diingat */ }
  }

  function apply(hidden) {
    root.classList.toggle(CLASS, hidden);
    document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
      button.setAttribute("aria-expanded", String(!hidden));
    });
  }

  apply(load());

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-sidebar-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const hidden = !root.classList.contains(CLASS);
        save(hidden);
        apply(hidden);
        // Chart dan peta perlu tahu bahwa lebar isi berubah.
        window.dispatchEvent(new Event("resize"));
      });
    });
    apply(root.classList.contains(CLASS));   // sinkronkan aria-expanded
  });
})();
