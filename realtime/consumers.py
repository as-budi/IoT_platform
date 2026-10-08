"""
Consumer = "view" untuk WebSocket.

View biasa: satu request masuk -> satu response keluar -> selesai.
Consumer: satu koneksi yang TERUS terbuka. Ada tiga momen penting:
    connect()       browser membuka ws://.../ws/devices/3/
    device_event()  ada kabar dari group "device-3" -> diteruskan ke browser
    disconnect()    tab ditutup atau koneksi putus

Koneksi ini satu arah secara logika: server mengirim kabar, browser tidak
mengirim perintah lewat sini (perintah tetap lewat form biasa yang
dilindungi CSRF).
"""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .events import group_name

# Kode penutupan khusus aplikasi (rentang 4000–4999 bebas dipakai).
CLOSE_NOT_LOGGED_IN = 4401
CLOSE_FORBIDDEN = 4403


class DeviceConsumer(AsyncJsonWebsocketConsumer):
    group = None

    async def connect(self):
        # scope = "request"-nya WebSocket. AuthMiddlewareStack (config/asgi.py)
        # mengisi scope["user"] dari cookie sesi, sama seperti request.user.
        user = self.scope["user"]
        device_id = self.scope["url_route"]["kwargs"]["pk"]

        if not user.is_authenticated:
            await self.reject(CLOSE_NOT_LOGGED_IN)
            return
        if not await self.can_view(user, device_id):
            await self.reject(CLOSE_FORBIDDEN)
            return

        # Bergabung ke group SEBELUM accept(), supaya tidak ada kabar yang
        # terlewat di antara "tersambung" dan "mulai mendengarkan".
        self.group = group_name(device_id)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def reject(self, code):
        # Koneksi diterima dulu, lalu langsung ditutup dengan kode yang jelas,
        # supaya JavaScript tahu alasannya (bukan sekadar "gagal tersambung").
        await self.accept()
        await self.close(code=code)

    @database_sync_to_async
    def can_view(self, user, device_id):
        # ORM Django bersifat sinkron; database_sync_to_async menjalankannya
        # di thread terpisah agar tidak menghentikan event loop.
        # Aturan kepemilikan yang SAMA dengan halaman web: visible_devices().
        from devices.views import visible_devices

        return visible_devices(user).filter(pk=device_id).exists()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive_json(self, content, **kwargs):
        """Pesan dari browser diabaikan: saluran ini hanya untuk kabar dari server."""

    async def device_event(self, message):
        """Dipanggil untuk setiap pesan {"type": "device.event"} di group device ini."""
        await self.send_json(message["event"])
