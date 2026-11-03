"""
Pencarian dokumentasi (tahap 25).

Halaman dokumentasi berjumlah puluhan, jadi tidak perlu mesin pencari terpisah:
setiap fragmen dirender sekali, dipotong per judul bagian (<h2>/<h3> ber-id), lalu
disimpan di memori proses. Kueri dicocokkan dengan judul halaman, judul bagian, dan
isi bagian; hasilnya diberi skor dan cuplikan dengan kata yang disorot.

Indeks bergantung pada alamat server (contoh perintah memuat host), jadi kuncinya
adalah alamat web yang dilihat device. Indeks dibangun ulang saat proses dimulai
ulang, misalnya setelah deploy.
"""
import html
import re
from dataclasses import dataclass

from django.template.loader import render_to_string
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .pages import PAGES

HEADING_RE = re.compile(r'<h([23])[^>]*\bid="([^"]+)"[^>]*>(.*?)</h\1>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[\w.:/-]+", re.U)
MAX_RESULTS = 30
SNIPPET = 180

_index = {}


@dataclass
class Section:
    page: object
    anchor: str          # "" = awal halaman, sebelum judul bagian pertama
    heading: str
    text: str


def plain(fragment):
    """HTML -> teks biasa satu baris."""
    return SPACE_RE.sub(" ", html.unescape(TAG_RE.sub(" ", fragment))).strip()


def split_sections(page, rendered):
    """Potong satu halaman yang sudah dirender menjadi bagian-bagian per judul."""
    sections, start, anchor, heading = [], 0, "", page.title
    for match in HEADING_RE.finditer(rendered):
        sections.append(Section(page, anchor, heading, plain(rendered[start:match.start()])))
        anchor, heading, start = match.group(2), plain(match.group(3)), match.end()
    sections.append(Section(page, anchor, heading, plain(rendered[start:])))
    return [section for section in sections if section.text or section.anchor]


def toc(rendered):
    """Daftar isi sebuah halaman: [(level, id, judul), ...]."""
    return [(int(level), anchor, plain(title)) for level, anchor, title in HEADING_RE.findall(rendered)]


def build_index(context):
    """Render setiap bab sekali: bagian-bagian untuk pencarian dan daftar isi untuk menu."""
    sections, tocs = [], {}
    for page in PAGES:
        rendered = render_to_string(page.template, context)
        sections += split_sections(page, rendered)
        tocs[page.slug] = [(anchor, title) for level, anchor, title in toc(rendered) if level == 2]
    return {"sections": sections, "tocs": tocs}


def get_index(key, context):
    if key not in _index:
        _index.clear()   # satu server = satu alamat; jangan menimbun indeks lama
        _index[key] = build_index(context)
    return _index[key]


def page_tocs(key, context):
    """{slug: [(id, judul bagian), ...]} untuk sub-menu di navigasi dokumentasi."""
    return get_index(key, context)["tocs"]


def terms_of(query):
    return [term for term in (t.lower() for t in WORD_RE.findall(query)) if len(term) >= 2][:8]


def score(section, terms):
    """Skor sebuah bagian, atau 0 bila ada istilah yang tidak ditemukan sama sekali."""
    # Judul halaman hanya dihitung untuk bagian pembukanya; kalau tidak, setiap bagian
    # halaman "Firmware updates and MAC address" akan cocok dengan kata "mac".
    title = section.page.title.lower() if not section.anchor else ""
    heading, text = section.heading.lower(), section.text.lower()
    total = 0
    for term in terms:
        hits = text.count(term)
        in_title, in_heading = term in title, term in heading
        if not (hits or in_title or in_heading):
            return 0
        total += hits + 8 * in_heading + 4 * in_title
    return total


def snippet(text, terms):
    """Potongan teks di sekitar kecocokan pertama, dengan <mark> pada setiap istilah."""
    lower = text.lower()
    positions = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, min(positions) - SNIPPET // 3) if positions else 0
    piece = text[start:start + SNIPPET]
    if start > 0:
        piece = "… " + piece.split(" ", 1)[-1]
    if start + SNIPPET < len(text):
        piece = piece.rsplit(" ", 1)[0] + " …"
    return highlight(piece, terms)


def highlight(text, terms):
    escaped = escape(text)
    if not terms:
        return mark_safe(escaped)
    pattern = re.compile("|".join(re.escape(escape(term)) for term in sorted(terms, key=len, reverse=True)),
                         re.I)
    return mark_safe(pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", escaped))


def search(query, key, context):
    """Hasil pencarian: daftar dict (page, anchor, heading, snippet), skor tertinggi dulu."""
    terms = terms_of(query)
    if not terms:
        return []
    found = []
    for section in get_index(key, context)["sections"]:
        value = score(section, terms)
        if value:
            found.append((value, section))
    found.sort(key=lambda item: -item[0])
    return [
        {
            "page": section.page,
            "anchor": section.anchor,
            "heading": highlight(section.heading, terms),
            "snippet": snippet(section.text, terms),
        }
        for _, section in found[:MAX_RESULTS]
    ]
