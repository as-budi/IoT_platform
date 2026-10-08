/*
 * Auto-refresh: muat ulang halaman ketika ada data baru.
 *
 * Cara kerjanya disebut "polling":
 *   1. Saat halaman dibuat, server menulis "versi data" ke atribut
 *      data-version pada elemen #auto-refresh.
 *   2. Setiap beberapa detik, skrip ini meminta versi terbaru ke
 *      data-url (view devices.views.device_updates), balasannya JSON kecil:
 *          {"version": "1532-4-17-0-0"}
 *   3. Bila versinya berbeda, ada sesuatu yang baru -> halaman dimuat ulang.
 *
 * Halaman TIDAK dimuat ulang saat pengguna sedang mengetik di form atau sedang
 * men-zoom chart, supaya pekerjaannya tidak hilang. Sebagai gantinya muncul
 * tombol "Reload now".
 *
 * Halaman lain bisa menambah alasan menunda lewat:
 *     AutoRefresh.hold(() => kondisi ? "alasan" : "");
 *
 * Skrip ini dimuat di base.html, tetapi hanya aktif di halaman yang punya
 * elemen #auto-refresh (halaman device dan halaman variabel).
 */
(function () {
  "use strict";

  const holds = [];
  window.AutoRefresh = {
    hold(check) { holds.push(check); },
  };

  const box = document.getElementById("auto-refresh");
  if (!box) return;

  const toggle = box.querySelector("input[type=checkbox]");
  const statusText = box.querySelector("[data-status]");
  const reloadButton = box.querySelector("[data-reload]");
  const url = box.dataset.url;
  const intervalMs = (parseInt(box.dataset.interval, 10) || 5) * 1000;
  const knownVersion = box.dataset.version;

  // Penyimpanan browser bisa tidak tersedia (mode privat, dsb.), jadi selalu
  // dibungkus try/catch. Bila gagal, fitur tetap jalan, hanya tidak diingat.
  const SETTING_KEY = "autoRefresh";
  const SCROLL_KEY = "autoRefreshScroll:" + location.pathname + location.search;
  function read(storage, key) {
    try { return storage.getItem(key); } catch (err) { return null; }
  }
  function write(storage, key, value) {
    try {
      if (value === null) storage.removeItem(key); else storage.setItem(key, value);
    } catch (err) { /* abaikan */ }
  }

  // Setelah dimuat ulang oleh skrip ini, kembalikan posisi scroll sebelumnya.
  const savedScroll = read(sessionStorage, SCROLL_KEY);
  if (savedScroll !== null) {
    write(sessionStorage, SCROLL_KEY, null);
    window.scrollTo(0, parseInt(savedScroll, 10) || 0);
  }

  // Tandai bila pengguna sudah mengisi sesuatu di form mana pun.
  let formIsDirty = false;
  document.addEventListener("input", (event) => {
    if (event.target.closest("form") && !box.contains(event.target)) formIsDirty = true;
  });

  function holdReason() {
    if (formIsDirty) return "a form has unsaved input";
    const active = document.activeElement;
    if (active && active.matches("input, textarea, select") && !box.contains(active)) {
      return "you are typing";
    }
    for (const check of holds) {
      const reason = check();
      if (reason) return reason;
    }
    return "";
  }

  function setStatus(text, tone) {
    statusText.textContent = text;
    statusText.className = tone ? "text-" + tone : "text-muted";
  }

  function clock() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function reloadPage() {
    write(sessionStorage, SCROLL_KEY, String(Math.round(window.scrollY)));
    location.reload();
  }
  reloadButton.addEventListener("click", reloadPage);

  let timer = null;
  let hasNewData = false;

  async function check() {
    // Tab yang tidak terlihat tidak perlu bertanya ke server.
    if (document.hidden) return;
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-store",
        credentials: "same-origin",
      });
      if (response.status === 401) {
        stop();
        setStatus("Session expired. Log in again to resume.", "danger");
        return;
      }
      if (!response.ok) throw new Error("HTTP " + response.status);
      const data = await response.json();
      if (data.version !== knownVersion) hasNewData = true;
    } catch (err) {
      setStatus("Cannot reach the server, retrying…", "warning");
      return;
    }

    if (!hasNewData) {
      setStatus("Up to date · checked " + clock());
      return;
    }
    const reason = holdReason();
    if (!reason) {
      setStatus("New data received, refreshing…", "success");
      stop();
      reloadPage();
      return;
    }
    // Ditunda; dicek lagi di putaran berikutnya (mis. setelah zoom di-reset).
    setStatus("New data received. Paused because " + reason + ".", "primary");
    reloadButton.hidden = false;
  }

  function start() {
    if (timer) return;
    check();
    timer = setInterval(check, intervalMs);
  }

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  toggle.addEventListener("change", () => {
    write(localStorage, SETTING_KEY, toggle.checked ? "on" : "off");
    if (toggle.checked) {
      start();
    } else {
      stop();
      setStatus("Off");
      reloadButton.hidden = !hasNewData;
    }
  });

  // Saat tab kembali terlihat, langsung periksa tanpa menunggu putaran berikutnya.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && timer) check();
  });

  toggle.checked = read(localStorage, SETTING_KEY) !== "off";
  if (toggle.checked) start(); else setStatus("Off");
})();
