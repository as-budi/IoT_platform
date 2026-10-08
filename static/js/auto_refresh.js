/*
 * Pembaruan langsung (live updates) untuk halaman device dan halaman variabel.
 *
 * Jalur utama: WebSocket.
 *   Browser membuka koneksi ws://<host>/ws/devices/<id>/ yang terus terbuka.
 *   Begitu ada data baru, server MENGIRIM kabar tanpa ditanya, mis.
 *       {"type": "telemetry", "points": [{"variable": 7, "value": 25.1, ...}]}
 *   Halaman lalu memperbarui dirinya di tempat (angka di card, titik di chart)
 *   tanpa memuat ulang. Kabar yang tidak bisa diterapkan di tempat (mis. status
 *   command berubah) membuat halaman dimuat ulang.
 *
 * Jalur cadangan: POLLING (cara tahap 2b).
 *   - Bila server tidak mendukung WebSocket (mis. Daphne belum dipasang),
 *     halaman kembali bertanya ke /devices/<id>/updates/ setiap 5 detik.
 *   - Selama WebSocket tersambung pun, versi data tetap diperiksa setiap 30
 *     detik, untuk menangkap perubahan yang kabarnya tidak sampai (mis. data
 *     dari mqtt_bridge saat Redis belum dipakai).
 *
 * Halaman mendaftarkan cara menerapkan kabar lewat:
 *     AutoRefresh.on("telemetry", (event) => { ...; return true; });
 *   return true  = sudah diterapkan di tempat
 *   selain itu   = halaman perlu dimuat ulang
 * dan alasan menunda muat ulang lewat:
 *     AutoRefresh.hold(() => kondisi ? "alasan" : "");
 */
