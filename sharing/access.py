"""
SATU tempat aturan akses device (tahap 19).

Sampai tahap 18 aturannya satu baris: Admin melihat semua, pengguna lain hanya
device miliknya. Sekarang device juga bisa DIBAGIKAN (sharing/models.py), dengan
tingkat akses berjenjang:

    VIEWER < OPERATOR < EDITOR < OWNER

    OWNER   = pemilik device, atau Admin (role Admin / superuser) untuk device apa pun.

Fungsi di sini dipakai semua bagian platform:

    devices_for(user, level)   queryset device yang boleh disentuh `user` minimal pada `level`
                               (halaman, dashboard, WebSocket, pilihan form aturan, rule engine)
    role_for(user, device)     peran `user` pada satu device (atau None)
    can(user, device, level)   True/False
    require(user, device, level)  PermissionDenied (403) bila tidak cukup

Peran yang didapat dari beberapa jalan (langsung + lewat grup) digabung: yang
TERTINGGI berlaku.
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from .models import DeviceShare, Role

VIEWER, OPERATOR, EDITOR = Role.VIEWER, Role.OPERATOR, Role.EDITOR
OWNER = "OWNER"

RANK = {VIEWER: 1, OPERATOR: 2, EDITOR: 3, OWNER: 4}
LABEL = {VIEWER: "Viewer", OPERATOR: "Operator", EDITOR: "Editor", OWNER: "Owner"}
# Kalimat untuk pesan 403, mis. "You need Operator access to this device to control it."
NEEDED_FOR = {OPERATOR: "control it", EDITOR: "change it", OWNER: "do this"}


def roles_at_least(level):
    """Peran share yang memenuhi `level`, mis. OPERATOR -> [OPERATOR, EDITOR]."""
    return [role for role in (VIEWER, OPERATOR, EDITOR) if RANK[role] >= RANK[level]]


def shares_of(user):
    """Share yang berlaku untuk `user`: langsung ke dia, atau ke grup yang ia ikuti."""
    return DeviceShare.objects.filter(Q(user=user) | Q(group__members__user=user))


def devices_for(user, level=VIEWER, *, shared=True):
    """Device yang boleh disentuh `user` minimal pada `level`.

    shared=False: hanya device MILIK sendiri (Admin tetap semua), mis. untuk
    rule chain "any of my devices".

    Bentuk SQL (pengguna biasa, level OPERATOR):

        SELECT ... FROM devices_device
        WHERE owner_id = 5
           OR id IN (SELECT s.device_id FROM sharing_deviceshare s
                     LEFT JOIN sharing_sharegroupmember m ON m.group_id = s.group_id
                     WHERE (s.user_id = 5 OR m.user_id = 5) AND s.role IN ('OPERATOR', 'EDITOR'))

    Subquery `id IN (...)` dipakai (bukan JOIN langsung) supaya satu device yang
    dibagikan lewat dua jalan tidak muncul dua kali.
    """
    from devices.models import Device

    devices = Device.objects.all()
    if user.is_admin:
        return devices
    allowed = Q(owner=user)
    if shared and level != OWNER:
        allowed |= Q(pk__in=shares_of(user).filter(role__in=roles_at_least(level)).values("device_id"))
    return devices.filter(allowed)


def roles(user, devices):
    """{device_id: peran} untuk banyak device sekaligus (2 query). Tanpa akses = tidak ada di dict."""
    ids = {getattr(d, "pk", d) for d in devices}
    if not ids:
        return {}
    if user.is_admin:
        return dict.fromkeys(ids, OWNER)
    from devices.models import Device

    result = dict.fromkeys(Device.objects.filter(pk__in=ids, owner=user).values_list("pk", flat=True), OWNER)
    rest = ids - set(result)
    if rest:
        for device_id, role in shares_of(user).filter(device_id__in=rest).values_list("device_id", "role"):
            if RANK[role] > RANK.get(result.get(device_id), 0):
                result[device_id] = role
    return result


def role_for(user, device, cache=None):
    """Peran `user` pada `device` (objek atau id): OWNER/EDITOR/OPERATOR/VIEWER, atau None.

    `cache` (dict, opsional) menyimpan hasil per device id, mis. satu dict per request
    supaya template yang bertanya berkali-kali tidak mengulang query.
    """
    if user is None or not user.is_authenticated:
        return None
    device_id = getattr(device, "pk", device)
    if cache is not None and device_id in cache:
        return cache[device_id]
    owner_id = getattr(device, "owner_id", None)
    if user.is_admin or (owner_id is not None and owner_id == user.pk):
        role = OWNER
    else:
        role = roles(user, [device_id]).get(device_id)
    if cache is not None:
        cache[device_id] = role
    return role


def has(role, level):
    return role is not None and RANK[role] >= RANK[level]


def can(user, device, level, cache=None):
    return has(role_for(user, device, cache), level)


def require(user, device, level):
    """Hentikan request dengan 403 bila peran `user` pada `device` kurang dari `level`.

    Device yang sama sekali tidak terlihat sudah 404 di queryset view; fungsi ini untuk
    device yang TERLIHAT tetapi perannya tidak cukup (mis. Viewer menekan tombol ON).
    """
    role = role_for(user, device)
    if not has(role, level):
        raise PermissionDenied(
            f"You need {LABEL[level]} access to this device to {NEEDED_FOR.get(level, 'do this')} "
            f"(your access: {LABEL.get(role, 'none')})."
        )
    return role


def request_cache(request):
    """Dict cache peran untuk satu request (dipakai template tag dan view)."""
    if request is None:
        return None
    if not hasattr(request, "_device_roles"):
        request._device_roles = {}
    return request._device_roles


def remember(request, devices):
    """Isi cache request untuk banyak device sekaligus, mis. sebelum merender dashboard."""
    cache = request_cache(request)
    missing = [d for d in devices if getattr(d, "pk", d) not in cache]
    if missing:
        found = roles(request.user, missing)
        for device in missing:
            device_id = getattr(device, "pk", device)
            cache[device_id] = found.get(device_id)
    return cache
