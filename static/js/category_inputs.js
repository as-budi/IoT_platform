/*
 * Isian nilai untuk variabel ON/OFF dan kategori di form aturan.
 *
 * <select data-category-target="threshold"> (variabel kondisi) atau "value" (aktuator
 * aksi): setiap <option> variabel ON/OFF atau kategori membawa data-labels, mis.
 * ["closed","open","locked"]. Bila variabel seperti itu dipilih, isian angka pasangannya
 * (nama field yang sama dengan akhiran berbeda, mis. when-0-variable -> when-0-threshold)
 * disembunyikan dan diganti <select> berisi label; kode yang dipilih (0, 1, 2, ...) tetap
 * dikirim lewat isian angka. Variabel angka: isian angka biasa tampil lagi.
 */
(function () {
  "use strict";

  function pairedInput(select) {
    if (!select.form || !select.name) return null;
    const input = select.form.elements[select.name.replace(/[^-]+$/, select.dataset.categoryTarget)];
    return input instanceof HTMLInputElement ? input : null;
  }

  function sync(select) {
    const input = pairedInput(select);
    if (!input) return;
    const option = select.selectedOptions[0];
    const labels = option && option.dataset.labels ? JSON.parse(option.dataset.labels) : [];
    let picker = input.categoryPicker;
    if (!labels.length) {
      if (picker) { picker.remove(); input.categoryPicker = null; }
      input.hidden = false;
      return;
    }
    if (!picker) {
      picker = document.createElement("select");
      picker.className = "form-select" + (input.classList.contains("form-control-sm") ? " form-select-sm" : "");
      picker.setAttribute("aria-label", "Value");
      picker.addEventListener("change", () => {
        input.value = picker.value;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      input.insertAdjacentElement("afterend", picker);
      input.categoryPicker = picker;
    }
    picker.innerHTML = "";
    labels.forEach((label, index) => picker.add(new Option(label, String(index))));
    const current = Number(input.value);
    if (input.value === "" || !Number.isInteger(current) || current < 0 || current >= labels.length) {
      input.value = "0";
    }
    picker.value = String(Number(input.value));
    input.hidden = true;
  }

  const init = (root) => root.querySelectorAll("select[data-category-target]").forEach(sync);

  document.addEventListener("change", (event) => {
    if (event.target.matches && event.target.matches("select[data-category-target]")) sync(event.target);
  });
  // Baris kondisi/aksi yang ditambahkan JavaScript halaman.
  new MutationObserver((mutations) => {
    mutations.forEach((mutation) => mutation.addedNodes.forEach((node) => {
      if (node.nodeType !== 1) return;
      if (node.matches("select[data-category-target]")) sync(node);
      init(node);
    }));
  }).observe(document.body, { childList: true, subtree: true });
  init(document);
})();
