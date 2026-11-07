"""
Unduhan log aktivitas sebagai file Excel (XLSX): /activity/export.xlsx?<filter yang sama>.

Filter (kata kunci, kategori, level, device, pemilik, tanggal) sama dengan halaman
/activity/, jadi yang diunduh = yang sedang terlihat, tanpa batas halaman. Baris diurutkan
dari yang TERBARU, seperti di halaman. Workbook ditulis langsung dengan zipfile dan
teks XML (bagian-bagian yang sama dengan devices/export.py, tanpa openpyxl); kolom Time
berisi tanggal Excel sungguhan, jadi bisa difilter/diurutkan di Excel.
"""
import tempfile
import zipfile
from xml.sax.saxutils import escape

from django.utils import timezone

from devices.export import (
    CONTENT_TYPES, ROOT_RELS, SHEET_TAIL, STYLES, WORKBOOK, WORKBOOK_RELS, excel_serial, text_cell,
)

MAX_ROWS = 200_000
CHUNK = 2000
COLUMNS = "ABCDEFGHIJ"


def headers(include_owner):
    names = ["Time", "Level", "Category", "Event", "Message", "Device", "Rule", "By"]
    return names + ["Owner"] if include_owner else names


def widths(include_owner):
    sizes = [20, 9, 11, 22, 70, 22, 22, 16]
    return sizes + [16] if include_owner else sizes


def sheet_head(include_owner):
    cols = "".join(
        f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths(include_owner), start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
        'state="frozen"/></sheetView></sheetViews>\n'
        f"<cols>{cols}</cols>\n<sheetData>\n"
    )


def row_values(event, include_owner):
    values = [
        event.get_level_display(),
        event.get_category_display(),
        event.verb,
        event.message,
        event.device_name or (event.device.name if event.device_id and event.device else ""),
        event.rule_name or (event.rule.name if event.rule_id and event.rule else ""),
        event.actor_name,
    ]
    if include_owner:
        values.append(event.owner.username if event.owner_id and event.owner else "")
    return values


def write_xlsx(events, target, include_owner=False):
    """Tulis workbook ke `target` (file biner). Mengembalikan jumlah baris data."""
    count = 0
    events = events.select_related("device", "rule", "owner")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", CONTENT_TYPES)
        book.writestr("_rels/.rels", ROOT_RELS)
        book.writestr("xl/workbook.xml", WORKBOOK.format(name="activity"))
        book.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        book.writestr("xl/styles.xml", STYLES)
        with book.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(sheet_head(include_owner).encode())
            head = "".join(text_cell(f"{COLUMNS[i]}1", name, style=2) for i, name in enumerate(headers(include_owner)))
            sheet.write(f'<row r="1">{head}</row>\n'.encode())
            for number, event in enumerate(events.iterator(chunk_size=CHUNK), start=2):
                cells = [f'<c r="A{number}" s="1"><v>{excel_serial(event.at):.10f}</v></c>']
                cells += [
                    text_cell(f"{COLUMNS[i]}{number}", value)
                    for i, value in enumerate(row_values(event, include_owner), start=1) if value
                ]
                sheet.write(f'<row r="{number}">{"".join(cells)}</row>\n'.encode())
                count += 1
            sheet.write(SHEET_TAIL.encode())
    return count


def xlsx_file(events, include_owner=False):
    """Workbook di file sementara (di memori sampai 10 MB, sesudahnya di disk)."""
    target = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    write_xlsx(events, target, include_owner)
    target.seek(0)
    return target


def filename(now=None):
    now = timezone.localtime(now or timezone.now())
    return f"activity-log-{now:%Y%m%d-%H%M}.xlsx"
