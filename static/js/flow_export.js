/*
 * Ekspor diagram rule chain (SVG buatan rules/flow_diagram.py) menjadi file PNG.
 *
 * Tombol: <button data-flow-export data-filename="rule-chain-x" data-title="X">
 * di dalam rules/includes/flow_diagram.html. Pendengar klik dipasang di
 * `document` (event delegation), sehingga tombol tetap berfungsi setelah kotak
 * "Last run" diganti HTML baru oleh pembaruan langsung.
 *
 * Langkah:
 * 1. Salin <svg>. Gaya diagram berasal dari static/css/app.css (warna lewat
 *    variabel CSS --app-*, --flow-*). SVG yang digambar sebagai gambar TIDAK
 *    membawa CSS halaman, jadi gaya hasil akhir setiap elemen (getComputedStyle)
 *    ditulis langsung ke salinannya.
 * 2. Salinan diubah menjadi teks (XMLSerializer) lalu dimuat sebagai <img>.
 * 3. <img> digambar ke <canvas> 2× lebih besar (tajam di layar retina), bersama
 *    judul aturan dan keterangan warna kategori node.
 * 4. canvas.toBlob() -> unduhan "<nama>.png".
 * Semua terjadi di browser; server tidak ikut. Warna mengikuti tema yang sedang
 * dipakai (terang/gelap).
 */
(function () {
  "use strict";

  const SCALE = 2;
  const PAD = 20;
  const TITLE_H = 34;
  const LEGEND_H = 28;
  // Properti yang menentukan tampilan diagram.
  const PROPS = [
    "fill", "fill-opacity", "stroke", "stroke-width", "stroke-dasharray", "stroke-opacity",
    "opacity", "font-family", "font-size", "font-weight", "font-style",
  ];
  const LEGEND = [
    ["Filter", "--flow-filter"], ["Enrichment", "--flow-enrichment"],
    ["Transformation", "--flow-transformation"], ["Action", "--flow-action"],
  ];

  function inlineStyles(source, target) {
    const style = getComputedStyle(source);
    target.setAttribute("style", PROPS.map((p) => `${p}:${style.getPropertyValue(p)}`).join(";"));
    target.removeAttribute("class");
    for (let i = 0; i < source.children.length; i++) inlineStyles(source.children[i], target.children[i]);
  }

  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  function loadImage(svg) {
    return new Promise((resolve, reject) => {
      const text = new XMLSerializer().serializeToString(svg);
      const url = URL.createObjectURL(new Blob([text], { type: "image/svg+xml;charset=utf-8" }));
      const img = new Image();
      img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
      img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("The diagram could not be drawn.")); };
      img.src = url;
    });
  }

  async function exportPng(button) {
    const box = button.closest("[data-flow-diagram]");
    const svg = box && box.querySelector("svg.flow-diagram");
    if (!svg) return;
    const width = Number(svg.getAttribute("width"));
    const height = Number(svg.getAttribute("height"));

    const copy = svg.cloneNode(true);
    copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    inlineStyles(svg, copy);
    copy.removeAttribute("style");
    const img = await loadImage(copy);

    const bodyStyle = getComputedStyle(document.body);
    const background = cssVar("--app-surface", "#ffffff");
    const text = bodyStyle.color;
    const muted = cssVar("--app-muted", "#6c757d");
    const font = bodyStyle.fontFamily;

    const canvas = document.createElement("canvas");
    const outW = Math.max(width, 520) + PAD * 2;
    const outH = height + TITLE_H + LEGEND_H + PAD * 2;
    canvas.width = outW * SCALE;
    canvas.height = outH * SCALE;
    const ctx = canvas.getContext("2d");
    ctx.scale(SCALE, SCALE);
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, outW, outH);

    // Judul: nama aturan.
    ctx.fillStyle = text;
    ctx.font = `600 16px ${font}`;
    ctx.textBaseline = "middle";
    ctx.fillText(button.dataset.title || "Rule chain", PAD, PAD + 10);

    ctx.drawImage(img, PAD, PAD + TITLE_H, width, height);

    // Keterangan warna kategori node.
    let x = PAD;
    const y = PAD + TITLE_H + height + LEGEND_H / 2;
    ctx.font = `12px ${font}`;
    LEGEND.forEach(([label, variable]) => {
      ctx.fillStyle = cssVar(variable, "#888");
      ctx.fillRect(x, y - 5, 10, 10);
      ctx.fillStyle = muted;
      ctx.fillText(label, x + 15, y);
      x += 15 + ctx.measureText(label).width + 18;
    });

    canvas.toBlob((blob) => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = (button.dataset.filename || "rule-chain") + ".png";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    }, "image/png");
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-flow-export]");
    if (!button) return;
    event.preventDefault();
    exportPng(button).catch((err) => { console.error(err); alert(err.message); });
  });
})();
