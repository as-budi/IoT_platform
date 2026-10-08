/*
 * Peta lokasi memakai Leaflet (pustaka peta) dan ubin peta OpenStreetMap.
 * Dipakai oleh template location_list, location_detail, dan location_form.
 *
 *   LocationMap.show(idElemen, titik, opsi)   -> peta berisi penanda lokasi
 *   LocationMap.picker(idElemen, inputLat, inputLng) -> peta untuk memilih koordinat
 *
 * File ini "static": disajikan apa adanya ke browser, tidak diproses Django.
 */
(function () {
  "use strict";

  // Titik awal bila belum ada koordinat (tengah Indonesia), dan zoom-nya.
  const DEFAULT_CENTER = [-2.5, 118.0];
  const DEFAULT_ZOOM = 5;

  function baseMap(elementId) {
    const map = L.map(elementId);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      // Server peta OpenStreetMap menolak permintaan tanpa header Referer
      // (aturan pemakaian ubin OSM). Django secara bawaan mengirim
      // "Referrer-Policy: same-origin", yang membuat browser TIDAK mengirim
      // Referer ke situs lain -> ubin diblok. Opsi ini mengizinkan browser
      // mengirim asal situs kita (mis. http://127.0.0.1:8000) khusus untuk
      // permintaan ubin, tanpa melonggarkan kebijakan di halaman lain.
      referrerPolicy: "strict-origin-when-cross-origin",
      // Atribusi wajib dicantumkan menurut lisensi OpenStreetMap.
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    return map;
  }

  // Isi popup dibuat lewat DOM (textContent), bukan string HTML, supaya nama
  // lokasi yang mengandung karakter seperti < > tidak bisa menyisipkan kode.
  function popupFor(point) {
    const link = document.createElement("a");
    link.href = point.url;
    link.textContent = point.name;
    return link;
  }

  function show(elementId, points, options) {
    const opts = options || {};
    const map = baseMap(elementId);
    const markers = points.map(function (point) {
      return L.marker([point.lat, point.lng]).addTo(map).bindPopup(popupFor(point));
    });
    if (markers.length === 1) {
      map.setView(markers[0].getLatLng(), opts.zoom || 15);
    } else if (markers.length > 1) {
      // Atur zoom supaya semua penanda terlihat.
      map.fitBounds(L.featureGroup(markers).getBounds().pad(0.2));
    } else {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }
    return map;
  }

  function picker(elementId, latInput, lngInput) {
    const map = baseMap(elementId);
    let marker = null;

    function readInputs() {
      const lat = parseFloat(latInput.value);
      const lng = parseFloat(lngInput.value);
      return Number.isFinite(lat) && Number.isFinite(lng) ? [lat, lng] : null;
    }

    function placeMarker(latlng) {
      if (marker) {
        marker.setLatLng(latlng);
        return;
      }
      marker = L.marker(latlng, { draggable: true }).addTo(map);
      marker.on("dragend", function () { writeInputs(marker.getLatLng()); });
    }

    function writeInputs(latlng) {
      // 6 angka di belakang koma, sama dengan ketelitian kolom di database.
      latInput.value = latlng.lat.toFixed(6);
      lngInput.value = latlng.lng.toFixed(6);
    }

    const initial = readInputs();
    if (initial) {
      placeMarker(initial);
      map.setView(initial, 16);
    } else {
      map.setView(DEFAULT_CENTER, DEFAULT_ZOOM);
    }

    // Klik peta -> isi kolom latitude/longitude.
    map.on("click", function (event) {
      placeMarker(event.latlng);
      writeInputs(event.latlng);
    });
    // Ketik koordinat -> penanda ikut pindah.
    [latInput, lngInput].forEach(function (input) {
      input.addEventListener("change", function () {
        const value = readInputs();
        if (value) {
          placeMarker(value);
          map.panTo(value);
        }
      });
    });
    return map;
  }

  window.LocationMap = { show: show, picker: picker };
})();
