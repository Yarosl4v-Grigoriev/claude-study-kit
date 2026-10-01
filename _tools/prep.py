#!/usr/bin/env python3
"""Подготовка исходников к чтению Claude с минимумом токенов.

Идея: каждый исходник «читается глазами» ровно один раз. Текстовые PDF превращаются в текст
бесплатно (pdftotext), рукописные (Freeform, Notes, .pages, фото) режутся на плитки, которые
один раз расшифровывает субагент в <предмет>/.source/text/<имя>.md. Дальше все команды
(/конспект, /сверка, /вопросы, ...) работают только с этим текстом.

  python3 _tools/prep.py pending [предмет]   новые исходники (нет в .index.json) и их состояние
  python3 _tools/prep.py extract <файл> ...  текст → .source/text/<имя>.md; рукопись → .source/tiles/<имя>/NN.png
  python3 _tools/prep.py status              таблица для /статус (ничего не создаёт)
"""
import json, re, subprocess, sys, unicodedata, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIRS = ["Конспект_с_пада", "Материал_преподом", "Материал_учебника", "Аудиотранскрипция"]  # домашку учитывает учёт.json
SKIP = {".DS_Store"}
TILE = 1400          # сторона плитки, px: крупнее — модель всё равно уменьшит, мельче — лишние плитки
OVERLAP = 120        # перекрытие плиток, чтобы строка на стыке не потерялась
BOARD_PX_PER_PT = 1.15  # разрешение досок Freeform/Notes: при нём читается обычный почерк
MAX_TILES = 40          # потолок плиток на страницу (иначе разрешение снижается)
MAX_SIDE = 9000         # картинки крупнее ужимаются до этой стороны
MIN_TEXT = 200       # символов на страницу, чтобы считать PDF текстовым
MARK = re.compile(r"\[(\?\??|!|пример|док|почему|связь|слово|перевод|ошибка\?|как сказать)\]")


def nfc(s):
    return unicodedata.normalize("NFC", s).lower()


MOJI = re.compile(r"[À-ÿ]{2,}[À-ÿ \xa0,.;:()\-−•]*")


def fix_cp1251(txt):
    """Кракозябры вида «Ïîñë-òè» (cp1251, прочитанный как latin-1) → нормальная кириллица."""
    if len(re.findall(r"[À-ÿ]", txt)) < 50 or len(re.findall(r"[А-яЁё]", txt)) > len(txt) * 0.05:
        return txt
    def one(m):
        try:
            return m[0].encode("latin-1").decode("cp1251")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m[0]
    return MOJI.sub(one, txt)


def subjects():
    return sorted(p for p in ROOT.iterdir()
                  if p.is_dir() and not p.name.startswith((".", "_")) and p.suffix != ".app" and p.name != "examples")


def sources(subj):
    for d in SRC_DIRS:
        base = subj / d
        if base.is_dir():
            for f in sorted(base.rglob("*")):
                if (f.is_file() or f.suffix == ".pages") and f.name not in SKIP and ".pages/" not in str(f):
                    yield f


def processed():
    idx = ROOT / ".index.json"
    data = json.loads(idx.read_text()) if idx.exists() else {}
    done = set()
    for v in data.get("processed", {}).values():
        for s in v.get("sources", []):
            done.add(nfc(s))
    return done


def text_path(f):
    subj = f.relative_to(ROOT).parts[0]
    return ROOT / subj / ".source" / "text" / (f.stem + ".md")


def tiles_dir(f):
    subj = f.relative_to(ROOT).parts[0]
    return ROOT / subj / ".source" / "tiles" / f.stem


def kind(f):
    ext = f.suffix.lower()
    if ext in (".txt", ".md"):
        return "text"
    if ext == ".pdf":
        pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(f)], capture_output=True, text=True).stdout or "Pages: 1")[1])
        txt = subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout
        return "pdf-text" if len(txt.strip()) >= MIN_TEXT * pages else "pdf-scan"
    if ext == ".pages":
        return "pages"
    if ext in (".docx", ".doc", ".rtf"):
        return "docx"
    if ext in (".png", ".jpg", ".jpeg", ".heic", ".webp"):
        return "image"
    return "other"


def state(f):
    t, d = text_path(f), tiles_dir(f)
    if t.exists():
        return "расшифровано → " + str(t.relative_to(ROOT))
    if d.exists() and any(d.glob("*.png")):
        return f"плитки готовы ({len(list(d.glob('*.png')))}) → нужна расшифровка субагентом"
    return "не подготовлено"


def cmd_pending(args):
    done = processed()
    want = nfc(args[0]) if args else None
    n = 0
    for subj in subjects():
        if want and nfc(subj.name) != want:
            continue
        for f in sources(subj):
            rel = str(f.relative_to(ROOT))
            if nfc(rel) in done:
                continue
            n += 1
            print(f"{rel}\t{kind(f)}\t{state(f)}")
    if not n:
        print("новых исходников нет")


def _grid(n):
    """Начала плиток по одной оси с перекрытием."""
    if n <= TILE:
        return [0]
    step = TILE - OVERLAP
    starts = list(range(0, n - TILE, step)) + [n - TILE]
    return sorted(set(starts))


