/*
 * Zoom dan geser (pan) untuk diagram rule chain (flow) dan state machine.
 *
 * Diagram: <div data-flow-diagram> berisi .flow-diagram-wrap > svg.flow-diagram dan tombol
 * dari rules/includes/diagram_zoom.html (data-zoom="out|reset|in|fit|full").
 *
 * - Tombol − / + : perkecil / perbesar (×1,25), 25–400 %. Angka persen = kembali ke 100 %.
 * - Fit          : seluruh diagram muat di kotak tampilan.
 * - Layar penuh  : diagram memenuhi layar (Esc untuk keluar); tersembunyi bila browser tidak mendukung.
 * - Ctrl/⌘ + scroll (atau pinch di trackpad): zoom di titik kursor.
 * - Seret dengan mouse saat diagram lebih besar dari kotaknya: geser.
 *
 * Zoom dilakukan dengan mengubah style width/height <svg> (gambar vektor, tetap tajam). Atribut
 * width/height tidak diubah, jadi Export PNG (flow_export.js) tetap memakai ukuran asli.
 * Kotak "Last run" / "Devices" diganti HTML baru oleh pembaruan langsung; MutationObserver
 * memasang lagi zoom dan posisi gulir terakhir ke diagram yang baru.
 */
(function () {
  "use strict";

  const MIN = 0.25;
  const MAX = 4;
  const STEP = 1.25;
  const saved = new Map(); // urutan diagram di halaman -> {zoom, left, top}

  function parts(box) {
    const wrap = box.querySelector(".flow-diagram-wrap");
    return { wrap, svg: wrap && wrap.querySelector("svg.flow-diagram") };
  }

  function key(box) {
    return String(Array.prototype.indexOf.call(document.querySelectorAll("[data-flow-diagram]"), box));
  }

  function remember(box) {
    const { wrap } = parts(box);
    if (wrap) saved.set(key(box), { zoom: Number(box.dataset.zoom || 1), left: wrap.scrollLeft, top: wrap.scrollTop });
  }

  function update(box) {
    const { wrap, svg } = parts(box);
    const zoom = Number(box.dataset.zoom || 1);
    const label = box.querySelector("[data-zoom-value]");
    if (label) label.textContent = Math.round(zoom * 100) + "%";
    const out = box.querySelector('[data-zoom="out"]');
    const inn = box.querySelector('[data-zoom="in"]');
    if (out) out.disabled = zoom <= MIN + 1e-6;
    if (inn) inn.disabled = zoom >= MAX - 1e-6;
    if (wrap && svg) {
      const pannable = wrap.scrollWidth > wrap.clientWidth + 1 || wrap.scrollHeight > wrap.clientHeight + 1;
      wrap.classList.toggle("is-pannable", pannable);
    }
  }

  // anchor = titik (px, relatif terhadap kotak) yang tetap di tempat selama zoom.
  function setZoom(box, zoom, anchor) {
    const { wrap, svg } = parts(box);
    if (!svg) return;
    zoom = Math.min(MAX, Math.max(MIN, zoom));
    const old = Number(box.dataset.zoom || 1);
    const ax = anchor ? anchor.x : wrap.clientWidth / 2;
    const ay = anchor ? anchor.y : wrap.clientHeight / 2;
    const px = (wrap.scrollLeft + ax) / old;
    const py = (wrap.scrollTop + ay) / old;
    svg.style.width = Number(svg.getAttribute("width")) * zoom + "px";
    svg.style.height = Number(svg.getAttribute("height")) * zoom + "px";
    box.dataset.zoom = String(zoom);
    wrap.scrollLeft = px * zoom - ax;
    wrap.scrollTop = py * zoom - ay;
    update(box);
    remember(box);
  }

  function fitZoom(box) {
    const { wrap, svg } = parts(box);
    const maxH = parseFloat(getComputedStyle(wrap).maxHeight);
    const height = document.fullscreenElement === box || !maxH ? wrap.clientHeight : maxH;
    const zx = (wrap.clientWidth - 4) / Number(svg.getAttribute("width"));
    const zy = (height - 8) / Number(svg.getAttribute("height"));
    return Math.min(zx, zy, MAX);
  }

  function init(box) {
    if (box.dataset.zoomReady) return;
    box.dataset.zoomReady = "1";
    const full = box.querySelector('[data-zoom="full"]');
    if (full) full.hidden = !document.fullscreenEnabled;
    const state = saved.get(key(box));
    if (state && parts(box).svg) {
      setZoom(box, state.zoom);
      const { wrap } = parts(box);
      wrap.scrollLeft = state.left;
      wrap.scrollTop = state.top;
      remember(box);
    } else {
      update(box);
    }
  }

  function initAll() { document.querySelectorAll("[data-flow-diagram]").forEach(init); }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-zoom]");
    const box = button && button.closest("[data-flow-diagram]");
    if (!box) return;
    event.preventDefault();
    const zoom = Number(box.dataset.zoom || 1);
    switch (button.dataset.zoom) {
      case "in": setZoom(box, zoom * STEP); break;
      case "out": setZoom(box, zoom / STEP); break;
      case "reset": setZoom(box, 1); break;
      case "fit": setZoom(box, fitZoom(box)); parts(box).wrap.scrollTo(0, 0); remember(box); break;
      case "full":
        if (document.fullscreenElement) document.exitFullscreen();
        else box.requestFullscreen().catch(() => {});
        break;
    }
  });

  // Ctrl/⌘ + scroll, juga pinch di trackpad (browser mengirimnya sebagai wheel + ctrlKey).
  document.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    const wrap = event.target.closest && event.target.closest(".flow-diagram-wrap");
    const box = wrap && wrap.closest("[data-flow-diagram]");
    if (!box) return;
    event.preventDefault();
    const rect = wrap.getBoundingClientRect();
    const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.002));
    setZoom(box, Number(box.dataset.zoom || 1) * factor, { x: event.clientX - rect.left, y: event.clientY - rect.top });
  }, { passive: false });

  // Geser dengan menyeret (mouse saja; layar sentuh sudah bisa digeser dengan jari).
  let drag = null;
  document.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "mouse" || event.button !== 0) return;
    const wrap = event.target.closest && event.target.closest(".flow-diagram-wrap.is-pannable");
    if (!wrap) return;
    drag = { wrap, x: event.clientX, y: event.clientY, left: wrap.scrollLeft, top: wrap.scrollTop };
    wrap.classList.add("is-panning");
    event.preventDefault();
  });
  document.addEventListener("pointermove", (event) => {
    if (!drag) return;
    drag.wrap.scrollLeft = drag.left - (event.clientX - drag.x);
    drag.wrap.scrollTop = drag.top - (event.clientY - drag.y);
  });
  document.addEventListener("pointerup", () => {
    if (!drag) return;
    drag.wrap.classList.remove("is-panning");
    const box = drag.wrap.closest("[data-flow-diagram]");
    if (box) remember(box);
    drag = null;
  });

  document.addEventListener("scroll", (event) => {
    const wrap = event.target.classList && event.target.classList.contains("flow-diagram-wrap") ? event.target : null;
    const box = wrap && wrap.closest("[data-flow-diagram]");
    if (box && !drag) remember(box);
  }, true);

  document.addEventListener("fullscreenchange", () => {
    document.querySelectorAll("[data-flow-diagram]").forEach((box) => {
      const full = box.querySelector('[data-zoom="full"]');
      if (full) {
        const on = document.fullscreenElement === box;
        full.setAttribute("aria-pressed", on ? "true" : "false");
        full.title = on ? "Exit full screen (Esc)" : "Full screen";
      }
      update(box);
    });
  });
  window.addEventListener("resize", () => document.querySelectorAll("[data-flow-diagram]").forEach(update));

  new MutationObserver(initAll).observe(document.body, { childList: true, subtree: true });
  initAll();
})();
