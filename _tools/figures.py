#!/usr/bin/env python3
"""
figures.py — генератор чистых SVG-рисунков для конспектов (без внешних библиотек).

ГЛАВНОЕ ПРАВИЛО: графики функций НЕ рисуются «на глаз» кривыми Безье.
Кривая строится только по вычисленным точкам f(x) — так невозможно
нарисовать 1/x как «монотонную кривую через ноль».

Использование (CLI):
    python3 _tools/figures.py spec.json            # SVG каждого рисунка -> stdout, через разделитель
    python3 _tools/figures.py spec.json -o dir/    # dir/<id>.svg
    python3 _tools/figures.py spec.json --check    # текстовая сверка графиков: знаки, монотонность, разрывы
    python3 _tools/figures.py spec.json --inline src.html out.html   # <!--fig:id--> в src.html -> SVG

spec.json — список объектов {"id": ..., "type": ..., ...}. Типы:
  plot      — график(и) функций: {"funcs":[{"f":"1/x","label":"y = 1/x","domain":[-5,5]}],
               "xlim":[-4,4], "ylim":[-4,4], "asymptotes":{"x":[0],"y":[0]},
               "points":[{"x":1,"y":1,"label":"(1,1)","open":false}], "fill":{"f":0,"a":0,"b":1},
               "hlines":[{"y":1,"label":"a+ε"}], "vlines":[{"x":2,"label":"x₀"}], "equal":true}
  numberline — числовая прямая ("ticks" — числа или пары [x,"a−δ"]): {"range":[-3,5], "intervals":[{"a":0,"b":2,"closed":[true,false],"label":"[0,2)"}],
               "points":[{"x":1,"open":true,"label":"1"}], "ticks":[0,1,2]}   (a/b могут быть "-inf"/"inf")
  sequence  — члены последовательности ("values":[...] вместо "a" — явный список, "connect":true — ломаная): {"a":"1/n", "n":[1,20], "limit":0, "eps":0.15, "N":7, "ylim":[-0.2,1.1]}
  venn      — диаграмма Эйлера–Венна: {"sets":["A","B"], "shade":["A&B"]}  (регион: "A&~B", "A&B&~C", ... ; "U" — вне всех)
  mapping   — стрелочная схема отображения: {"A":["1","2","3"], "B":["a","b"], "arrows":[[0,0],[1,0],[2,1]],
               "names":["A","B"], "f":"φ"}
  chain     — композиция: {"sets":[{"name":"A","elems":["x"]},...], "maps":[{"f":"f","arrows":[[0,0]]},...], "total":"g∘f"}
  geom      — геометрия по координатам (векторы, многоугольники, площади): {"xlim":[-1,6], "ylim":[-1,5],
               "polygons":[{"pts":[[0,0],[4,1],[5,4],[1,3]],"label":"S","color":"c1"}],
               "vectors":[{"from":[0,0],"to":[4,1],"label":"a","color":"c2"}],
               "segments":[{"from":[0,0],"to":[5,4],"dashed":true,"label":"d"}],
               "circles":[{"c":[0,0],"r":2,"dashed":true}], "points":[{"x":1,"y":3,"label":"D","offset":[6,-6]}],
               "texts":[{"x":2,"y":4,"t":"+","color":"c3","size":16}],
               "angles":[{"at":[0,0],"from":[4,1],"to":[1,3],"r":0.6,"label":"φ"}],
               "axes":true, "grid":true, "equal":true}   (цвета: c1…c5 или #hex; --check печатает площади и длины;
               у многоугольника "opacity" — заливка, "dashed" — пунктир; "axes":false — чистая схема без осей)
  flow      — схема рассуждения: {"nodes":["x ∈ A∩B","x ∈ A и x ∈ B","..."], "edges":["⇒","⇔"], "dir":"h"|"v"}

Функции можно вызывать и из Python: from figures import plot, numberline, ...
"""
import json, math, sys, os, html, re

# Палитра — согласована с _templates/style.css
C = {"ink": "#1a1a1a", "axis": "#444", "grid": "#e6e6e6", "muted": "#6b6b6b",
     "c1": "#3b6fd6", "c2": "#e07b16", "c3": "#2f9e5a", "c4": "#d63b3b", "c5": "#8a4fd1",
     "fill": "#3b6fd6", "band": "#d63b3b"}
SERIES = [C["c1"], C["c2"], C["c3"], C["c4"], C["c5"]]
FONT = 'font-family="STIX Two Math, Cambria Math, Times New Roman, serif" font-size="13"'
_MATH = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
_MATH.update(abs=abs, min=min, max=max, sgn=lambda v: (v > 0) - (v < 0))


def esc(s):
    return html.escape(str(s), quote=True)


def _num(v):
    if isinstance(v, str):
        v = v.strip().lower()
        if v in ("inf", "+inf", "∞"): return math.inf
        if v in ("-inf", "−inf", "-∞"): return -math.inf
        return float(eval(v, {"__builtins__": {}}, _MATH))
    return float(v)


def compile_f(expr):
    expr = expr.replace("^", "**").replace("−", "-")
    code = compile(expr, "<f>", "eval")
    def f(x):
        try:
            y = eval(code, {"__builtins__": {}}, dict(_MATH, x=x, n=x))
            y = float(y)
            return y if math.isfinite(y) else None
        except (ZeroDivisionError, ValueError, OverflowError, TypeError):
            return None
    return f


def nice_step(span, target=8):
    raw = span / target
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if m * p >= raw: return m * p
    return 10 * p


def fmt(v):
    if abs(v - round(v)) < 1e-9: return str(int(round(v))).replace("-", "−")
    return f"{v:g}".replace("-", "−")


