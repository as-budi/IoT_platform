"""
Unduhan data history satu variabel sebagai CSV atau XLSX.

    /devices/<device>/variables/<variabel>/export/csv/?period=1d
    /devices/<device>/variables/<variabel>/export/xlsx/?range=all

Rentang waktu sama dengan chart (?period=..., ?period=custom&start=...&end=...),
atau seluruh data (?range=all). Baris diurutkan dari yang TERLAMA.

Kolom:
    time       waktu lokal server (TIME_ZONE), "2026-10-21 14:05:09"
    time_utc   waktu UTC dalam ISO 8601 (sampai milidetik), untuk diolah program lain
    value      angka seperti yang disimpan (aktuator ON/OFF: 1 / 0)
    state      khusus variabel ON/OFF: "ON" / "OFF"

CSV
    Ditulis BARIS DEMI BARIS (StreamingHttpResponse): jutaan baris pun tidak dimuat
    sekaligus ke memori. Diawali BOM UTF-8 supaya Excel membaca "°C" dengan benar.

XLSX
    File .xlsx sebenarnya ZIP berisi beberapa file XML (format Office Open XML).
    Di sini ditulis langsung dengan zipfile dan teks XML, tanpa paket tambahan
    seperti openpyxl. Waktu disimpan sebagai TANGGAL Excel (bukan teks), jadi bisa
    langsung dipakai untuk grafik atau filter di Excel/LibreOffice/Google Sheets.
    Batas baris Excel 1.048.576; di atas MAX_XLSX_ROWS pengguna diarahkan ke CSV.
"""
import csv
import tempfile
import zipfile
from datetime import datetime, timezone as dt_timezone
from xml.sax.saxutils import escape

from django.utils import timezone
from django.utils.text import slugify

MAX_XLSX_ROWS = 500_000
CHUNK = 2000


def rows(queryset):
    """(ts, value) urut dari yang terlama, diambil per potongan supaya hemat memori."""
    return queryset.order_by("ts").values_list("ts", "value").iterator(chunk_size=CHUNK)


def filename(variable, start, end, extension):
    """greenhouse-node_temperature_2026-10-20_2026-10-21.csv"""
    if start is None:
        span = "all"
    else:
        span = f"{timezone.localtime(start):%Y-%m-%d}_{timezone.localtime(end):%Y-%m-%d}"
    return f"{slugify(variable.device.name) or 'device'}_{variable.key}_{span}.{extension}"


def headers(variable):
    unit = f" ({variable.unit})" if variable.unit and not variable.is_boolean else ""
    columns = ["time", "time_utc", f"value{unit}"]
    if variable.is_boolean:
        columns.append("state")
    return columns


def utc_text(ts):
    """2026-10-21T07:05:09.123Z (UTC, sampai milidetik)."""
    return ts.astimezone(dt_timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def state_text(value):
    return "ON" if value >= 0.5 else "OFF"


# --- CSV ------------------------------------------------------------------------------

class _Echo:
    """Objek "file" yang mengembalikan teks yang ditulis csv.writer, bukan menyimpannya."""

    def write(self, value):
        return value


def csv_lines(variable, queryset):
    writer = csv.writer(_Echo())
    yield "﻿" + writer.writerow(headers(variable))
    for ts, value in rows(queryset):
        line = [
            timezone.localtime(ts).strftime("%Y-%m-%d %H:%M:%S"),
            utc_text(ts),
            f"{value:g}",
        ]
        if variable.is_boolean:
            line.append(state_text(value))
        yield writer.writerow(line)


# --- XLSX ------------------------------------------------------------------------------

EXCEL_EPOCH = datetime(1899, 12, 30)

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

# Gaya sel: 0 = biasa, 1 = tanggal-waktu "yyyy-mm-dd hh:mm:ss", 2 = judul kolom tebal.
STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

SHEET_HEAD = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
<cols><col min="1" max="1" width="20" customWidth="1"/><col min="2" max="2" width="24" customWidth="1"/><col min="3" max="4" width="14" customWidth="1"/></cols>
<sheetData>
"""
SHEET_TAIL = "</sheetData>\n</worksheet>"


def text_cell(ref, text, style=0):
    style_attr = f' s="{style}"' if style else ""
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(str(text))}</t></is></c>'


def excel_serial(ts):
    """Waktu -> nomor tanggal Excel (hari sejak 1899-12-30, pecahan = jam), di zona waktu lokal."""
    local = timezone.localtime(ts).replace(tzinfo=None)
    return (local - EXCEL_EPOCH).total_seconds() / 86400


def write_xlsx(variable, queryset, target):
    """Tulis workbook ke `target` (file biner). Mengembalikan jumlah baris data."""
    columns = "ABCD"
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", CONTENT_TYPES)
        book.writestr("_rels/.rels", ROOT_RELS)
        # Nama sheet Excel: maks. 31 karakter, tanpa : \\ / ? * [ ]
        sheet_name = "".join(ch for ch in variable.key if ch not in ':\\/?*[]')[:31] or "data"
        book.writestr("xl/workbook.xml", WORKBOOK.format(name=escape(sheet_name)))
        book.writestr("xl/_rels/workbook.xml.rels", WORKBOOK_RELS)
        book.writestr("xl/styles.xml", STYLES)
        with book.open("xl/worksheets/sheet1.xml", "w") as sheet:
            sheet.write(SHEET_HEAD.encode())
            head = "".join(text_cell(f"{columns[i]}1", name, style=2) for i, name in enumerate(headers(variable)))
            sheet.write(f'<row r="1">{head}</row>\n'.encode())
            for number, (ts, value) in enumerate(rows(queryset), start=2):
                cells = [
                    f'<c r="A{number}" s="1"><v>{excel_serial(ts):.10f}</v></c>',
                    text_cell(f"B{number}", utc_text(ts)),
                    f'<c r="C{number}"><v>{value!r}</v></c>',
                ]
                if variable.is_boolean:
                    cells.append(text_cell(f"D{number}", state_text(value)))
                sheet.write(f'<row r="{number}">{"".join(cells)}</row>\n'.encode())
                count += 1
            sheet.write(SHEET_TAIL.encode())
    return count


def xlsx_file(variable, queryset):
    """Workbook di file sementara (di memori sampai 10 MB, sesudahnya di disk)."""
    target = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    write_xlsx(variable, queryset, target)
    target.seek(0)
    return target
