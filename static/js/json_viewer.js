/*
 * Penampil JSON sederhana (tahap 13): nomor baris, warna key/nilai, garis
 * kedalaman, dan tombol lipat/buka untuk setiap object/array.
 *
 * Pemakaian:
 *   <pre data-json-viewer>{"a": 1}</pre>
 *   <script src="{% static 'js/json_viewer.js' %}"></script>
 *
 * Teks JSON dibaca dari <pre>, lalu <pre> disembunyikan dan diganti tampilan
 * berbaris. <pre> tetap ada di halaman (tombol Copy menyalin isinya), dan bila
 * JavaScript mati atau JSON-nya rusak, <pre> biasa itulah yang terlihat.
 *
 * Semua teks dimasukkan lewat textContent (bukan innerHTML), jadi isi JSON
 * seperti "<script>" tampil sebagai teks dan tidak pernah dijalankan.
 *
 * Halaman lain bisa memakai JsonViewer.create(data) untuk membuat tampilan
 * dari nilai yang sudah di-parse (dipakai editor di halaman impor).
 */
(function () {
  "use strict";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function isContainer(value) {
    return value !== null && typeof value === "object";
  }

  function valueSpan(value) {
    if (value === null) return el("span", "jv-literal", "null");
    switch (typeof value) {
      case "string": return el("span", "jv-string", JSON.stringify(value));
      case "number": return el("span", "jv-number", String(value));
      case "boolean": return el("span", "jv-literal", String(value));
      default: return el("span", "", String(value));
    }
  }

  function build(root, data) {
    let lineNo = 0;

    // Satu baris: [nomor][tombol lipat][garis kedalaman ×depth][isi].
    function row(depth, parts, toggle) {
      const line = el("div", "jv-line");
      line.appendChild(el("span", "jv-num", String(++lineNo)));
      const fold = el("span", "jv-fold");
      if (toggle) fold.appendChild(toggle);
      line.appendChild(fold);
      const guides = el("span", "jv-guides");
      for (let i = 0; i < depth; i++) guides.appendChild(el("span", "jv-guide"));
      line.appendChild(guides);
      const code = el("span", "jv-code");
      parts.forEach((part) => code.appendChild(typeof part === "string" ? document.createTextNode(part) : part));
      line.appendChild(code);
      return line;
    }

    function keyParts(key) {
      return key === undefined ? [] : [el("span", "jv-key", JSON.stringify(key)), ": "];
    }

    // Satu nilai (beserta key-nya bila ada) pada kedalaman `depth`.
    function render(parent, value, key, depth, last) {
      const comma = last ? "" : ",";
      if (!isContainer(value)) {
        parent.appendChild(row(depth, [...keyParts(key), valueSpan(value), comma]));
        return;
      }
      const isArray = Array.isArray(value);
      const entries = isArray ? value.map((v, i) => [undefined, v, i]) : Object.entries(value).map(([k, v], i) => [k, v, i]);
      const [open, close] = isArray ? ["[", "]"] : ["{", "}"];
      if (entries.length === 0) {
        parent.appendChild(row(depth, [...keyParts(key), open + close, comma]));
        return;
      }

      // Blok yang bisa dilipat: baris pembuka, anak-anak, baris penutup.
      const node = el("div", "jv-node");
      const toggle = el("button", "jv-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", "Collapse");
      const count = entries.length + (isArray ? (entries.length === 1 ? " item" : " items") : (entries.length === 1 ? " key" : " keys"));
      const summary = el("span", "jv-summary");
      summary.append("…", close, comma, " ", el("span", "jv-count", count));
      node.appendChild(row(depth, [...keyParts(key), open, summary], toggle));
      const children = el("div", "jv-children");
      entries.forEach(([k, v, i]) => render(children, v, k, depth + 1, i === entries.length - 1));
      node.appendChild(children);
      const closing = row(depth, [close + comma]);
      closing.classList.add("jv-close");
      node.appendChild(closing);
      toggle.addEventListener("click", () => setCollapsed(node, !node.classList.contains("jv-collapsed")));
      parent.appendChild(node);
    }

    render(root, data, undefined, 0, true);
  }

  function setCollapsed(node, collapsed) {
    node.classList.toggle("jv-collapsed", collapsed);
    const toggle = node.querySelector(":scope > .jv-line .jv-toggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand" : "Collapse");
  }

  /* Tampilan lengkap (toolbar Expand/Collapse all + baris-baris) untuk `data`. */
  function create(data) {
    const wrapper = el("div", "jv");
    const toolbar = el("div", "jv-toolbar");
    const expand = el("button", "btn btn-sm btn-link p-0", "Expand all");
    const collapse = el("button", "btn btn-sm btn-link p-0", "Collapse all");
    expand.type = collapse.type = "button";
    toolbar.append(expand, collapse);
    const view = el("div", "jv-view");
    build(view, data);
    wrapper.append(toolbar, view);

    const all = () => view.querySelectorAll(".jv-node");
    expand.addEventListener("click", () => all().forEach((node) => setCollapsed(node, false)));
    // Lipat semua kecuali object terluar, supaya isinya tetap terlihat sebagai ringkasan.
    collapse.addEventListener("click", () => all().forEach((node, i) => setCollapsed(node, i > 0)));
    return wrapper;
  }

  function init(pre) {
    let data;
    try {
      data = JSON.parse(pre.textContent);
    } catch (err) {
      return;   // bukan JSON yang sah: biarkan <pre> biasa
    }
    pre.hidden = true;
    pre.insertAdjacentElement("afterend", create(data));
  }

  window.JsonViewer = { create };
  document.querySelectorAll("[data-json-viewer]").forEach(init);
})();