class Frame:
    """Система координат: мировые (x,y) -> пиксели."""
    def __init__(self, xlim, ylim, width=360, height=None, equal=False, pad=(34, 18, 26, 18)):
        self.x0, self.x1 = xlim; self.y0, self.y1 = ylim
        l, t, r, b = pad
        self.l, self.t, self.r, self.b = l, t, r, b
        self.W = width
        pw = width - l - r
        if height is None:
            ph = pw * (self.y1 - self.y0) / (self.x1 - self.x0) if equal else pw * 0.68
            height = ph + t + b
        self.H = height
        self.pw, self.ph = pw, height - t - b

    def X(self, x): return self.l + (x - self.x0) / (self.x1 - self.x0) * self.pw
    def Y(self, y): return self.t + (self.y1 - y) / (self.y1 - self.y0) * self.ph

    def svg_open(self, extra=""):
        return (f'<svg viewBox="0 0 {self.W:.0f} {self.H:.0f}" width="{self.W:.0f}" '
                f'xmlns="http://www.w3.org/2000/svg" {FONT} {extra}>')


def _arrow_defs(uid):
    return (f'<defs><marker id="ar{uid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9 z" fill="{C["axis"]}"/></marker></defs>')


_uid = [0]
def uid():
    _uid[0] += 1
    return f"f{_uid[0]}"


def axes(fr, u, xlabel="x", ylabel="y", ticks=True, grid=True, xstep=None, ystep=None):
    out = []
    xs = xstep or nice_step(fr.x1 - fr.x0)
    ys = ystep or nice_step(fr.y1 - fr.y0)
    ax_y = fr.Y(0) if fr.y0 <= 0 <= fr.y1 else fr.Y(fr.y0)
    ax_x = fr.X(0) if fr.x0 <= 0 <= fr.x1 else fr.X(fr.x0)
    xt = [k * xs for k in range(math.ceil(fr.x0 / xs), math.floor(fr.x1 / xs) + 1)]
    yt = [k * ys for k in range(math.ceil(fr.y0 / ys), math.floor(fr.y1 / ys) + 1)]
    if grid:
        for v in xt: out.append(f'<line x1="{fr.X(v):.1f}" y1="{fr.t}" x2="{fr.X(v):.1f}" y2="{fr.t+fr.ph:.1f}" stroke="{C["grid"]}"/>')
        for v in yt: out.append(f'<line x1="{fr.l}" y1="{fr.Y(v):.1f}" x2="{fr.l+fr.pw:.1f}" y2="{fr.Y(v):.1f}" stroke="{C["grid"]}"/>')
    out.append(f'<line x1="{fr.l-6}" y1="{ax_y:.1f}" x2="{fr.l+fr.pw+10:.1f}" y2="{ax_y:.1f}" stroke="{C["axis"]}" marker-end="url(#ar{u})"/>')
    out.append(f'<line x1="{ax_x:.1f}" y1="{fr.t+fr.ph+6:.1f}" x2="{ax_x:.1f}" y2="{fr.t-10:.1f}" stroke="{C["axis"]}" marker-end="url(#ar{u})"/>')
    out.append(f'<text x="{fr.l+fr.pw+8:.1f}" y="{ax_y+15:.1f}" font-style="italic">{esc(xlabel)}</text>')
    out.append(f'<text x="{ax_x+6:.1f}" y="{fr.t-2:.1f}" font-style="italic">{esc(ylabel)}</text>')
    if ticks:
        for v in xt:
            if abs(v) < 1e-12: continue
            out.append(f'<line x1="{fr.X(v):.1f}" y1="{ax_y-3:.1f}" x2="{fr.X(v):.1f}" y2="{ax_y+3:.1f}" stroke="{C["axis"]}"/>')
            out.append(f'<text x="{fr.X(v):.1f}" y="{ax_y+15:.1f}" text-anchor="middle" font-size="10" fill="{C["muted"]}">{fmt(v)}</text>')
        for v in yt:
            if abs(v) < 1e-12: continue
            out.append(f'<line x1="{ax_x-3:.1f}" y1="{fr.Y(v):.1f}" x2="{ax_x+3:.1f}" y2="{fr.Y(v):.1f}" stroke="{C["axis"]}"/>')
            out.append(f'<text x="{ax_x-6:.1f}" y="{fr.Y(v)+3.5:.1f}" text-anchor="end" font-size="10" fill="{C["muted"]}">{fmt(v)}</text>')
        if fr.x0 <= 0 <= fr.x1 and fr.y0 <= 0 <= fr.y1:
            out.append(f'<text x="{ax_x-5:.1f}" y="{ax_y+14:.1f}" text-anchor="end" font-size="10" fill="{C["muted"]}">0</text>')
    return out


def sample_branches(f, a, b, ylim, n=1200):
    """Точки кривой, разбитые на ветви: разрыв там, где f не определена или скачок (полюс)."""
    lo, hi = ylim
    span = hi - lo
    branches, cur, prev = [], [], None
    for i in range(n + 1):
        x = a + (b - a) * i / n
        y = f(x)
        if y is None:
            if cur: branches.append(cur); cur = []
            prev = None; continue
        if prev is not None:
            # полюс: смена знака с огромным скачком или скачок больше 2 высот окна
            if abs(y - prev) > 2 * span or (y * prev < 0 and abs(y - prev) > 0.6 * span and
                                             max(abs(y), abs(prev)) > 0.5 * span):
                if cur: branches.append(cur)
                cur = []
        cur.append((x, y)); prev = y
    if cur: branches.append(cur)
    return [br for br in branches if len(br) > 1]


def path_d(fr, pts, lim):
    lo, hi = lim
    m = 3 * (hi - lo)
    return "M" + " L".join(f"{fr.X(x):.2f},{fr.Y(max(lo - m, min(hi + m, y))):.2f}" for x, y in pts)


