/*
 * Halaman "Delete data" sebuah variabel (devices/variable_data_confirm_delete.html).
 *
 * Tombol "Download CSV and delete":
 *   1. mengunduh CSV seluruh data variabel (sampai baris terakhir saat halaman dibuka),
 *   2. menyimpannya sebagai file lewat tautan sementara (blob),
 *   3. BARU mengirim form penghapusan (POST), lalu pindah ke halaman variabel.
 * Bila unduhan gagal, form tidak dikirim: tidak ada data yang terhapus tanpa salinannya.
 * Tanpa JavaScript, form terkirim biasa (halaman menampilkan tautan CSV untuk diunduh dulu).
 */
(function () {
  "use strict";

  const form = document.getElementById("delete-data-form");
  if (!form) return;
  const status = document.getElementById("delete-data-status");
  const button = form.querySelector("button[type=submit]");

  function say(text, isError) {
    status.textContent = text;
    status.classList.toggle("text-danger", Boolean(isError));
  }

  function saveFile(blob, name) {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Beri browser waktu memulai penyimpanan sebelum alamat blob dilepas.
    setTimeout(() => URL.revokeObjectURL(link.href), 60000);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    button.disabled = true;
    try {
      say("Downloading CSV…");
      const download = await fetch(form.dataset.csvUrl, { credentials: "same-origin" });
      if (!download.ok) throw new Error(`the download failed (HTTP ${download.status})`);
      saveFile(await download.blob(), form.dataset.filename);

      say("CSV downloaded. Deleting data…");
      const answer = await fetch(form.action, {
        method: "POST",
        body: new FormData(form),           // berisi csrfmiddlewaretoken dan up_to
        credentials: "same-origin",
        headers: { "X-Requested-With": "fetch" },
      });
      const data = await answer.json().catch(() => ({}));
      if (!answer.ok) throw new Error(`deleting failed (HTTP ${answer.status}); the CSV was downloaded`);
      window.location.href = data.redirect || "/";
    } catch (err) {
      say(`Nothing was deleted: ${err.message}.`, true);
      button.disabled = false;
    }
  });
})();
