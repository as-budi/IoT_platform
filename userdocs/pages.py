"""
Daftar halaman dokumentasi pengguna (tahap 25), urut seperti di menu dokumentasi.

Setiap halaman adalah satu template fragmen HTML di
userdocs/templates/userdocs/pages/<slug>.html. Fragmen itu dirender dua kali:
  - untuk ditampilkan (userdocs/views.py: page), dan
  - untuk indeks pencarian (userdocs/search.py), tanpa pengguna dan tanpa device.
Karena itu fragmen hanya boleh bergantung pada konteks dari userdocs/context.py.

Menambah halaman: buat fragmennya, lalu tambahkan satu baris Page di PAGES.
Judul bagian di dalam fragmen memakai <h2 id="..."> / <h3 id="...">: id itulah
tujuan tautan dan hasil pencarian.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    section: str
    summary: str

    @property
    def template(self):
        return f"userdocs/pages/{self.slug}.html"


PAGES = [
    Page("getting-started", "Getting started", "Basics",
         "What the platform does, the main ideas, and your first device in a few steps."),
    Page("account", "Your account", "Basics",
         "Signing up, logging in, passwords, email address and profile."),
    Page("devices", "Devices and locations", "Devices and data",
         "Adding devices, access tokens, online status and locations on a map."),
    Page("variables", "Variables", "Devices and data",
         "Sensors and actuators of a device: keys, units, value types and limits."),
    Page("dashboard", "Dashboard, charts and history", "Devices and data",
         "Following data live, charts with time ranges and zoom, and downloading data."),
    Page("control", "Controlling devices", "Devices and data",
         "Switching actuators with requested states, and sending commands."),
    Page("cameras", "Cameras (ESP32-CAM)", "Devices and data",
         "Snapshots from an ESP32-CAM: the Camera card, Capture now, the gallery and how long images are kept."),
    Page("connect-esp32", "Connect an ESP32", "Connecting devices",
         "Step-by-step guide with ready-made firmware templates for HTTP, MQTT and CoAP."),
    Page("device-api", "Device API reference", "Connecting devices",
         "Every address, topic and payload a device can use, with examples to try from a computer."),
    Page("security", "Security and certificates", "Connecting devices",
         "Access tokens, encryption, and the root certificate an ESP32 needs."),
    Page("firmware-updates", "Firmware updates and MAC address", "Connecting devices",
         "Updating devices over the air, and the firmware report with version, board and MAC address."),
    Page("rules", "Rules overview", "Automation",
         "Rule models, the rule list, execution log, import and export."),
    Page("rules-eca", "ECA rules", "Automation",
         "When an event arrives, if a condition holds, then run actions; with hysteresis and timing."),
    Page("rule-chains", "Rule chains", "Automation",
         "Messages that travel through filter, enrichment, transformation and action nodes."),
    Page("state-machines", "State machines", "Automation",
         "Devices that move between named states, with actions on entering and leaving."),
    Page("schedules", "Schedules", "Automation",
         "Rules that run at set times, every N minutes, or once."),
    Page("alarms-notifications", "Alarms and notifications", "Automation",
         "Alarm life cycle, email and Telegram messages from rules."),
    Page("sharing", "Sharing devices", "Working together",
         "Viewer, Operator and Editor roles, sharing with users and groups."),
    Page("activity-log", "Activity log", "Working together",
         "What happened to your devices, rules and account, with filters."),
    Page("mqtt-sandbox", "MQTT sandbox", "Apps and integrations",
         "Practising MQTT on the platform broker without a device."),
    Page("connected-apps", "Connected apps and API", "Apps and integrations",
         "Signing in to other apps with your account, and the API for app developers."),
    Page("troubleshooting", "Troubleshooting", "Help",
         "Common problems and how to solve them."),
    Page("administration", "For administrators", "Help",
         "Managing users and what administrators can see."),
]

# Micro rule (tahap 26): halaman ini hanya tampil untuk pengguna yang boleh melihat fiturnya
# (settings.MICRO_RULES: all / admin / off), lihat visible_pages().
EDGE_PAGE = Page("edge-rules", "Running rules on devices", "Automation",
                 "Preview: how a rule is split between your devices so it keeps working without the platform.")
_index = [page.slug for page in PAGES].index("rules-eca") + 1
PAGES = PAGES[:_index] + [EDGE_PAGE] + PAGES[_index:]
HIDDEN_UNLESS_EDGE = {EDGE_PAGE.slug}

BY_SLUG = {page.slug: page for page in PAGES}


def visible_pages(user=None):
    """PAGES yang boleh dilihat `user` (halaman micro rule mengikuti MICRO_RULES)."""
    from rules.edge.limits import visible_to

    show_edge = visible_to(user)
    return [page for page in PAGES if show_edge or page.slug not in HIDDEN_UNLESS_EDGE]


def is_visible(slug, user=None):
    return slug in BY_SLUG and any(page.slug == slug for page in visible_pages(user))


def sections(user=None):
    """[(nama bagian, [Page, ...]), ...] dalam urutan PAGES, hanya halaman yang boleh dilihat."""
    result = []
    for page in visible_pages(user):
        if not result or result[-1][0] != page.section:
            result.append((page.section, []))
        result[-1][1].append(page)
    return result


def neighbours(slug, user=None):
    """(halaman sebelumnya, halaman berikutnya) untuk tautan di bawah halaman."""
    pages = visible_pages(user)
    index = [page.slug for page in pages].index(slug)
    previous = pages[index - 1] if index > 0 else None
    following = pages[index + 1] if index + 1 < len(pages) else None
    return previous, following