(function () {
  "use strict";

  const holds = [];
  const handlers = {};
  window.AutoRefresh = {
    hold(check) { holds.push(check); },
    on(type, handler) { (handlers[type] = handlers[type] || []).push(handler); },
  };

  const box = document.getElementById("auto-refresh");
  if (!box) return;

  const toggle = box.querySelector("input[type=checkbox]");
  const statusText = box.querySelector("[data-status]");
  const reloadButton = box.querySelector("[data-reload]");
  const versionUrl = box.dataset.url;
  const pollMs = (parseInt(box.dataset.interval, 10) || 5) * 1000;
  const SAFETY_POLL_MS = 30 * 1000;
  const wsUrl = box.dataset.wsPath
    ? (location.protocol === "https:" ? "wss://" : "ws://") + location.host + box.dataset.wsPath
    : "";
  let knownVersion = box.dataset.version;

  // --- penyimpanan browser (bisa tidak tersedia; selalu try/catch) ------------
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

  // --- kapan TIDAK boleh memuat ulang ---------------------------------------------
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

  // --- muat ulang halaman ------------------------------------------------------------
  let reloadNeeded = false;

  function reloadPage() {
    write(sessionStorage, SCROLL_KEY, String(Math.round(window.scrollY)));
    location.reload();
  }
  reloadButton.addEventListener("click", reloadPage);

  function requestReload() {
    reloadNeeded = true;
    tryReload();
  }

  function tryReload() {
    if (!reloadNeeded || !toggle.checked) return;
    const reason = holdReason();
    if (!reason) {
      setStatus("New data received, refreshing…", "success");
      stopAll();
      reloadPage();
      return;
    }
    setStatus("New data received. Paused because " + reason + ".", "primary");
    reloadButton.hidden = false;
  }
  // Muat ulang yang tertunda dicoba lagi berkala (mis. setelah zoom di-reset).
  setInterval(tryReload, 2000);

  // --- versi data (polling) --------------------------------------------------------------

  async function fetchVersion() {
    const response = await fetch(versionUrl, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    });
    if (response.status === 401) throw new Error("401");
    if (!response.ok) throw new Error("HTTP " + response.status);
    return (await response.json()).version;
  }

  // Bandingkan dengan versi yang diketahui; bila berbeda, muat ulang.
  async function checkVersion() {
    if (document.hidden || reloadNeeded) return;
    try {
      const version = await fetchVersion();
      if (version !== knownVersion) {
        requestReload();
      } else if (mode === "poll") {
        setStatus("Polling every " + pollMs / 1000 + " s · up to date at " + clock());
      }
    } catch (err) {
      if (err.message === "401") {
        stopAll();
        setStatus("Session expired. Log in again to resume.", "danger");
      } else if (mode === "poll") {
        setStatus("Cannot reach the server, retrying…", "warning");
      }
    }
  }

  // Setelah kabar WebSocket diterapkan di tempat, catat versi barunya supaya
  // pemeriksaan cadangan tidak menganggapnya sebagai perubahan yang terlewat.
  async function rememberVersion() {
    try { knownVersion = await fetchVersion(); } catch (err) { /* dicoba lagi nanti */ }
  }

  // --- WebSocket ---------------------------------------------------------------------------
  let mode = "off";          // "ws" | "poll" | "off"
  let socket = null;
  let failures = 0;
  let everOpened = false;
  let retryTimer = null;
  let pollTimer = null;

  function handleEvent(event) {
    const list = handlers[event.type] || [];
    let applied = list.length > 0;
    for (const handler of list) {
      try {
        if (handler(event) !== true) applied = false;
      } catch (err) {
        console.error("Live update handler failed", err);
        applied = false;
      }
    }
    if (applied) rememberVersion(); else requestReload();
  }

  function connect() {
    mode = "ws";
    socket = new WebSocket(wsUrl);

    socket.addEventListener("open", () => {
      everOpened = true;
      failures = 0;
      setStatus("Live (WebSocket)", "success");
      // Perubahan antara halaman dibuat dan koneksi terbuka tidak dikabarkan;
      // periksa sekali, lalu berkala sebagai cadangan.
      checkVersion();
      clearInterval(pollTimer);
      pollTimer = setInterval(checkVersion, SAFETY_POLL_MS);
    });

    socket.addEventListener("message", (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch (err) { return; }
      handleEvent(event);
      if (!reloadNeeded) setStatus("Live (WebSocket) · last update " + clock(), "success");
    });

    socket.addEventListener("close", (closeEvent) => {
      socket = null;
      clearInterval(pollTimer);
      if (mode !== "ws") return;  // ditutup sengaja (saklar dimatikan)
      if (closeEvent.code === 4401) {
        mode = "off";
        setStatus("Session expired. Log in again to resume.", "danger");
        return;
      }
      if (closeEvent.code === 4403) {
        mode = "off";
        setStatus("You no longer have access to this device.", "danger");
        return;
      }
      failures += 1;
      if (!everOpened && failures >= 2) {
        // Server tidak melayani WebSocket sama sekali -> pakai polling.
        startPolling();
        return;
      }
      const delay = Math.min(30, 2 ** failures);
      setStatus("Connection lost, reconnecting in " + delay + " s…", "warning");
      retryTimer = setTimeout(connect, delay * 1000);
    });
  }

  function startPolling() {
    mode = "poll";
    setStatus("WebSocket unavailable; polling every " + pollMs / 1000 + " s", "warning");
    checkVersion();
    clearInterval(pollTimer);
    pollTimer = setInterval(checkVersion, pollMs);
  }

  function start() {
    reloadButton.hidden = !reloadNeeded;
    if (wsUrl && "WebSocket" in window) connect(); else startPolling();
  }

  function stopAll() {
    mode = "off";
    clearTimeout(retryTimer);
    clearInterval(pollTimer);
    if (socket) socket.close();
    socket = null;
  }

  toggle.addEventListener("change", () => {
    write(localStorage, SETTING_KEY, toggle.checked ? "on" : "off");
    if (toggle.checked) {
      start();
    } else {
      stopAll();
      setStatus("Off");
    }
  });

  // Tab yang kembali terlihat: periksa segera (polling dihentikan saat tersembunyi).
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && mode !== "off") checkVersion();
  });

  toggle.checked = read(localStorage, SETTING_KEY) !== "off";
  if (toggle.checked) start(); else setStatus("Off");
})();

/*
 * Alat bantu tampilan yang dipakai halaman device dan variabel.
 */
window.LiveFormat = {
  // Sama dengan filter show_value: ON/OFF untuk boolean, 2 desimal untuk angka.
  value(value, isBoolean) {
    if (value === null || value === undefined) return "—";
    return isBoolean ? (value >= 0.5 ? "ON" : "OFF") : Number(value).toFixed(2);
  },
  // Sama dengan format tanggal tabel history: "08 Oct 2026 14:03:22".
  dateTime(ms) {
    const d = new Date(ms);
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const two = (n) => String(n).padStart(2, "0");
    return `${two(d.getDate())} ${months[d.getMonth()]} ${d.getFullYear()} ` +
      `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
  },
  // "just now", "3 minutes ago", ... untuk elemen yang diperbarui langsung.
  ago(ms) {
    const seconds = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (seconds < 60) return "just now";
    const units = [[86400, "day"], [3600, "hour"], [60, "minute"]];
    for (const [size, name] of units) {
      if (seconds >= size) {
        const n = Math.floor(seconds / size);
        return `${n} ${name}${n === 1 ? "" : "s"} ago`;
      }
    }
    return "just now";
  },
};

// Teks "x ago" pada elemen ber-atribut data-live-ts diperbarui setiap 15 detik.
setInterval(() => {
  document.querySelectorAll("[data-live-ts]").forEach((el) => {
    const suffix = el.dataset.liveSuffix || "";
    el.textContent = window.LiveFormat.ago(Number(el.dataset.liveTs)) + suffix;
  });
}, 15000);
