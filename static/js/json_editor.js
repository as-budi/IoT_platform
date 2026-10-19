/*
 * Editor JSON untuk halaman impor aturan (tahap 13).
 *
 * <textarea data-json-editor> diberi:
 *   - nomor baris di kiri (ikut menggulir bersama teks),
 *   - pewarnaan key/nilai seperti penampil JSON,
 *   - pemeriksaan langsung: "Valid JSON · FLOW rule · 7 nodes" atau letak error,
 *   - tab Preview: tampilan berlipat dari json_viewer.js (JsonViewer.create),
 *   - tombol Format: merapikan indentasi (2 spasi per tingkat), berguna untuk
 *     JSON yang ditempel dalam satu baris panjang; file hasil Export sudah rapi,
 *     jadi untuk file itu tombol ini menjawab "Already formatted",
 *   - muat file .json ke editor (pilih file atau seret ke editor).
 *
 * Cara pewarnaan: textarea tetap textarea biasa (teksnya dibuat transparan),
 * dan di belakangnya ada <pre> berisi teks yang sama yang sudah diwarnai.
 * Keduanya memakai font, ukuran, dan padding yang sama sehingga hurufnya
 * bertumpuk tepat; posisi gulir <pre> disamakan dengan textarea.
 * Yang dikirim ke server tetap isi textarea, jadi tanpa JavaScript form
 * tetap bekerja seperti biasa.
 */
