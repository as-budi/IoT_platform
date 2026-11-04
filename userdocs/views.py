"""
Dokumentasi pengguna (tahap 25): /docs/, /docs/<slug>/, /docs/search/?q=...

Bisa dibaca tanpa login, supaya calon pengguna bisa melihatnya sebelum mendaftar.
Hanya bab "Connect an ESP32" yang memakai data pengguna: pilihan device dan access
token-nya, khusus pengguna yang login dengan peran Editor ke atas.
"""
from django.http import Http404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe

from . import pages, search
from .context import device_context, docs_context


def base_context(request, facts, current=None):
    """Konteks kerangka dokumentasi: menu bertingkat dan kotak pencarian.

    Menu: bagian (judul, tidak bisa diklik) -> bab (tautan) -> bagian-bagian bab
    (sub-menu, dari judul <h2> setiap bab; terbuka untuk bab yang sedang dibaca).
    """
    tocs = search.page_tocs(facts["http"]["base_url"], facts)
    nav = [
        (section, [{"page": item, "toc": tocs.get(item.slug, []), "current": item.slug == current}
                   for item in items])
        for section, items in pages.sections(request.user)
    ]
    return {"sections": pages.sections(request.user), "nav": nav, "query": request.GET.get("q", "").strip()}


def index(request):
    context = base_context(request, docs_context(request))
    return render(request, "userdocs/index.html", context)


def page(request, slug):
    doc = pages.BY_SLUG.get(slug)
    if doc is None or not pages.is_visible(slug, request.user):
        raise Http404("No such page.")
    facts = docs_context(request)
    context = dict(facts)
    if slug == "connect-esp32":
        context.update(device_context(request))
    # Fragmen dirender lebih dulu: daftar isi diambil dari judul-judul bagiannya.
    rendered = render_to_string(doc.template, context, request=request)
    previous, following = pages.neighbours(slug, request.user)
    context.update(base_context(request, facts, current=slug))
    context.update({
        "doc": doc,
        "content": mark_safe(rendered),
        "toc": [item for item in search.toc(rendered) if item[0] in (2, 3)],
        "previous": previous,
        "following": following,
    })
    return render(request, "userdocs/page.html", context)


def search_view(request):
    facts = docs_context(request)
    context = base_context(request, facts)
    visible = {page.slug for page in pages.visible_pages(request.user)}
    context["results"] = [result for result in search.search(context["query"], facts["http"]["base_url"], facts)
                          if result["page"].slug in visible]
    return render(request, "userdocs/search.html", context)