def tile_image(img_path, out_dir, prefix):
    """Режет картинку на плитки TILE×TILE по обеим осям, не ужимая мелкий почерк."""
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(img_path).convert("RGB")
    w, h = im.size
    k = min(1, MAX_SIDE / max(w, h))
    if k < 1:
        im = im.resize((round(w * k), round(h * k)), Image.LANCZOS)
        w, h = im.size
    out = []
    for r, y in enumerate(_grid(h), 1):
        for c, x in enumerate(_grid(w), 1):
            p = out_dir / f"{prefix}r{r:02d}c{c:02d}.png"
            im.crop((x, y, min(w, x + TILE), min(h, y + TILE))).save(p, optimize=True)
            out.append(p)
    return out


def cmd_extract(args):
    for a in args:
        f = Path(a).resolve()
        if not f.exists():
            print(f"! нет файла: {a}")
            continue
        k = kind(f)
        t = text_path(f)
        t.parent.mkdir(parents=True, exist_ok=True)
        if k == "text":
            t.write_text(f"<!-- источник: {f.relative_to(ROOT)} -->\n" + f.read_text(errors="replace"))
            print(f"текст → {t.relative_to(ROOT)}")
            continue
        if k == "docx":
            txt = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(f)], capture_output=True, text=True).stdout
            t.write_text(f"<!-- источник: {f.relative_to(ROOT)} -->\n" + txt)
            print(f"текст ({len(txt)} симв.) → {t.relative_to(ROOT)}")
            continue
        if k == "pdf-text":
            txt = fix_cp1251(subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout)
            t.write_text(f"<!-- источник: {f.relative_to(ROOT)} (pdftotext; формулы проверить по оригиналу при сомнении) -->\n" + txt)
            print(f"текст ({len(txt)} симв.) → {t.relative_to(ROOT)}")
            continue
        d = tiles_dir(f)
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.png"):
            old.unlink()
        tiles = []
        if k == "pdf-scan":
            pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(f)], capture_output=True, text=True).stdout)[1])
            for p in range(1, pages + 1):
                tmp = d / f"_p{p}"
                # ширина страницы → ~TILE px, независимо от размера доски
                w_pt = float(re.search(r"[Pp]age\s+(?:\d+\s+)?size:\s+([\d.]+)", subprocess.run(["pdfinfo", "-f", str(p), "-l", str(p), str(f)], capture_output=True, text=True).stdout)[1])
                h_pt = float(re.search(r"[Pp]age\s+(?:\d+\s+)?size:\s+[\d.]+\s+x\s+([\d.]+)", subprocess.run(["pdfinfo", "-f", str(p), "-l", str(p), str(f)], capture_output=True, text=True).stdout)[1])
                # узкий лист (A4) — во всю ширину плитки; широкая доска — фиксированное разрешение для почерка
                scale = max(BOARD_PX_PER_PT, TILE / w_pt)
                tiles_est = len(_grid(round(w_pt * scale))) * len(_grid(round(h_pt * scale)))
                if tiles_est > MAX_TILES:
                    scale *= (MAX_TILES / tiles_est) ** 0.5
                dpi = max(40, min(200, int(scale * 72)))  # вниз: лишний пиксель не должен давать лишнюю колонку
                subprocess.run(["pdftoppm", "-r", str(dpi), "-f", str(p), "-l", str(p), "-png", "-singlefile", str(f), str(tmp)], check=True)
                tiles += tile_image(f"{tmp}.png", d, f"p{p}_")
                Path(f"{tmp}.png").unlink()
        elif k == "pages":
            with zipfile.ZipFile(f) as z:
                imgs = [n for n in z.namelist() if n.startswith("Data/") and n.lower().endswith((".png", ".jpg", ".jpeg"))
                        and "small" not in n.lower() and "micro" not in n.lower()]
                imgs.sort(key=lambda n: -z.getinfo(n).file_size)
                for i, n in enumerate(imgs, 1):
                    tmp = d / f"_img{i}{Path(n).suffix}"
                    tmp.write_bytes(z.read(n))
                    tiles += tile_image(tmp, d, f"i{i}_")
                    tmp.unlink()
            if not tiles:
                print(f"! в {f.name} нет картинок — экспортируй .pages в PDF и повтори")
        elif k == "image":
            tiles = tile_image(f, d, "")
        else:
            print(f"! неизвестный тип: {f.name}")
            continue
        print(f"плитки ({len(tiles)}; имя rNNcMM = ряд/колонка, читать по рядам слева направо) → {d.relative_to(ROOT)}  ⇒ расшифровать в {t.relative_to(ROOT)}")


def cmd_status(_):
    done = processed()
    print("| предмет | новых исходников | конспектов | открытых вопросов |")
    print("|---|---|---|---|")
    for subj in subjects():
        new = sum(1 for f in sources(subj) if nfc(str(f.relative_to(ROOT))) not in done)
        cons = len(list((subj / "_конспекты").glob("*.pdf"))) if (subj / "_конспекты").is_dir() else 0
        q = subj / "вопросы.md"
        opened = sum(1 for l in q.read_text().splitlines() if MARK.search(l) and "[✓]" not in l and not l.startswith(("#", "Пиши сюда"))) if q.exists() else 0
        print(f"| {subj.name} | {new} | {cons} | {opened} |")


if __name__ == "__main__":
    cmds = {"pending": cmd_pending, "extract": cmd_extract, "status": cmd_status}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__)
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])