(function () {
  "use strict";

  const TOKEN = /("(?:\\.|[^"\\\n])*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b/g;

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /* Teks JSON (boleh belum sah) -> HTML berwarna. Semua teks di-escape dulu. */
  function highlight(text) {
    let html = "";
    let last = 0;
    text.replace(TOKEN, (match, str, colon, num, literal, offset) => {
      html += escapeHtml(text.slice(last, offset));
      if (str !== undefined) {
        html += `<span class="${colon ? "jv-key" : "jv-string"}">${escapeHtml(str)}</span>`;
        if (colon) html += escapeHtml(colon);
      } else if (num !== undefined) {
        html += `<span class="jv-number">${num}</span>`;
      } else {
        html += `<span class="jv-literal">${literal}</span>`;
      }
      last = offset + match.length;
      return match;
    });
    return html + escapeHtml(text.slice(last)) + "\n";   // "\n": baris kosong terakhir tetap punya tinggi
  }

  /* Letak error JSON.parse sebagai {line, column}. Pesan error berbeda per browser. */
  function errorPosition(error, text) {
    const message = String(error.message);
    let match = message.match(/line (\d+) column (\d+)/i);
    if (match) return { line: Number(match[1]), column: Number(match[2]) };
    match = message.match(/position (\d+)/i);
    if (match) {
      const before = text.slice(0, Number(match[1])).split("\n");
      return { line: before.length, column: before[before.length - 1].length + 1 };
    }
    return null;
  }

  function cleanMessage(error) {
    return String(error.message)
      .replace(/^JSON\.parse: /, "")
      .replace(/ in JSON at position \d+( \(line \d+ column \d+\))?/, "")
      .replace(/ at line \d+ column \d+ of the JSON data/, "");
  }

  /* Ringkasan isi yang sudah sah, mis. "FLOW rule · 7 nodes, 7 links". */
  function describe(data) {
    if (!data || typeof data !== "object" || Array.isArray(data)) return "not an object";
    const parts = [];
    if (data.model) parts.push(`${data.model} rule`);
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    if (data.flow && Array.isArray(data.flow.nodes)) {
      parts.push(`${plural(data.flow.nodes.length, "node")}, ${plural((data.flow.links || []).length, "link")}`);
    } else if (data.eca) {
      const count = (groups) => (Array.isArray(groups) ? groups.flat().length : 0);
      parts.push(`${plural(count(data.eca.when), "condition")}, ${plural((data.actions || []).length, "action")}`);
    }
    return parts.join(" · ");
  }

  /* Rapikan indentasi JSON yang SAH tanpa mengubah isinya.
     Sengaja tidak memakai JSON.stringify(JSON.parse(...)): cara itu mengubah
     angka 30.0 menjadi 30. Di sini hanya spasi di luar string yang diatur ulang,
     dengan gaya yang sama dengan file Export (json.dumps(indent=2) di Python). */
  function formatJson(text, indent = "  ") {
    let out = "";
    let depth = 0;
    let inString = false;
    const newline = () => "\n" + indent.repeat(depth);
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inString) {
        out += ch;
        if (ch === "\\") { out += text[++i]; }
        else if (ch === '"') { inString = false; }
        continue;
      }
      if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") continue;
      if (ch === '"') { inString = true; out += ch; continue; }
      if (ch === "{" || ch === "[") {
        // Object/array kosong tetap ditulis {} atau [].
        let j = i + 1;
        while (/\s/.test(text[j] || "")) j++;
        if (text[j] === (ch === "{" ? "}" : "]")) { out += ch + text[j]; i = j; continue; }
        depth++;
        out += ch + newline();
        continue;
      }
      if (ch === "}" || ch === "]") { depth--; out += newline() + ch; continue; }
      if (ch === ",") { out += "," + newline(); continue; }
      if (ch === ":") { out += ": "; continue; }
      out += ch;
    }
    return out;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function init(textarea) {
    const form = textarea.form;
    const fileInput = form && form.querySelector('input[type="file"][data-json-file]');

    // --- Susun elemen editor di sekitar textarea ---
    const root = el("div", "je");
    const bar = el("div", "je-bar");
    const tabs = el("div", "je-tabs btn-group btn-group-sm");
    const editTab = el("button", "btn btn-outline-secondary active", "Edit");
    const previewTab = el("button", "btn btn-outline-secondary", "Preview");
    [editTab, previewTab].forEach((b) => { b.type = "button"; });
    tabs.append(editTab, previewTab);
    // Tombol Format: ikon kurung kurawal + teks, dengan keterangan di sampingnya.
    const formatButton = el("button", "btn btn-sm btn-outline-primary je-format");
    formatButton.type = "button";
    formatButton.innerHTML =
      '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M8 4H7a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2h1"/>' +
      '<path d="M16 4h1a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v4a2 2 0 0 1-2 2h-1"/></svg>' +
      "<span>Format</span>";
    formatButton.title = "Re-indent the JSON with 2 spaces per level. Useful after pasting JSON that is all on one line.";
    const formatHint = el("span", "je-hint small text-muted", "Tidy up indentation (2 spaces)");
    const status = el("span", "je-status small");
    status.setAttribute("aria-live", "polite");
    bar.append(tabs, formatButton, formatHint, status);

    const editPane = el("div", "je-edit");
    const gutter = el("div", "je-gutter");
    gutter.setAttribute("aria-hidden", "true");
    const area = el("div", "je-area");
    const painted = el("pre", "je-highlight");
    painted.setAttribute("aria-hidden", "true");
    const previewPane = el("div", "je-preview");
    previewPane.hidden = true;

    textarea.parentNode.insertBefore(root, textarea);
    area.append(painted, textarea);
    editPane.append(gutter, area);
    root.append(bar, editPane, previewPane);
    textarea.classList.remove("form-control", "small");
    textarea.classList.add("je-input");
    textarea.setAttribute("wrap", "off");

    let parsed = null;
    let parseError = null;

    function syncScroll() {
      painted.scrollTop = textarea.scrollTop;
      painted.scrollLeft = textarea.scrollLeft;
      gutter.scrollTop = textarea.scrollTop;
    }

    function update() {
      const text = textarea.value;
      painted.innerHTML = highlight(text);
      parsed = null;
      parseError = null;
      let errorLine = 0;
      if (text.trim()) {
        try {
          parsed = JSON.parse(text);
        } catch (err) {
          const where = errorPosition(err, text);
          errorLine = where ? where.line : 0;
          parseError = (where ? `Line ${where.line}, column ${where.column}: ` : "") + cleanMessage(err);
        }
      }
      const count = text.split("\n").length;
      const numbers = [];
      for (let n = 1; n <= count; n++) {
        numbers.push(n === errorLine ? `<div class="je-error-line">${n}</div>` : `<div>${n}</div>`);
      }
      gutter.innerHTML = numbers.join("") + "<div>&nbsp;</div>";
      status.className = "je-status small " + (parseError ? "text-danger" : "text-muted");
      status.textContent = !text.trim() ? "" : parseError || `Valid JSON · ${describe(parsed)}`;
      formatButton.disabled = parsed === null;
      formatButton.title = parsed === null
        ? "Fix the JSON first: only valid JSON can be formatted."
        : "Re-indent the JSON with 2 spaces per level. Useful after pasting JSON that is all on one line.";
      syncScroll();
    }

    let pending = false;
    function scheduleUpdate() {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => { pending = false; update(); });
    }

    function showPreview(show) {
      editTab.classList.toggle("active", !show);
      previewTab.classList.toggle("active", show);
      editPane.hidden = show;
      previewPane.hidden = !show;
      if (!show) { textarea.focus(); return; }
      update();
      previewPane.replaceChildren();
      if (parsed !== null && window.JsonViewer) {
        previewPane.appendChild(window.JsonViewer.create(parsed));
      } else {
        previewPane.appendChild(el("div", "je-preview-empty text-muted small",
          parseError ? `Fix the JSON first. ${parseError}` : "Paste a JSON rule or choose a file."));
      }
    }

    function load(file) {
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        textarea.value = String(reader.result);
        // Isi file sekarang ada di editor dan bisa diubah; yang dikirim adalah
        // teks editor, jadi pilihan file dikosongkan (server memakai file bila ada).
        if (fileInput) fileInput.value = "";
        showPreview(false);
        update();
      };
      reader.readAsText(file);
    }

    textarea.addEventListener("input", scheduleUpdate);
    textarea.addEventListener("scroll", syncScroll);
    editTab.addEventListener("click", () => showPreview(false));
    previewTab.addEventListener("click", () => showPreview(true));

    // Keterangan di samping tombol berganti sebentar sebagai tanda tombol bekerja.
    let hintTimer = null;
    function flashHint(text, className) {
      formatHint.textContent = text;
      formatHint.className = `je-hint small ${className}`;
      clearTimeout(hintTimer);
      hintTimer = setTimeout(() => {
        formatHint.textContent = "Tidy up indentation (2 spaces)";
        formatHint.className = "je-hint small text-muted";
      }, 2500);
    }

    formatButton.addEventListener("click", () => {
      if (parsed === null) return;
      const tidy = formatJson(textarea.value);
      if (tidy === textarea.value) {
        flashHint("✓ Already formatted", "text-success");
        return;
      }
      const before = textarea.value.split("\n").length;
      textarea.value = tidy;
      update();
      flashHint(`✓ Formatted: ${before} → ${tidy.split("\n").length} lines`, "text-success");
      textarea.focus();
    });
    if (fileInput) fileInput.addEventListener("change", () => load(fileInput.files[0]));
    editPane.addEventListener("dragover", (event) => { event.preventDefault(); editPane.classList.add("je-drop"); });
    editPane.addEventListener("dragleave", () => editPane.classList.remove("je-drop"));
    editPane.addEventListener("drop", (event) => {
      event.preventDefault();
      editPane.classList.remove("je-drop");
      load(event.dataTransfer.files[0]);
    });

    update();
  }

  document.querySelectorAll("textarea[data-json-editor]").forEach(init);
})();
