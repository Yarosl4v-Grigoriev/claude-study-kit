#!/usr/bin/env python3
"""Заметки → Pages → PDF в <предмет>/Конспект_с_пада/.

Берёт ТОЛЬКО заметки, чей заголовок начинается с метки «<Предмет>-<тип>»
(Алгебра-семинар, Матан-лекция, ...). Остальные заметки не читаются.
Уже выгруженные (по id заметки) пропускаются. Реестр: .source/sync_state.json

Запуск:  python3 _tools/sync_notes.py [--redo] [--dry]
  --redo  перевыгрузить и те, что изменены после выгрузки
  --dry   только показать, что будет выгружено
"""
import base64, html, json, re, struct, subprocess, sys, tempfile, time, zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".source" / "sync_state.json"

# Метка в заметке → папка предмета. Правится в _tools/subjects.json.
SUBJECTS = {k.lower(): v for k, v in
            json.loads((Path(__file__).resolve().parent / "subjects.json").read_text()).items()
            if not k.startswith("_")}
KINDS = {"лекция": "Лекция", "лек": "Лекция", "семинар": "Семинар", "сем": "Семинар"}
LABEL = re.compile(r"^\s*([A-Za-zА-Яа-яЁё. ]+?)\s*[-–—]\s*([A-Za-zА-Яа-яЁё]+)")
DATE = re.compile(r"(\d{1,2})[.,](\d{1,2})[.,](\d{2}|\d{4})")


def osa(script, *args):
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.rstrip("\n")


def list_notes():
    out = osa('''
tell application "Notes"
  set o to ""
  repeat with f in folders
    if name of f is not "Recently Deleted" then
      repeat with n in notes of f
        set c to creation date of n
        set m to modification date of n
        set o to o & (id of n) & "¦" & (name of n) & "¦" & (year of c) & "-" & ((month of c) as integer) & "-" & (day of c) & "¦" & (m as «class isot» as string) & linefeed
      end repeat
    end if
  end repeat
  return o
end tell''')
    for line in out.splitlines():
        p = line.split("¦")
        if len(p) == 4:
            yield dict(id=p[0], name=p[1], created=p[2], modified=p[3])


def parse_label(title):
    title = title.replace("\xa0", " ")
    m = LABEL.match(title)  # «Алгебра-семинар ...»
    if m:
        subj = SUBJECTS.get(m.group(1).strip().lower().rstrip("."))
        kind = KINDS.get(m.group(2).lower())
        if subj and kind:
            return subj, kind
    # «Лек линал 25.09.26», «сем алгебра ...» — тип, затем предмет
    m = re.match(r"^\s*([А-Яа-яЁё]+)[\s\-–—]+(.+)$", title)
    if m and KINDS.get(m.group(1).lower()):
        rest = m.group(2).lower()
        for key in sorted(SUBJECTS, key=len, reverse=True):
            if re.match(re.escape(key) + r"(?![А-Яа-яЁёA-Za-z])", rest):
                return SUBJECTS[key], KINDS[m.group(1).lower()]
    return None


def note_date(note):
    m = DATE.search(note["name"])
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        return f"{d:02d}.{mo:02d}.{y % 100:02d}"
    y, mo, d = map(int, note["created"].split("-"))
    return f"{d:02d}.{mo:02d}.{y % 100:02d}"


# ---------- HTML заметки → docx (без внешних библиотек) ----------
EMU_CM = 360000
MAX_W, MAX_H = 16 * EMU_CM, 23 * EMU_CM


