#!/usr/bin/env python3
"""Reviewed Chinese-first bilingual PDFs with lossless source-region preservation.

No network access, translation API, model credential, or shell execution is used.
The assistant translates manifest units; this program guards and composes them.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

try:
    import pymupdf as fitz
except ImportError:
    raise SystemExit('Install PyMuPDF >=1.26,<2: python -m pip install "PyMuPDF>=1.26,<2"')

VERSION = "1.1.0"
EPS = 0.04
MARGIN = 36
MAX_HEIGHT = 14000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_job(job):
    job = Path(job)
    data = json.loads((job / "manifest.json").read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("Unsupported job schema")
    if sha256(job / "source.pdf") != data["source_sha256"]:
        raise ValueError("Source PDF changed; create a new translation job")
    return data, fitz.open(job / "source.pdf")


def digest(data):
    content = {k: v for k, v in data.items() if k != "review"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def rect_list(r):
    return [round(float(x), 4) for x in r]


def objects(page):
    """Protected geometries, including text inside figures and vector drawings."""
    rects = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] == 0:
            for line in block["lines"]:
                rects.append(("text", fitz.Rect(line["bbox"])))
    for im in page.get_image_info():
        rects.append(("image", fitz.Rect(im["bbox"])))
    for r in page.cluster_drawings():
        rects.append(("vector", fitz.Rect(r)))
    for a in page.annots() or []:
        rects.append(("annotation", a.rect))
    for w in page.widgets() or []:
        rects.append(("widget", w.rect))
    # Clip geometries to the visible page; hairlines need a small positive box.
    result = []
    for kind, r in rects:
        if r.width < EPS:
            r.x0 -= EPS
            r.x1 += EPS
        if r.height < EPS:
            r.y0 -= EPS
            r.y1 += EPS
        r = r & page.rect
        if not r.is_empty:
            result.append((kind, r))
    return result


def contains(outer, inner):
    return (outer.x0 <= inner.x0 + EPS and outer.y0 <= inner.y0 + EPS
            and outer.x1 >= inner.x1 - EPS and outer.y1 >= inner.y1 - EPS)


def overlaps(a, b):
    r = a & b
    return not r.is_empty and r.width > EPS and r.height > EPS


def merge_intervals(intervals, gap=0):
    out = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def page_regions(page, obs, mode):
    """Conservative mixed-width column proposal; caller still reviews it."""
    w, h = page.rect.width, page.rect.height
    mid = w / 2
    blocks = [fitz.Rect(b[:4]) for b in page.get_text("blocks") if b[6] == 0]
    left = [r for r in blocks if r.x1 < mid + 8 and r.width > w * .22]
    right = [r for r in blocks if r.x0 > mid - 8 and r.width > w * .22]
    use_columns = mode == "double" or (mode == "auto" and len(left) >= 2 and len(right) >= 2)
    if not use_columns:
        return [page.rect]
    # A narrow center gutter is safe only outside spanning content bands.
    spans = [[max(0, r.y0 - 2), min(h, r.y1 + 2)]
             for _, r in obs if r.x0 < mid - 1 and r.x1 > mid + 1]
    spans = merge_intervals(spans, gap=10)
    # Expand both edges until neither cuts any object in either column.
    for _ in range(len(obs) + 1):
        old = [v[:] for v in spans]
        for s in spans:
            for _, r in obs:
                if r.y0 + EPS < s[0] < r.y1 - EPS:
                    s[0] = max(0, r.y0 - 2)
                if r.y0 + EPS < s[1] < r.y1 - EPS:
                    s[1] = min(h, r.y1 + 2)
        spans = merge_intervals(spans)
        if old == spans:
            break
    regions, y = [], 0
    for a, b in spans + [[h, h]]:
        if a > y + EPS:
            regions.extend([fitz.Rect(0, y, mid, a), fitz.Rect(mid, y, w, a)])
        if b > a + EPS:
            regions.append(fitz.Rect(0, a, w, b))
        y = b
    return regions or [page.rect]


def split_region(page, region, obs, target):
    protected = [r for _, r in obs if overlaps(region, r)]
    occupied = merge_intervals([(max(region.y0, r.y0 - 1), min(region.y1, r.y1 + 1))
                                for r in protected])
    gaps, last = [], region.y0
    for a, b in occupied:
        if a > last + 2:
            gaps.append((last + a) / 2)
        last = max(last, b)
    # Prefer true block boundaries to arbitrary line breaks.
    boundaries = [b[3] for b in page.get_text("blocks")
                  if b[6] == 0 and contains(region, fitz.Rect(b[:4]))]
    cuts, start = [], region.y0
    while region.y1 - start > target * 1.35:
        candidates = [g for g in gaps if start + target * .40 < g < start + target * 1.45]
        if not candidates:
            candidates = [g for g in gaps if g > start + target * .40]
        if not candidates:
            break
        def score(g):
            paragraph = any(0 <= g - b < 14 for b in boundaries)
            return abs(g - (start + target)) - (target * .25 if paragraph else 0)
        cut = min(candidates, key=score)
        if cut >= region.y1 - 15:
            break
        cuts.append(cut)
        start = cut
    edges = [region.y0] + cuts + [region.y1]
    return [fitz.Rect(region.x0, a, region.x1, b) for a, b in zip(edges, edges[1:])]


def source_text(page, rect):
    return page.get_text("text", clip=rect, sort=True).strip()


def new_unit(page, rect, ident):
    unit = {"id": ident, "page": page.number, "bbox": rect_list(rect),
            "source_text": source_text(page, rect), "zh": "", "ocr_text": "",
            "scan_checked": False}
    unit["geometry_stamp"] = hashlib.sha256(json.dumps(
        [unit["page"], unit["bbox"], unit["source_text"]]).encode()).hexdigest()
    return unit


def geometry_errors(data, doc):
    errors, page_units, ids = [], {}, set()
    page_order = [u.get("page") for u in data.get("units", [])]
    if all(isinstance(n, int) for n in page_order) and page_order != sorted(page_order):
        errors.append("Source page reading order changed")
    if data.get("pages") != [rect_list(p.rect) for p in doc]:
        errors.append("Source dimensions/page count do not match manifest")
    for u in data.get("units", []):
        ident = u.get("id")
        if not isinstance(ident, str) or not ident or ident in ids:
            errors.append(f"Duplicate/invalid unit ID: {ident}")
        ids.add(ident)
        n = u.get("page")
        if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(doc):
            errors.append(f"{ident}: invalid source page")
            continue
        b = u.get("bbox")
        if not isinstance(b, list) or len(b) != 4 or not all(
                isinstance(v, (int, float)) and math.isfinite(v) for v in b):
            errors.append(f"{ident}: invalid bbox")
            continue
        r = fitz.Rect(b)
        if r.is_empty or r.is_infinite or not contains(doc[n].rect, r):
            errors.append(f"{ident}: region is empty/outside page")
            continue
        page_units.setdefault(n, []).append((u, r))
    for n, page in enumerate(doc):
        entries = page_units.get(n, [])
        # Exact planar partition using all x/y edges, not just summed areas.
        xs = sorted({page.rect.x0, page.rect.x1} | {v for _, r in entries for v in (r.x0, r.x1)})
        ys = sorted({page.rect.y0, page.rect.y1} | {v for _, r in entries for v in (r.y0, r.y1)})
        bad = False
        for a, b in zip(xs, xs[1:]):
            for c, d in zip(ys, ys[1:]):
                if b - a <= .00001 or d - c <= .00001:
                    continue
                x, y = (a + b) / 2, (c + d) / 2
                count = sum(r.x0 <= x < r.x1 and r.y0 <= y < r.y1 for _, r in entries)
                if count != 1:
                    errors.append(f"Page {n+1}: coverage {'gap' if count == 0 else 'overlap'} at ({x:.2f},{y:.2f})")
                    bad = True
                    break
            if bad:
                break
        for kind, r in objects(page):
            if not any(contains(region, r) for _, region in entries):
                errors.append(f"Page {n+1}: a {kind} object is cut at {rect_list(r)}")
        for u, r in entries:
            if u.get("source_text") != source_text(page, r):
                errors.append(f"{u['id']}: source extraction/geometry changed; run refresh")
    return errors


def text_errors(data, doc):
    errors = []
    images = {p.number: [fitz.Rect(im["bbox"]) for im in p.get_image_info()] for p in doc}
    for u in data.get("units", []):
        ident = u["id"]
        en = (u.get("source_text", "") + "\n" + u.get("ocr_text", "")).strip()
        zh = u.get("zh", "")
        if not isinstance(zh, str):
            errors.append(f"{ident}: zh must be a string")
            continue
        is_text = len(re.findall(r"[A-Za-z]", en)) >= 4
        if is_text and not re.search(r"[\u3400-\u9fff]", zh):
            errors.append(f"{ident}: missing Chinese translation")
        if re.search(r"\b(?:TODO|TBD|PLACEHOLDER)\b|待翻译|此处填入|译文占位", zh, re.I):
            errors.append(f"{ident}: placeholder translation")
        if not en and not u.get("scan_checked"):
            errors.append(f"{ident}: image-only/empty extraction needs visual scan review")
        elif not u.get("scan_checked") and any(
                overlaps(fitz.Rect(u["bbox"]), r) for r in images[u["page"]]):
            errors.append(f"{ident}: raster content needs visual label/OCR review, even with an extractable caption")
    return errors


def check_job(data, doc, require_review=False):
    errors = geometry_errors(data, doc)
    geometry_ok = not errors
    # Avoid indexing malformed units when geometry is already invalid.
    if not errors:
        errors.extend(text_errors(data, doc))
    if require_review:
        review = data.get("review", {})
        for item in ("layout", "translation"):
            if review.get(item) != digest(data):
                errors.append(f"Missing/stale {item} review; inspect and approve again")
    return {"ok": not errors, "errors": errors, "source_pages": len(doc),
            "units": len(data.get("units", [])), "source_sha256": data["source_sha256"],
            "coverage": "100% with no overlap" if geometry_ok else "invalid",
            "semantic_fidelity": "Requires visual and linguistic review; not automatically certified"}


def layout_proof(job, data, doc):
    proof = fitz.open()
    for n, page in enumerate(doc):
        p = proof.new_page(width=page.rect.width, height=page.rect.height)
        p.insert_image(p.rect, pixmap=page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), annots=True))
        for i, u in enumerate(data["units"]):
            if u["page"] != n:
                continue
            r = fitz.Rect(u["bbox"])
            color = ((i % 3 == 0) * .7, (i % 3 == 1) * .6, (i % 3 == 2) * .8)
            p.draw_rect(r, color=color, width=.8)
            label = f"{i+1}: {u['id']}"
            p.insert_text((r.x0 + 3, r.y0 + 11), label, fontsize=8, color=color)
        p.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(Path(job) / f"layout-{n+1:03d}.png")
    proof.save(Path(job) / "layout.pdf", garbage=4, deflate=True)
    proof.close()


def prepare(input_pdf, job, layout="auto", target=160):
    job = Path(job)
    if job.exists() and any(job.iterdir()):
        raise ValueError("Job directory is not empty; use refresh to resume or choose a new directory")
    job.mkdir(parents=True, exist_ok=True)
    with fitz.open(input_pdf) as doc:
        if doc.needs_pass:
            raise ValueError("Encrypted PDF: provide an unlocked copy")
        if any(p.rotation != 0 for p in doc):
            raise ValueError("Rotated page: normalize page rotation before creating the job")
        if not len(doc):
            raise ValueError("Empty source PDF")
    shutil.copyfile(input_pdf, job / "source.pdf")
    doc = fitz.open(job / "source.pdf")
    data = {"schema": 1, "tool_version": VERSION, "source_sha256": sha256(job / "source.pdf"),
            "pages": [rect_list(p.rect) for p in doc], "glossary": {}, "review": {}, "units": []}
    for page in doc:
        obs = objects(page)
        regions = page_regions(page, obs, layout)
        parts = [r for region in regions for r in split_region(page, region, obs, target)]
        for j, r in enumerate(parts):
            data["units"].append(new_unit(page, r, f"p{page.number+1:03d}-u{j+1:03d}"))
    errors = geometry_errors(data, doc)
    # If rounded proposed boundaries cut anything, fall back for that source page.
    if errors:
        # Keep fallback intentionally simple: full pages; visual review can refine.
        data["units"] = [new_unit(p, p.rect, f"p{p.number+1:03d}-u001") for p in doc]
    errors = geometry_errors(data, doc)
    if errors:
        raise ValueError("Cannot construct a safe layout: " + "; ".join(errors))
    write_json(job / "manifest.json", data)
    layout_proof(job, data, doc)
    doc.close()
    return {"job": str(job.resolve()), "units": len(data["units"]),
            "next": "Inspect layout, refine if needed, translate zh fields, check, approve, render"}


def refresh(job):
    data, doc = load_job(job)
    for u in data["units"]:
        n = u["page"]
        current = source_text(doc[n], fitz.Rect(u["bbox"]))
        stamp = hashlib.sha256(json.dumps([n, u["bbox"], current]).encode()).hexdigest()
        # Geometry stamp is created on prepare and subsequent refreshes.
        if u.get("geometry_stamp") != stamp:
            u["zh"] = ""
            u["ocr_text"] = ""
            u["scan_checked"] = False
        u["source_text"] = current
        u["geometry_stamp"] = stamp
    data["review"] = {}
    errors = geometry_errors(data, doc)
    if errors:
        doc.close()
        raise ValueError("; ".join(errors))
    write_json(Path(job) / "manifest.json", data)
    layout_proof(job, data, doc)
    doc.close()
    return {"refreshed": True, "review_invalidated": True}


def chinese_block(text, width):
    """Render literal Chinese once, measure exact used height without shrinking."""
    temp = fitz.open()
    p = temp.new_page(width=width, height=MAX_HEIGHT)
    body = "".join("<p>" + html.escape(part) + "</p>" for part in text.split("\n") if part.strip())
    css = "* {margin:0;padding:0} body {font-family:sans-serif;font-size:11pt;line-height:1.6;color:#18334a} p {margin-bottom:5pt}"
    spare, scale = p.insert_htmlbox(fitz.Rect(0, 0, width, MAX_HEIGHT), body, css=css, scale_low=1)
    if spare < 0 or scale < .999:
        temp.close()
        raise ValueError("Chinese passage is too long; split the unit at a safe source boundary")
    height = max(1, math.ceil(MAX_HEIGHT - spare))
    return temp, height


def visible_bounds(page, region, zoom=2):
    """Bound every nonwhite rendered pixel, with a small protective inset.

    Check exact white rather than using a threshold: faint strokes, annotations,
    scanned labels and vector-only graphics all participate. No Pillow is needed.
    The raster is used only to measure whitespace; originals remain vector crops.
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=region,
                          colorspace=fitz.csRGB, alpha=False, annots=True)
    samples = pix.samples
    left, top, right, bottom = pix.width, pix.height, 0, 0
    for row_index in range(pix.height):
        row = samples[row_index * pix.stride:row_index * pix.stride + pix.width * 3]
        nonwhite = row.strip(b"\xff")
        if not nonwhite:
            continue
        start = len(row) - len(row.lstrip(b"\xff"))
        end = len(row.rstrip(b"\xff"))
        left = min(left, start // 3)
        right = max(right, (end + 2) // 3)
        top = min(top, row_index)
        bottom = row_index + 1
    if right <= left or bottom <= top:
        return None
    padding = .75
    return fitz.Rect(max(region.x0, (pix.x + left) / zoom - padding),
                     max(region.y0, (pix.y + top) / zoom - padding),
                     min(region.x1, (pix.x + right) / zoom + padding),
                     min(region.y1, (pix.y + bottom) / zoom + padding))


def display_regions(data, source):
    """Trim only blank display padding; keep the reviewed partition unchanged.

    Use shared horizontal bounds for each original page/column so short headings
    and equations inherit their column's body width rather than becoming huge.
    """
    ink, columns = {}, {}
    for u in data["units"]:
        region = fitz.Rect(u["bbox"])
        bounds = visible_bounds(source[u["page"]], region)
        ink[u["id"]] = bounds
        if bounds is None:
            continue
        key = (u["page"], region.x0, region.x1)
        if key in columns:
            columns[key][0] = min(columns[key][0], bounds.x0)
            columns[key][1] = max(columns[key][1], bounds.x1)
        else:
            columns[key] = [bounds.x0, bounds.x1]
    clips = {}
    for u in data["units"]:
        bounds = ink[u["id"]]
        if bounds is None:
            clips[u["id"]] = None
            continue
        region = fitz.Rect(u["bbox"])
        x0, x1 = columns[(u["page"], region.x0, region.x1)]
        clips[u["id"]] = fitz.Rect(x0, bounds.y0, x1, bounds.y1)
    return clips, ink


def render(job, output, draft=False):
    data, source = load_job(job)
    report = check_job(data, source, require_review=not draft)
    if draft:
        errors = geometry_errors(data, source)
        if errors:
            source.close()
            raise ValueError("; ".join(errors))
    elif not report["ok"]:
        source.close()
        raise ValueError("; ".join(report["errors"]))
    output = Path(output).resolve()
    if output == (Path(job) / "source.pdf").resolve():
        raise ValueError("Output cannot overwrite source.pdf")
    output.parent.mkdir(parents=True, exist_ok=True)
    display_clips, ink_bounds = display_regions(data, source)
    width = max(595, max((r.width for r in display_clips.values() if r is not None),
                         default=0) + 2 * MARGIN)
    content_width = width - 2 * MARGIN
    out, page, y, placements = fitz.open(), None, MARGIN, []
    raster_pages = {}
    for u in data["units"]:
        src = source[u["page"]]
        clip = display_clips[u["id"]]
        zh = u.get("zh", "").strip()
        if draft and not zh:
            zh = "[UNTRANSLATED DRAFT / 未翻译草稿]"
        if clip is None and not zh:
            placements.append({"id": u["id"], "source_page": u["page"],
                               "source_bbox": u["bbox"], "render_bbox": None,
                               "ink_bbox": None, "output_page": None,
                               "original_bbox": None, "chinese_bbox": None,
                               "original_mode": "blank"})
            continue
        temp, zh_height = chinese_block(zh, content_width) if zh else (None, 0)
        factor = content_width / clip.width if clip is not None else 1
        sw, sh = (content_width, clip.height * factor) if clip is not None else (0, 0)
        pair_height = 13 + zh_height + (8 if zh else 0) + sh + 18
        page_height = max(842, math.ceil(pair_height + 2 * MARGIN))
        if page_height > MAX_HEIGHT:
            raise ValueError(f"{u['id']}: pair too tall; split the unit safely")
        if page is None or y + pair_height > page.rect.height - MARGIN:
            page = out.new_page(width=width, height=page_height)
            y = MARGIN
            page.insert_text((MARGIN, page_height - 16), f"{'DRAFT | ' if draft else ''}Bilingual paper | {page.number+1}", fontsize=8, color=(.45, .48, .5))
        page.insert_text((MARGIN, y+8), f"SOURCE {u['page']+1} / {u['id']}", fontsize=7, color=(.4, .46, .5))
        y += 13
        zh_rect = None
        if temp:
            zh_rect = fitz.Rect(MARGIN, y, MARGIN + content_width, y + zh_height)
            page.show_pdf_page(zh_rect, temp, 0, clip=fitz.Rect(0, 0, content_width, zh_height), keep_proportion=False)
            temp.close()
            y += zh_height + 8
        target = fitz.Rect(MARGIN, y, width - MARGIN, y + sh) if clip is not None else None
        annotated = bool(list(src.annots() or []) or list(src.widgets() or []))
        if clip is None:
            pass
        elif annotated:
            if u["page"] not in raster_pages:
                raster_pages[u["page"]] = src.get_pixmap(matrix=fitz.Matrix(240/72, 240/72), annots=True)
            pix = raster_pages[u["page"]]
            # Crop the already rendered page in a temporary PDF to preserve appearances.
            raster = fitz.open()
            rp = raster.new_page(width=src.rect.width, height=src.rect.height)
            rp.insert_image(rp.rect, pixmap=pix)
            page.show_pdf_page(target, raster, 0, clip=clip, keep_proportion=False)
            raster.close()
        elif src.get_contents():
            page.show_pdf_page(target, source, u["page"], clip=clip, keep_proportion=False)
        placements.append({"id": u["id"], "source_page": u["page"], "source_bbox": u["bbox"],
                           "render_bbox": rect_list(clip) if clip is not None else None,
                           "ink_bbox": rect_list(ink_bounds[u["id"]]) if clip is not None else None,
                           "scale": factor,
                           "output_page": page.number, "original_bbox": rect_list(target) if target is not None else None,
                           "chinese_bbox": rect_list(zh_rect) if zh_rect else None,
                           "original_mode": "blank" if clip is None else "raster-240dpi" if annotated else "vector"})
        y += sh + 18
    if not len(out):
        out.new_page(width=width, height=842)
    out.set_metadata({"title": "Chinese-English bilingual paper", "creator": f"paper-bilingual-pdf {VERSION}",
                      "subject": f"Source SHA256: {data['source_sha256']}"})
    # Atomic output keeps a failed rerender from destroying the previous result.
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", dir=output.parent)
    os.close(fd)
    try:
        out.save(tmp_name, garbage=4, deflate=True)
        out.close()
        os.replace(tmp_name, output)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    source.close()
    report.update({"draft": draft, "placements": placements, "manifest_digest": digest(data),
                   "layout": "Chinese and originals share left/right body margins; only blank source padding is trimmed",
                   "output_sha256": sha256(output), "output": str(output)})
    write_json(output.with_suffix(".audit.json"), report)
    return {"output": str(output), "pairs": len(placements), "draft": draft}


def proof(pdf, directory, dpi=120):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    with fitz.open(pdf) as doc:
        for page in doc:
            path = directory / f"page-{page.number+1:03d}.png"
            page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), annots=True).save(path)
            paths.append(str(path.resolve()))
    return {"pages": paths}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("input_pdf")
    p.add_argument("job")
    p.add_argument("--layout", choices=("auto", "single", "double"), default="auto")
    p.add_argument("--target-height", type=float, default=160)
    for cmd in ("refresh", "check", "approve"):
        p = sub.add_parser(cmd)
        p.add_argument("job")
        if cmd == "check":
            p.add_argument("--require-review", action="store_true")
        if cmd == "approve":
            p.add_argument("--layout", action="store_true")
            p.add_argument("--translation", action="store_true")
    p = sub.add_parser("render")
    p.add_argument("job")
    p.add_argument("output")
    p.add_argument("--draft", action="store_true")
    p = sub.add_parser("proof")
    p.add_argument("pdf")
    p.add_argument("directory")
    p.add_argument("--dpi", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            if args.target_height < 40:
                raise ValueError("Target height must be at least 40 PDF points")
            result = prepare(args.input_pdf, args.job, args.layout, args.target_height)
        elif args.command == "refresh":
            result = refresh(args.job)
        elif args.command in ("check", "approve"):
            data, doc = load_job(args.job)
            result = check_job(data, doc, getattr(args, "require_review", False))
            doc.close()
            if args.command == "approve":
                if not args.layout and not args.translation:
                    raise ValueError("Select --layout and/or --translation after doing that review")
                if not result["ok"]:
                    raise ValueError("; ".join(result["errors"]))
                for name in ("layout", "translation"):
                    if getattr(args, name):
                        data.setdefault("review", {})[name] = digest(data)
                write_json(Path(args.job) / "manifest.json", data)
                result = {"approved": [name for name in ("layout", "translation") if getattr(args, name)]}
        elif args.command == "render":
            result = render(args.job, args.output, args.draft)
        else:
            if not 36 <= args.dpi <= 300:
                raise ValueError("Proof dpi must be between 36 and 300")
            result = proof(args.pdf, args.directory, args.dpi)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("ok") is False else 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
