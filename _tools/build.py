#!/usr/bin/env python3
"""Сборка PDF-конспекта одной командой (вместо 5–7 отдельных шагов модели).

  python3 _tools/build.py <предмет> <тема> [--pdf ИМЯ.pdf] [--dir _конспекты|_разборы] [--fig ИМЯ] [--sources ФАЙЛ ...] [--no-preview]

Что делает:
  1. берёт <предмет>/.source/<тема>.html;
  2. рисунки: если есть .source/fig/<тема>.json — печатает `figures.py --check` и подставляет <!--fig:id-->;
     ручные SVG подставляет из .source/fig/<тема>/<имя>.svg вместо <!--svg:имя-->;
  3. проверяет: не осталось ли незаменённых меток, нет ли сырого `<` внутри $…$ (нужно \\lt);
  4. печатает PDF (Chrome headless; иначе weasyprint, но без MathJax) в <предмет>/<dir>/<pdf>;
     имя PDF при пересборке берётся из .index.json, если --pdf не указан;
  5. проверяет PDF: число страниц, не остались ли формулы сырым LaTeX ($…$);
  6. рендерит в PNG (60 dpi) только страницы с подписями «Рис.» → .source/_preview/ для визуальной проверки;
  7. с --sources записывает в .index.json: исходники → PDF, дата.
"""
import argparse, json, os, re, shutil, subprocess, sys, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
CHROMES = [os.environ.get("CHROME", ""),
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium",
           "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]


def nfc(s):
    return unicodedata.normalize("NFC", s).lower()


def find_subject(name):
    for p in ROOT.iterdir():
        if p.is_dir() and nfc(p.name) == nfc(name):
            return p
    sys.exit(f"! нет папки предмета: {name}")


def chrome():
    for c in CHROMES:
        if c and (Path(c).exists() or shutil.which(c)):
            return c if Path(c).exists() else shutil.which(c)
    return None


def index_key(subj, topic):
    return f"{subj.name[:1].upper()}{subj.name[1:]}/{topic}"


def load_index():
    p = ROOT / ".index.json"
    return json.loads(p.read_text()) if p.exists() else {}


def pdf_from_index(idx, subj, topic):
    for k, v in idx.get("processed", {}).items():
        if nfc(k) == nfc(f"{subj.name}/{topic}") and v.get("pdf"):
            return Path(v["pdf"]).name
    return None


def rel(p):
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject"); ap.add_argument("topic")
    ap.add_argument("--pdf"); ap.add_argument("--dir", default="_конспекты")
    ap.add_argument("--fig", help="имя спецификации рисунков, если отличается от темы")
    ap.add_argument("--sources", nargs="*"); ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args()

    subj = find_subject(a.subject)
    src = subj / ".source" / f"{a.topic}.html"
    if not src.exists():
        sys.exit(f"! нет {src.relative_to(ROOT)}")
    idx = load_index()
    pdf_name = a.pdf or pdf_from_index(idx, subj, a.topic)
    if not pdf_name:
        sys.exit("! укажи --pdf <лекция|семинар>_<ГГГГ-ММ-ДД>_<тема>.pdf (в .index.json этой темы ещё нет)")
    out = subj / a.dir / pdf_name
    out.parent.mkdir(parents=True, exist_ok=True)
    html = src.read_text(encoding="utf-8")
    warn = []

    # 2. рисунки
    fig = a.fig or a.topic
    spec = subj / ".source" / "fig" / f"{fig}.json"
    build = subj / ".source" / f"_build-{a.topic}.html"  # своя для каждой темы: параллельные сборки не мешают
    if spec.exists():
        chk = subprocess.run([sys.executable, str(TOOLS / "figures.py"), str(spec), "--check"], capture_output=True, text=True)
        if chk.stdout.strip():
            print("— figures --check —\n" + chk.stdout.strip())
        if chk.returncode:
            sys.exit("! figures.py --check: " + chk.stderr.strip())
        tmp = subj / ".source" / f"_fig_in-{a.topic}.html"
        tmp.write_text(html, encoding="utf-8")
        r = subprocess.run([sys.executable, str(TOOLS / "figures.py"), str(spec), "--inline", str(tmp), str(build)],
                           capture_output=True, text=True)
        tmp.unlink()
        if r.returncode:
            sys.exit("! figures.py --inline: " + r.stderr.strip())
        html = build.read_text(encoding="utf-8")
    svgdir = subj / ".source" / "fig" / fig

    def put_svg(m):
        f = svgdir / f"{m[1]}.svg"
        if not f.exists():
            return m[0]
        return f.read_text(encoding="utf-8")
    html = re.sub(r"<!--svg:([\w\-.]+)-->", put_svg, html)

    # 2а. все ли рисунки исходников перенесены (по расшифровкам .source/text/<имя>.md)
    srcs = a.sources
    if not srcs:
        for k, v in idx.get("processed", {}).items():
            if nfc(k) == nfc(f"{subj.name}/{a.topic}"):
                srcs = v.get("sources")
    src_figs = 0
    for sname in srcs or []:
        tp = subj / ".source" / "text" / (Path(sname).stem + ".md")
        if tp.exists():
            src_figs += len(re.findall(r"\[рис\s+\d", tp.read_text(encoding="utf-8")))   # рисунки доски; [рис-стр] учебника не считаем
    n_figs = len(re.findall(r"<figure\b", html))
    if src_figs and n_figs < src_figs:
        warn.append(f"в расшифровках исходников рисунков: {src_figs}, в конспекте: {n_figs} — каждый [рис] должен стать рисунком "
                    f"(или в тексте явно сказано, почему нет)")

    # 3. проверки исходника
    left = re.findall(r"<!--(?:fig|svg):[\w\-.]+-->", html)
    if left:
        warn.append("не подставлены рисунки: " + ", ".join(sorted(set(left)))
                    + f" (fig — id из {rel(spec)}, svg — файлы в {rel(svgdir)}/)")
    text_only = re.sub(r"<svg.*?</svg>", "", html, flags=re.S)
    for m in re.finditer(r"\$\$(.+?)\$\$|\$([^$]+?)\$", text_only, re.S):
        body = m[1] or m[2]
        if re.search(r"<(?![/!a-zA-Z])|<[a-zA-Z]", body):
            warn.append(f"сырой `<` в формуле (нужно \\lt): ${body[:50].strip()}$")
    build.write_text(html, encoding="utf-8")

    # 4. печать
    c = chrome()
    if c:
        subprocess.run([c, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--virtual-time-budget=20000",
                        f"--print-to-pdf={out}", build.as_uri()], capture_output=True)
    elif shutil.which("weasyprint"):
        warn.append("Chrome не найден: weasyprint не выполняет MathJax — формулы останутся текстом")
        subprocess.run(["weasyprint", str(build), str(out)], check=True)
    else:
        sys.exit("! нет ни Chrome, ни weasyprint")
    build.unlink()
    if not out.exists():
        sys.exit("! PDF не создан")

    # 5. проверка PDF
    pages = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True).stdout)[1])
    fig_pages, raw_tex = [], 0
    for p in range(1, pages + 1):
        t = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), str(out), "-"], capture_output=True, text=True).stdout
        if re.search(r"Рис\.\s*\d", t):
            fig_pages.append(p)
        raw_tex += len(re.findall(r"\$[^$\n]{1,80}\$|\\(?:frac|sqrt|lim|sum)\b", t))
    if raw_tex:
        warn.append(f"в PDF {raw_tex} мест с сырым LaTeX — MathJax не отработал или сломана формула")

    # 6. превью страниц с рисунками
    prev = []
    if not a.no_preview and fig_pages:
        pdir = subj / ".source" / "_preview"
        pdir.mkdir(exist_ok=True)
        for old in pdir.glob(f"{a.topic}-p*.png"):
            old.unlink()
        for p in fig_pages:
            base = pdir / f"{a.topic}-p{p}"
            subprocess.run(["pdftoppm", "-r", "60", "-f", str(p), "-l", str(p), "-png", "-singlefile", str(out), str(base)], check=True)
            prev.append(str(Path(f"{base}.png").relative_to(ROOT)))

    # 7. реестр
    if a.sources:
        m = re.search(r"\d{4}-\d{2}-\d{2}", pdf_name)
        idx.setdefault("processed", {})[index_key(subj, a.topic)] = {
            "sources": a.sources, "pdf": rel(out), "date": m[0] if m else ""}
        (ROOT / ".index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1))

    print(f"PDF: {rel(out)} · страниц {pages} · страницы с рисунками: {fig_pages or 'нет'}")
    if prev:
        print("Превью для проверки рисунков:\n  " + "\n  ".join(prev))
    if a.sources:
        print(f".index.json: {len(a.sources)} исходн. → {out.name}")
    for w in warn:
        print("! " + w)


if __name__ == "__main__":
    main()