def plot(funcs, xlim, ylim, width=360, height=None, equal=False, asymptotes=None, points=None,
         hlines=None, vlines=None, fill=None, xlabel="x", ylabel="y", grid=True, xstep=None, ystep=None,
         legend=None, **_):
    fr = Frame(xlim, ylim, width, height, equal)
    u = uid()
    out = [fr.svg_open(), _arrow_defs(u),
           f'<defs><clipPath id="cp{u}"><rect x="{fr.l}" y="{fr.t}" width="{fr.pw:.1f}" height="{fr.ph:.1f}"/></clipPath></defs>']
    out += axes(fr, u, xlabel, ylabel, True, grid, xstep, ystep)
    if fill:
        f = compile_f(str(fill["f"])); g = compile_f(str(fill.get("g", "0")))
        a, b = _num(fill["a"]), _num(fill["b"])
        top = [(a + (b - a) * i / 300) for i in range(301)]
        pts = [(x, f(x)) for x in top if f(x) is not None]
        bot = [(x, g(x)) for x in reversed(top) if g(x) is not None]
        d = path_d(fr, pts + bot, ylim) + " Z"
        out.append(f'<path d="{d}" fill="{fill.get("color", C["fill"])}" fill-opacity="0.15" stroke="none" clip-path="url(#cp{u})"/>')
    asym = asymptotes or {}
    for x in asym.get("x", []):
        out.append(f'<line x1="{fr.X(x):.1f}" y1="{fr.t}" x2="{fr.X(x):.1f}" y2="{fr.t+fr.ph:.1f}" stroke="{C["c4"]}" stroke-dasharray="5 4" stroke-width="1"/>')
    for y in asym.get("y", []):
        out.append(f'<line x1="{fr.l}" y1="{fr.Y(y):.1f}" x2="{fr.l+fr.pw:.1f}" y2="{fr.Y(y):.1f}" stroke="{C["c4"]}" stroke-dasharray="5 4" stroke-width="1"/>')
    for h in hlines or []:
        col = h.get("color", C["muted"])
        out.append(f'<line x1="{fr.l}" y1="{fr.Y(h["y"]):.1f}" x2="{fr.l+fr.pw:.1f}" y2="{fr.Y(h["y"]):.1f}" stroke="{col}" stroke-dasharray="3 3"/>')
        if h.get("label"): out.append(f'<text x="{fr.l+fr.pw-2:.1f}" y="{fr.Y(h["y"])-4:.1f}" text-anchor="end" fill="{col}">{esc(h["label"])}</text>')
    for v in vlines or []:
        col = v.get("color", C["muted"])
        out.append(f'<line x1="{fr.X(v["x"]):.1f}" y1="{fr.t}" x2="{fr.X(v["x"]):.1f}" y2="{fr.t+fr.ph:.1f}" stroke="{col}" stroke-dasharray="3 3"/>')
        if v.get("label"): out.append(f'<text x="{fr.X(v["x"])+4:.1f}" y="{fr.t+10:.1f}" fill="{col}">{esc(v["label"])}</text>')
    for i, fd in enumerate(funcs):
        f = compile_f(fd["f"])
        a, b = fd.get("domain", xlim)
        a, b = max(_num(a), xlim[0]), min(_num(b), xlim[1])
        col = fd.get("color", SERIES[i % len(SERIES)])
        dash = ' stroke-dasharray="6 4"' if fd.get("dashed") else ""
        brs = sample_branches(f, a, b, ylim)
        for br in brs:
            out.append(f'<path d="{path_d(fr, br, ylim)}" fill="none" stroke="{col}" stroke-width="2"{dash} '
                       f'stroke-linejoin="round" clip-path="url(#cp{u})"/>')
        if fd.get("label") and not legend:
            lx = _num(fd["label_at"]) if "label_at" in fd else None
            pos = _label_pos(fr, f, brs, ylim, lx)
            if pos:
                out.append(f'<text x="{pos[0]:.1f}" y="{pos[1]:.1f}" fill="{col}" font-style="italic">{esc(fd["label"])}</text>')
    if legend:
        items = [(fd.get("color", SERIES[i % len(SERIES)]), fd["label"], fd.get("dashed")) for i, fd in enumerate(funcs) if fd.get("label")]
        lw = max(len(t) for _, t, _ in items) * 6.6 + 34
        lx = fr.l + 6 if legend != "right" else fr.l + fr.pw - lw - 6
        out.append(f'<rect x="{lx:.1f}" y="{fr.t+4}" width="{lw:.1f}" height="{len(items)*16+6}" fill="white" fill-opacity="0.92" stroke="{C["grid"]}" rx="3"/>')
        for k, (col, t, d) in enumerate(items):
            yy = fr.t + 17 + k * 16
            out.append(f'<line x1="{lx+5:.1f}" y1="{yy-4}" x2="{lx+23:.1f}" y2="{yy-4}" stroke="{col}" stroke-width="2"{" stroke-dasharray=\"4 3\"" if d else ""}/>')
            out.append(f'<text x="{lx+28:.1f}" y="{yy}" font-size="12" font-style="italic" fill="{col}">{esc(t)}</text>')
    for p in points or []:
        x, y = _num(p["x"]), _num(p["y"])
        col = p.get("color", C["ink"])
        fillc = "white" if p.get("open") else col
        out.append(f'<circle cx="{fr.X(x):.1f}" cy="{fr.Y(y):.1f}" r="3.2" fill="{fillc}" stroke="{col}" stroke-width="1.5"/>')
        if p.get("label"):
            dx, dy = p.get("offset", [6, -6])
            out.append(f'<text x="{fr.X(x)+dx:.1f}" y="{fr.Y(y)+dy:.1f}" font-size="11" fill="{col}">{esc(p["label"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _label_pos(fr, f, brs, ylim, lx=None):
    if lx is not None and f(lx) is not None:
        return fr.X(lx) + 6, fr.Y(f(lx)) - 6
    # правая часть самой длинной ветви, в пределах окна
    best = None
    for br in brs:
        inside = [(x, y) for x, y in br if ylim[0] < y < ylim[1]]
        if inside and (best is None or len(inside) >= len(best)): best = inside
    if not best: return None
    x, y = best[int(len(best) * 0.8)]
    px, py = fr.X(x), fr.Y(y)
    return min(px + 6, fr.l + fr.pw - 60), max(py - 8, fr.t + 12)


def _col(c, default):
    return C.get(c, c) if c else default


def _shoelace(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def geom(xlim, ylim, width=360, height=None, equal=True, axes_on=True, grid=True, polygons=None, vectors=None,
         segments=None, circles=None, points=None, texts=None, angles=None, xlabel="x", ylabel="y", xstep=None,
         ystep=None, **kw):
    axes_on = kw.get("axes", axes_on)
    fr = Frame(xlim, ylim, width, height, equal)
    u = uid()
    out = [fr.svg_open(), _arrow_defs(u)]
    marks = {}
    def mark(col):
        if col not in marks:
            mid = f"{u}v{len(marks)}"
            marks[col] = mid
            out.append(f'<defs><marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                       f'orient="auto"><path d="M0,1 L10,5 L0,9 z" fill="{col}"/></marker></defs>')
        return marks[col]
    if axes_on:
        out += axes(fr, u, xlabel, ylabel, True, grid, xstep, ystep)
    for i, pg in enumerate(polygons or []):
        col = _col(pg.get("color"), SERIES[i % len(SERIES)])
        pts = [(_num(x), _num(y)) for x, y in pg["pts"]]
        d = " ".join(f"{fr.X(x):.1f},{fr.Y(y):.1f}" for x, y in pts)
        tag = "polygon" if pg.get("closed", True) else "polyline"
        dash = ' stroke-dasharray="5 4"' if pg.get("dashed") else ""
        out.append(f'<{tag} points="{d}" fill="{col if tag == "polygon" else "none"}" fill-opacity="{pg.get("opacity", 0.15)}" '
                   f'stroke="{col}" stroke-width="1.8"{dash}/>')
        if pg.get("label"):
            cx = sum(x for x, _ in pts) / len(pts); cy = sum(y for _, y in pts) / len(pts)
            out.append(f'<text x="{fr.X(cx):.1f}" y="{fr.Y(cy)+4:.1f}" text-anchor="middle" fill="{col}" font-style="italic">{esc(pg["label"])}</text>')
    for c in circles or []:
        col = _col(c.get("color"), C["muted"])
        (cx, cy), r = c["c"], _num(c["r"])
        dash = ' stroke-dasharray="5 4"' if c.get("dashed") else ""
        out.append(f'<ellipse cx="{fr.X(_num(cx)):.1f}" cy="{fr.Y(_num(cy)):.1f}" rx="{r*fr.pw/(fr.x1-fr.x0):.1f}" '
                   f'ry="{r*fr.ph/(fr.y1-fr.y0):.1f}" fill="none" stroke="{col}" stroke-width="1.4"{dash}/>')
    for sg in segments or []:
        col = _col(sg.get("color"), C["muted"])
        (x1, y1), (x2, y2) = [(_num(a), _num(b)) for a, b in (sg["from"], sg["to"])]
        dash = ' stroke-dasharray="5 4"' if sg.get("dashed") else ""
        out.append(f'<line x1="{fr.X(x1):.1f}" y1="{fr.Y(y1):.1f}" x2="{fr.X(x2):.1f}" y2="{fr.Y(y2):.1f}" stroke="{col}" stroke-width="1.4"{dash}/>')
        if sg.get("label"):
            out.append(f'<text x="{fr.X((x1+x2)/2)+5:.1f}" y="{fr.Y((y1+y2)/2)-5:.1f}" font-size="12" fill="{col}">{esc(sg["label"])}</text>')
    for i, v in enumerate(vectors or []):
        col = _col(v.get("color"), SERIES[i % len(SERIES)])
        (x1, y1) = (_num(v.get("from", [0, 0])[0]), _num(v.get("from", [0, 0])[1]))
        (x2, y2) = (_num(v["to"][0]), _num(v["to"][1]))
        out.append(f'<line x1="{fr.X(x1):.1f}" y1="{fr.Y(y1):.1f}" x2="{fr.X(x2):.1f}" y2="{fr.Y(y2):.1f}" stroke="{col}" '
                   f'stroke-width="2" marker-end="url(#{mark(col)})"/>')
        if v.get("label"):
            dx, dy = v.get("offset", [6, -6])
            out.append(f'<text x="{fr.X((x1+x2)/2)+dx:.1f}" y="{fr.Y((y1+y2)/2)+dy:.1f}" fill="{col}" font-style="italic" font-weight="bold">{esc(v["label"])}</text>')
    for a in angles or []:
        col = _col(a.get("color"), C["c5"])
        (ox, oy), (ax_, ay), (bx, by) = [(_num(u_[0]), _num(u_[1])) for u_ in (a["at"], a["from"], a["to"])]
        r = _num(a.get("r", 0.5))
        t1, t2 = math.atan2(ay - oy, ax_ - ox), math.atan2(by - oy, bx - ox)
        d = (t2 - t1) % (2 * math.pi)
        if d > math.pi:
            t1, t2, d = t2, t1, 2 * math.pi - d
        pts = [(ox + r * math.cos(t1 + d * k / 24), oy + r * math.sin(t1 + d * k / 24)) for k in range(25)]
        out.append(f'<polyline points="{" ".join(f"{fr.X(x):.1f},{fr.Y(y):.1f}" for x, y in pts)}" fill="none" stroke="{col}" stroke-width="1.4"/>')
        if a.get("label"):
            tm = t1 + d / 2
            out.append(f'<text x="{fr.X(ox + 1.6 * r * math.cos(tm)):.1f}" y="{fr.Y(oy + 1.6 * r * math.sin(tm)) + 4:.1f}" text-anchor="middle" fill="{col}" font-style="italic">{esc(a["label"])}</text>')
    for t in texts or []:
        col = _col(t.get("color"), C["ink"])
        out.append(f'<text x="{fr.X(_num(t["x"])):.1f}" y="{fr.Y(_num(t["y"])):.1f}" text-anchor="{t.get("anchor", "middle")}" '
                   f'font-size="{t.get("size", 13)}" fill="{col}"{" font-style=\"italic\"" if t.get("italic") else ""}>{esc(t["t"])}</text>')
    for p in points or []:
        x, y = _num(p["x"]), _num(p["y"])
        col = _col(p.get("color"), C["ink"])
        out.append(f'<circle cx="{fr.X(x):.1f}" cy="{fr.Y(y):.1f}" r="3" fill="{"white" if p.get("open") else col}" stroke="{col}" stroke-width="1.5"/>')
        if p.get("label"):
            dx, dy = p.get("offset", [6, -6])
            out.append(f'<text x="{fr.X(x)+dx:.1f}" y="{fr.Y(y)+dy:.1f}" font-size="12" fill="{col}">{esc(p["label"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def check_geom(spec):
    rep = []
    for pg in spec.get("polygons", []):
        pts = [(_num(x), _num(y)) for x, y in pg["pts"]]
        if len(pts) < 3:
            continue
        a = _shoelace(pts)
        rep.append(f'  многоугольник {pg.get("label", "")} {len(pts)} верш.: площадь {abs(a):g} '
                   f'(обход {"против" if a > 0 else "по"} часовой)')
    for v in spec.get("vectors", []):
        f0 = v.get("from", [0, 0]); dx, dy = _num(v["to"][0]) - _num(f0[0]), _num(v["to"][1]) - _num(f0[1])
        rep.append(f'  вектор {v.get("label", "")}: ({dx:g}, {dy:g}), длина {math.hypot(dx, dy):.4g}')
    vs = [v for v in spec.get("vectors", []) if v.get("from", [0, 0]) == spec.get("vectors", [{}])[0].get("from", [0, 0])]
    if len(vs) >= 2:
        f0 = vs[0].get("from", [0, 0])
        (ax, ay), (bx, by) = [(_num(v["to"][0]) - _num(f0[0]), _num(v["to"][1]) - _num(f0[1])) for v in vs[:2]]
        rep.append(f'  det({vs[0].get("label","a")}, {vs[1].get("label","b")}) = {ax*by-ay*bx:g} (площадь параллелограмма {abs(ax*by-ay*bx):g})')
    return "\n".join(rep)


def numberline(range, intervals=None, points=None, ticks=None, width=360, label="x", **_):
    a0, a1 = range
    fr = Frame((a0, a1), (0, 1), width, 64, pad=(14, 8, 18, 8))
    u = uid(); y = 30
    out = [fr.svg_open(), _arrow_defs(u),
           f'<line x1="{fr.l-6}" y1="{y}" x2="{fr.l+fr.pw+8:.1f}" y2="{y}" stroke="{C["axis"]}" marker-end="url(#ar{u})"/>',
           f'<text x="{fr.l+fr.pw+4:.1f}" y="{y+16}" font-style="italic">{esc(label)}</text>']
    for t in ticks or []:
        t, lab = (t if isinstance(t, (list, tuple)) else (t, None))
        t = _num(t); lab = fmt(t) if lab is None else lab
        out.append(f'<line x1="{fr.X(t):.1f}" y1="{y-4}" x2="{fr.X(t):.1f}" y2="{y+4}" stroke="{C["axis"]}"/>')
        out.append(f'<text x="{fr.X(t):.1f}" y="{y+18}" text-anchor="middle" font-size="11" font-style="italic">{esc(lab)}</text>')
    for i, iv in enumerate(intervals or []):
        a, b = _num(iv["a"]), _num(iv["b"])
        col = iv.get("color", SERIES[i % len(SERIES)])
        xa = fr.l - 4 if a == -math.inf else fr.X(a)
        xb = fr.l + fr.pw + 2 if b == math.inf else fr.X(b)
        yy = y - 8 * (iv.get("level", 0))
        out.append(f'<line x1="{xa:.1f}" y1="{yy}" x2="{xb:.1f}" y2="{yy}" stroke="{col}" stroke-width="5" stroke-opacity="0.75"/>')
        cl = iv.get("closed", [True, True])
        for v, c in ((a, cl[0]), (b, cl[1])):
            if math.isinf(v): continue
            out.append(f'<circle cx="{fr.X(v):.1f}" cy="{yy}" r="4" fill="{col if c else "white"}" stroke="{col}" stroke-width="1.8"/>')
        if iv.get("label"):
            out.append(f'<text x="{(max(xa, fr.l)+min(xb, fr.l+fr.pw))/2:.1f}" y="{yy-9}" text-anchor="middle" fill="{col}" font-size="12">{esc(iv["label"])}</text>')
    for p in points or []:
        x = _num(p["x"]); col = p.get("color", C["ink"])
        out.append(f'<circle cx="{fr.X(x):.1f}" cy="{y}" r="3.5" fill="{"white" if p.get("open") else col}" stroke="{col}" stroke-width="1.6"/>')
        if p.get("label"): out.append(f'<text x="{fr.X(x):.1f}" y="{y+18}" text-anchor="middle" font-size="11" fill="{col}">{esc(p["label"])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def sequence(a=None, n=(1, 20), limit=None, eps=None, N=None, ylim=None, width=360, height=None, label=None,
             values=None, connect=False, **_):
    if values is not None:
        vals = [(int(n[0]) + i, float(v)) for i, v in enumerate(values)]
        n = (int(n[0]), int(n[0]) + len(values) - 1)
    else:
        f = compile_f(a)
        vals = [(k, f(k)) for k in range(int(n[0]), int(n[1]) + 1) if f(k) is not None]
    if ylim is None:
        ys = [v for _, v in vals] + ([limit - eps, limit + eps] if limit is not None and eps else [])
        lo, hi = min(ys + [0]), max(ys + [0]); m = 0.1 * (hi - lo or 1)
        ylim = (lo - m, hi + m)
    fr = Frame((min(0, n[0] - 1), n[1] + 1), ylim, width, height)
    u = uid()
    out = [fr.svg_open(), _arrow_defs(u)]
    out += axes(fr, u, "n", label or "aₙ", True, False, xstep=max(1, nice_step(n[1], 10)), ystep=_.get("ystep"))
    if limit is not None and eps:
        out.append(f'<rect x="{fr.l}" y="{fr.Y(limit+eps):.1f}" width="{fr.pw:.1f}" height="{fr.Y(limit-eps)-fr.Y(limit+eps):.1f}" fill="{C["band"]}" fill-opacity="0.10"/>')
        for v, lab in ((limit + eps, "a+ε"), (limit - eps, "a−ε")):
            out.append(f'<line x1="{fr.l}" y1="{fr.Y(v):.1f}" x2="{fr.l+fr.pw:.1f}" y2="{fr.Y(v):.1f}" stroke="{C["band"]}" stroke-dasharray="5 4"/>')
            out.append(f'<text x="{fr.l+fr.pw-2:.1f}" y="{fr.Y(v)-3:.1f}" text-anchor="end" font-size="11" fill="{C["band"]}">{lab}</text>')
    if limit is not None:
        out.append(f'<line x1="{fr.l}" y1="{fr.Y(limit):.1f}" x2="{fr.l+fr.pw:.1f}" y2="{fr.Y(limit):.1f}" stroke="{C["band"]}" stroke-width="1.2"/>')
    if N is not None:
        out.append(f'<line x1="{fr.X(N):.1f}" y1="{fr.t}" x2="{fr.X(N):.1f}" y2="{fr.t+fr.ph:.1f}" stroke="{C["c3"]}" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{fr.X(N)+4:.1f}" y="{fr.t+10:.1f}" fill="{C["c3"]}" font-size="12">N(ε)</text>')
    if connect:
        out.append(f'<polyline points="{" ".join(f"{fr.X(k):.1f},{fr.Y(v):.1f}" for k, v in vals)}" fill="none" stroke="{C["c1"]}" stroke-opacity="0.35"/>')
    for k, v in vals:
        inside = limit is not None and eps and abs(v - limit) < eps
        col = C["c1"] if (N is None or k >= N) else C["muted"]  # ∀n ≥ N
        out.append(f'<circle cx="{fr.X(k):.1f}" cy="{fr.Y(v):.1f}" r="2.8" fill="{col}"/>')
    out.append("</svg>")
    return "\n".join(out)


VENN_POS = {1: [(0, 0)], 2: [(-0.55, 0), (0.55, 0)], 3: [(-0.55, 0.35), (0.55, 0.35), (0, -0.55)]}


def venn(sets, shade=None, width=300, labels=None, universe=True, **_):
    k = len(sets); R = 1.0
    W = width; H = width * (0.68 if k < 3 else 0.9)
    sc = W / 4.2
    cx0, cy0 = W / 2, H / 2 + (0 if k < 3 else -sc * 0.05)
    cs = [(cx0 + dx * sc, cy0 - dy * sc) for dx, dy in VENN_POS[k]]
    u = uid()
    out = [f'<svg viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" xmlns="http://www.w3.org/2000/svg" {FONT}>', "<defs>"]
    for i, (x, y) in enumerate(cs):
        out.append(f'<clipPath id="in{u}{i}"><circle cx="{x:.1f}" cy="{y:.1f}" r="{R*sc:.1f}"/></clipPath>')
        out.append(f'<mask id="out{u}{i}"><rect width="{W}" height="{H}" fill="white"/><circle cx="{x:.1f}" cy="{y:.1f}" r="{R*sc:.1f}" fill="black"/></mask>')
    out.append("</defs>")
    if universe:
        out.append(f'<rect x="4" y="4" width="{W-8}" height="{H-8}" fill="none" stroke="{C["axis"]}" rx="4"/>')
        out.append(f'<text x="12" y="22" font-style="italic">U</text>')
    for reg in shade or []:
        out.append(_venn_region(reg, sets, u, W, H))
    for i, (x, y) in enumerate(cs):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{R*sc:.1f}" fill="none" stroke="{SERIES[i]}" stroke-width="1.8"/>')
        lx = x + (-1 if i == 0 else 1 if i == 1 else 0) * R * sc * 0.75
        ly = y - R * sc * 0.72 if i < 2 else y + R * sc * 0.72 + 12
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-style="italic" font-size="15" fill="{SERIES[i]}">{esc(sets[i])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _venn_region(reg, sets, u, W, H):
    """reg: либо атомарный регион 'A&~B&C', либо объединение через '|' ('A&~B|B&~A')."""
    parts = []
    for atom in reg.split("|"):
        inc, exc = [], []
        for t in atom.split("&"):
            t = t.strip()
            if t == "U": continue
            (exc if t.startswith("~") else inc).append(sets.index(t.lstrip("~")))
        g = f'<rect width="{W}" height="{H}" fill="{C["fill"]}" fill-opacity="0.28"/>'
        for j in exc: g = f'<g mask="url(#out{u}{j})">{g}</g>'
        for j in inc: g = f'<g clip-path="url(#in{u}{j})">{g}</g>'
        parts.append(g)
    return "\n".join(parts)


def mapping(A, B, arrows, names=("A", "B"), f="f", width=320, **_):
    n = max(len(A), len(B))
    H = 92 + n * 30; W = width
    xa, xb = W * 0.22, W * 0.78
    cyy = H / 2 + 4
    def ys(k): return [cyy + (i - (k - 1) / 2) * 30 for i in range(k)]
    ya, yb = ys(len(A)), ys(len(B))
    u = uid()
    out = [f'<svg viewBox="0 0 {W} {H:.0f}" width="{W}" xmlns="http://www.w3.org/2000/svg" {FONT}>',
           f'<defs><marker id="m{u}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,1 L10,5 L0,9 z" fill="{C["c1"]}"/></marker></defs>']
    for x, k, nm, col in ((xa, len(A), names[0], C["c1"]), (xb, len(B), names[1], C["c2"])):
        ry = k * 15 + 14
        out.append(f'<ellipse cx="{x:.1f}" cy="{cyy:.1f}" rx="{W*0.12:.1f}" ry="{ry:.1f}" fill="{col}" fill-opacity="0.07" stroke="{col}" stroke-width="1.5"/>')
        out.append(f'<text x="{x:.1f}" y="{cyy+ry+16:.1f}" text-anchor="middle" font-style="italic" font-size="15" fill="{col}">{esc(nm)}</text>')
    for i, s in enumerate(A):
        out.append(f'<circle cx="{xa:.1f}" cy="{ya[i]:.1f}" r="2.5"/><text x="{xa-8:.1f}" y="{ya[i]+4:.1f}" text-anchor="end">{esc(s)}</text>')
    for j, s in enumerate(B):
        out.append(f'<circle cx="{xb:.1f}" cy="{yb[j]:.1f}" r="2.5"/><text x="{xb+8:.1f}" y="{yb[j]+4:.1f}">{esc(s)}</text>')
    for i, j in arrows:
        out.append(f'<line x1="{xa+5:.1f}" y1="{ya[i]:.1f}" x2="{xb-5:.1f}" y2="{yb[j]:.1f}" stroke="{C["c1"]}" stroke-width="1.3" marker-end="url(#m{u})"/>')
    out.append(f'<text x="{W/2:.1f}" y="15" text-anchor="middle" font-style="italic" font-size="{15 if len(f) < 6 else 12}">{esc(f)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def flow(nodes, edges=None, dir="h", width=520, **_):
    """Цепочка утверждений в рамках со стрелками (⇒, ⇔, «по опр.»…)."""
    k = len(nodes); edges = edges or ["⇒"] * (k - 1)
    u = uid(); out = []
    if dir == "h":
        gap = 34; bw = (width - gap * (k - 1)) / k; bh = 46; H = bh + 8
        out.append(f'<svg viewBox="0 0 {width} {H}" width="{width}" xmlns="http://www.w3.org/2000/svg" {FONT} font-size="12">')
        for i, t in enumerate(nodes):
            x = i * (bw + gap)
            out.append(f'<rect x="{x+1:.1f}" y="4" width="{bw-2:.1f}" height="{bh}" rx="6" fill="#eef4ff" stroke="{C["c1"]}"/>')
            lines = _wrap(t, int(bw / 7))
            for li, ln in enumerate(lines):
                out.append(f'<text x="{x+bw/2:.1f}" y="{4+bh/2+4+(li-(len(lines)-1)/2)*14:.1f}" text-anchor="middle">{esc(ln)}</text>')
            if i < k - 1:
                out.append(f'<text x="{x+bw+gap/2:.1f}" y="{4+bh/2+6:.1f}" text-anchor="middle" font-size="18" fill="{C["c4"]}">{esc(edges[i])}</text>')
    else:
        bh = 34; gap = 26; H = k * bh + (k - 1) * gap + 8; bw = width * 0.8
        out.append(f'<svg viewBox="0 0 {width} {H}" width="{width}" xmlns="http://www.w3.org/2000/svg" {FONT} font-size="12">')
        for i, t in enumerate(nodes):
            y = 4 + i * (bh + gap)
            out.append(f'<rect x="{(width-bw)/2:.1f}" y="{y}" width="{bw:.1f}" height="{bh}" rx="6" fill="#eef4ff" stroke="{C["c1"]}"/>')
            out.append(f'<text x="{width/2:.1f}" y="{y+bh/2+4:.1f}" text-anchor="middle">{esc(t)}</text>')
            if i < k - 1:
                out.append(f'<text x="{width/2:.1f}" y="{y+bh+gap/2+6:.1f}" text-anchor="middle" font-size="16" fill="{C["c4"]}">{esc(edges[i])}</text>')
    out.append("</svg>")
    return "\n".join(out)


def chain(sets, maps, width=520, total=None, **_):
    """Цепочка множеств-овалов с отображениями между соседними: sets=[{"name":"A","elems":["x"]},...],
    maps=[{"f":"f","arrows":[[0,0]]}, ...], total — подпись сквозной дуги (напр. "g∘f")."""
    k = len(sets); W = width
    n = max(len(s_["elems"]) for s_ in sets); H = 70 + n * 28 + (56 if total else 0)
    cy = (H + (56 if total else 0)) / 2 + 2
    xs = [W * (0.12 + 0.76 * i / (k - 1)) for i in range(k)]
    def ys(m): return [cy + (i - (m - 1) / 2) * 28 for i in range(m)]
    u = uid()
    out = [f'<svg viewBox="0 0 {W} {H:.0f}" width="{W}" xmlns="http://www.w3.org/2000/svg" {FONT}>',
           f'<defs><marker id="m{u}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,1 L10,5 L0,9 z" fill="context-stroke"/></marker></defs>']
    pos = []
    for i, st in enumerate(sets):
        m = len(st["elems"]); ry = m * 14 + 14; col = SERIES[i % len(SERIES)]
        out.append(f'<ellipse cx="{xs[i]:.1f}" cy="{cy:.1f}" rx="{W*0.07:.1f}" ry="{ry:.1f}" fill="{col}" fill-opacity="0.07" stroke="{col}" stroke-width="1.5"/>')
        out.append(f'<text x="{xs[i]:.1f}" y="{cy+ry+16:.1f}" text-anchor="middle" font-style="italic" font-size="15" fill="{col}">{esc(st["name"])}</text>')
        pos.append(list(zip([xs[i]] * m, ys(m))))
        for (x, y), e in zip(pos[-1], st["elems"]):
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5"/><text x="{x+6:.1f}" y="{y-5:.1f}" font-size="12">{esc(e)}</text>')
    for i, mp in enumerate(maps):
        for a, b in mp["arrows"]:
            (x1, y1), (x2, y2) = pos[i][a], pos[i + 1][b]
            out.append(f'<line x1="{x1+4:.1f}" y1="{y1:.1f}" x2="{x2-5:.1f}" y2="{y2:.1f}" stroke="{C["ink"]}" stroke-width="1.3" marker-end="url(#m{u})"/>')
        out.append(f'<text x="{(xs[i]+xs[i+1])/2:.1f}" y="{cy-n*14-10:.1f}" text-anchor="middle" font-style="italic" font-size="15">{esc(mp["f"])}</text>')
    if total:
        x1, x2 = xs[0], xs[-1]; top = cy - n * 14 - 40
        out.append(f'<path d="M{x1:.1f},{cy-n*14-12:.1f} C{x1+40:.1f},{top-10:.1f} {x2-40:.1f},{top-10:.1f} {x2-4:.1f},{cy-n*14-12:.1f}" fill="none" stroke="{C["c4"]}" stroke-dasharray="5 4" stroke-width="1.4" marker-end="url(#m{u})"/>')
        out.append(f'<text x="{(x1+x2)/2:.1f}" y="{top-12:.1f}" text-anchor="middle" font-style="italic" font-size="15" fill="{C["c4"]}">{esc(total)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def _wrap(s, n):
    words, lines, cur = s.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > n: lines.append(cur); cur = w
        else: cur = (cur + " " + w).strip()
    return lines + [cur] if cur else lines


def check_plot(spec):
    """Текстовая сверка: знаки, монотонность, разрывы, значения в характерных точках."""
    rep = []
    for fd in spec.get("funcs", []):
        f = compile_f(fd["f"])
        a, b = fd.get("domain", spec["xlim"]); a, b = _num(a), _num(b)
        brs = sample_branches(f, a, b, spec["ylim"], 2000)
        rep.append(f'{fd["f"]} на [{fmt(a)}, {fmt(b)}]: ветвей {len(brs)}')
        for br in brs:
            xs, ys = [p[0] for p in br], [p[1] for p in br]
            d = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
            mono = "возрастает" if all(v >= -1e-12 for v in d) else "убывает" if all(v <= 1e-12 for v in d) else "немонотонна"
            sgn = ("y>0" if min(ys) > 0 else "y<0" if max(ys) < 0 else "y≥0" if min(ys) >= 0 else
                   "y≤0" if max(ys) <= 0 else "меняет знак")
            rep.append(f'  x∈[{xs[0]:.3g}, {xs[-1]:.3g}]: {mono}, {sgn}, y от {ys[0]:.3g} до {ys[-1]:.3g}')
        probes = [v for v in (-2, -1, -0.5, 0, 0.5, 1, 2) if a <= v <= b]
        rep.append("  f: " + ", ".join(f"f({fmt(v)})={('—' if f(v) is None else format(f(v), '.3g'))}" for v in probes))
    return "\n".join(rep)


RENDER = {"geom": geom, "chain": chain, "plot": plot, "numberline": numberline, "sequence": sequence, "venn": venn, "mapping": mapping, "flow": flow}


def render(spec):
    spec = dict(spec)
    t = spec.pop("type")
    return RENDER[t](**{k: v for k, v in spec.items() if k != "id"})


def main():
    args = sys.argv[1:]
    if not args: print(__doc__); return
    specs = json.load(open(args[0], encoding="utf-8"))
    if isinstance(specs, dict): specs = [specs]
    if "--check" in args:
        for s in specs:
            if s["type"] == "plot": print(f'[{s.get("id")}]\n' + check_plot(s))
            if s["type"] == "geom": print(f'[{s.get("id")}]\n' + check_geom(s))
        return
    if "--inline" in args:
        # подстановка <!--fig:id--> в HTML: --inline src.html out.html
        i = args.index("--inline"); src, dst = args[i + 1], args[i + 2]
        page = open(src, encoding="utf-8").read()
        for s in specs:
            page = page.replace(f'<!--fig:{s["id"]}-->', render(s))
        missing = re.findall(r"<!--fig:([\w-]+)-->", page)
        if missing: sys.exit(f"нет спецификаций для рисунков: {missing}")
        open(dst, "w", encoding="utf-8").write(page)
        return
    outdir = args[args.index("-o") + 1] if "-o" in args else None
    for s in specs:
        svg = render(s)
        if outdir:
            os.makedirs(outdir, exist_ok=True)
            open(os.path.join(outdir, f'{s["id"]}.svg'), "w", encoding="utf-8").write(svg)
        else:
            print(f'<!-- {s.get("id")} -->\n{svg}')


if __name__ == "__main__":
    main()