def png_jpg_size(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            mk = data[i + 1]
            if 0xC0 <= mk <= 0xCF and mk not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return 600, 400


class ToDocx(HTMLParser):
    BLOCK = {"div", "p", "h1", "h2", "h3", "h4", "li", "tr", "br"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.cur = [], []
        self.fmt = {"b": 0, "i": 0, "u": 0}
        self.size = None
        self.images = []  # (rid, bytes, ext)
        self.list_stack = []

    def flush(self):
        if self.cur:
            self.paras.append(self.cur)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("ul", "ol"):
            self.list_stack.append([tag, 0])
        if tag in self.BLOCK:
            self.flush()
        if tag == "li" and self.list_stack:
            st = self.list_stack[-1]
            st[1] += 1
            self.cur.append(("t", ("• " if st[0] == "ul" else f"{st[1]}. "), dict(self.fmt), None))
        if tag in ("b", "strong", "h1", "h2", "h3"):
            self.fmt["b"] += 1
        if tag in ("i", "em"):
            self.fmt["i"] += 1
        if tag == "u":
            self.fmt["u"] += 1
        if tag == "h1":
            self.size = 36
        elif tag in ("h2", "h3"):
            self.size = 30
        if tag == "img":
            m = re.match(r"data:image/(\w+);base64,(.+)", a.get("src", ""), re.S)
            if m:
                data = base64.b64decode(m[2])
                ext = "jpeg" if m[1] in ("jpg", "jpeg") else "png"
                if m[1] not in ("png", "jpg", "jpeg"):
                    return  # heic/gif и т.п. — не поддерживаем
                self.images.append((len(self.images) + 1, data, ext))
                self.cur.append(("img", len(self.images), None, data))

    def handle_endtag(self, tag):
        if tag in ("ul", "ol") and self.list_stack:
            self.list_stack.pop()
        if tag in ("b", "strong", "h1", "h2", "h3"):
            self.fmt["b"] = max(0, self.fmt["b"] - 1)
        if tag in ("i", "em"):
            self.fmt["i"] = max(0, self.fmt["i"] - 1)
        if tag == "u":
            self.fmt["u"] = max(0, self.fmt["u"] - 1)
        if tag in ("h1", "h2", "h3"):
            self.flush()
            self.size = None
        if tag in ("div", "p", "li", "tr"):
            self.flush()

    def handle_data(self, data):
        data = data.replace("\n", " ")
        if data.strip() or (self.cur and data):
            f = dict(self.fmt)
            f["sz"] = self.size
            self.cur.append(("t", data, f, None))


def esc(s):
    return html.escape(s, quote=False)


def build_docx(body_html, out):
    p = ToDocx()
    p.feed(body_html)
    p.flush()
    doc = []
    for para in p.paras:
        runs = []
        for kind, val, f, data in para:
            if kind == "t":
                rpr = ""
                if f.get("b"): rpr += "<w:b/>"
                if f.get("i"): rpr += "<w:i/>"
                if f.get("u"): rpr += '<w:u w:val="single"/>'
                if f.get("sz"): rpr += f'<w:sz w:val="{f["sz"]}"/>'
                runs.append(f'<w:r><w:rPr>{rpr}</w:rPr><w:t xml:space="preserve">{esc(val)}</w:t></w:r>')
            else:
                w, h = png_jpg_size(data)
                cx, cy = w * 9525, h * 9525
                k = min(1, MAX_W / cx, MAX_H / cy)
                cx, cy = int(cx * k), int(cy * k)
                runs.append(
                    '<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
                    f'<wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{val}" name="img{val}"/>'
                    '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                    '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                    f'<pic:nvPicPr><pic:cNvPr id="{val}" name="img{val}"/><pic:cNvPicPr/></pic:nvPicPr>'
                    f'<pic:blipFill><a:blip r:embed="rId{val + 10}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                    f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
                    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
                    '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
        doc.append(f"<w:p>{''.join(runs)}</w:p>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        f'<w:body>{"".join(doc)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="708" w:footer="708" w:gutter="0"/>'
        '</w:sectPr></w:body></w:document>')
    rels = "".join(
        f'<Relationship Id="rId{i + 10}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/img{i}.{ext}"/>' for i, _, ext in p.images)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'{rels}</Relationships>')
        for i, data, ext in p.images:
            z.writestr(f"word/media/img{i}.{ext}", data)


def pages_to_pdf(docx, pdf):
    # открываем через LaunchServices: `open POSIX file` из AppleScript Pages не читает (sandbox)
    osa('tell application "Pages" to close every document saving no')
    subprocess.run(["open", "-a", "Pages", str(docx)], check=True)
    osa('''
on run argv
  tell application "Pages"
    set d to missing value
    repeat 120 times
      try
        set d to document 1
        set x to name of d
        exit repeat
      on error
        set d to missing value
        delay 0.5
      end try
    end repeat
    if d is missing value then error "Pages не открыл документ"
    delay 2
    export d to POSIX file (item 1 of argv) as PDF
    close d saving no
  end tell
end run''', str(pdf))


def main():
    dry, redo = "--dry" in sys.argv, "--redo" in sys.argv
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    notes_state = state.setdefault("notes", {})
    done, changed, bad = [], [], []
    for n in list_notes():
        title = n["name"]
        lab = parse_label(title)
        if not lab:
            if re.match(r"^\s*[^\s\-–—]+\s*[-–—]\s*(лекц|лек|семин|сем)", title, re.I):
                bad.append(f"метка не распознана: «{title}»")
            continue
        subj, kind = lab
        rec = notes_state.get(n["id"])
        if rec and not (redo and rec["modified"] != n["modified"]):
            if rec["modified"] != n["modified"]:
                changed.append(f'{rec["file"]} (заметка изменена после выгрузки; --redo)')
            continue
        folder = ROOT / subj / "Конспект_с_пада"
        name = f"{kind} {subj} {note_date(n)}"
        target = folder / f"{name}.pdf"
        if rec:
            target = ROOT / rec["file"]
        else:
            k = 2
            while target.exists():
                target = folder / f"{name} ({k}).pdf"
                k += 1
        if dry:
            done.append(f"[dry] {target.relative_to(ROOT)}")
            continue
        body = osa('tell application "Notes" to get body of note id "%s"' % n["id"])
        # Pages (sandbox) не читает docx из /tmp и скрытых папок — временный файл кладём в _tools/tmp
        tmpbase = Path(__file__).resolve().parent / "tmp"
        tmpbase.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmpbase, prefix="sync_") as tmp:
            docx = Path(tmp) / f"note_{time.time_ns()}.docx"
            build_docx(body, docx)
            folder.mkdir(parents=True, exist_ok=True)
            pages_to_pdf(docx, target)
        notes_state[n["id"]] = {"file": str(target.relative_to(ROOT)), "title": title,
                                "modified": n["modified"], "exported": datetime.now().isoformat(timespec="seconds")}
        done.append(str(target.relative_to(ROOT)))
        if not dry:
            STATE.parent.mkdir(exist_ok=True)
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"Заметки: новых выгружено {len(done)}")
    for x in done: print("  +", x)
    for x in changed: print("  ~", x)
    for x in bad: print("  !", x)


if __name__ == "__main__":
    main()
