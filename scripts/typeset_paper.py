"""Semantic bilingual typesetting: equal text styles, native vector mathematics.

Uses ReportLab for text flow and Matplotlib's LaTeX-style MathText renderer for
mathematics. Original figure PDF regions are embedded after text pagination.
"""
from __future__ import annotations

import copy
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import tempfile
import unicodedata

import pymupdf as fitz

BODY_SIZE = 11
BODY_LEADING = 16
PAGE_SIZE = (595.276, 841.89)
SIDE_MARGIN = 44
STYLES = {"body": (11, 16), "title": (17, 23), "heading": (12.5, 18.5),
          "caption": (9.5, 13.5), "meta": (9.5, 14), "reference": (9.5, 13.5)}
PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|PLACEHOLDER)\b|待翻译|此处填入|译文占位", re.I)


def _inside(a, b):
    a, b = fitz.Rect(a), fitz.Rect(b)
    return (a.x0 <= b.x0 + .05 and a.y0 <= b.y0 + .05 and
            a.x1 >= b.x1 - .05 and a.y1 >= b.y1 - .05)


def _overlaps(a, b):
    r = fitz.Rect(a) & fitz.Rect(b)
    return not r.is_empty and r.width > .05 and r.height > .05


def canonical(text):
    """Comparison key for source English: ignores line wraps, punctuation, math styling."""
    greek = {"alpha":"α", "beta":"β", "gamma":"γ", "delta":"δ", "epsilon":"ε",
             "varepsilon":"ε", "theta":"θ", "lambda":"λ", "mu":"μ", "nu":"ν",
             "pi":"π", "rho":"ρ", "sigma":"σ", "tau":"τ", "phi":"φ", "omega":"ω"}
    text = re.sub(r"\\(?:mathrm|mathbf|mathit|mathsf|text|operatorname|left|right|big|Big)\b", "", text)
    for command, char in greek.items():
        text = re.sub(r"\\" + command + r"\b", char, text)
    text = re.sub(r"\\(?=[A-Za-z])", "", text)
    for command, char in greek.items():
        if command != "varepsilon":
            text = text.replace(char, command)
    text = unicodedata.normalize("NFKD", text).lower()
    return "".join(re.findall(r"[a-z0-9]+", text))


def _body_source(page, unit, excluded):
    lines = []
    for block in page.get_text("dict", clip=fitz.Rect(unit["bbox"]))["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            rect = fitz.Rect(line["bbox"])
            if any(_inside(r, rect) for r in excluded):
                continue
            text = "".join(s["text"] for s in line["spans"])
            lines.append((rect.y0, rect.x0, text))
    return "\n".join(s for _, _, s in sorted(lines))


def validate(data, source):
    errors = []
    for unit in data["units"]:
        ident = unit["id"]
        page = source[unit["page"]]
        blocks = unit.get("blocks")
        if not isinstance(blocks, list):
            errors.append(f"{ident}: structured blocks are missing; run typeset, then review the blocks")
            continue
        excluded, assets, en_parts = [], [], []
        for omission in unit.get("omit", []):
            r = omission.get("bbox")
            reason=omission.get("reason")
            boundary_ok=False
            if isinstance(r,list) and len(r)==4:
                region=fitz.Rect(r)
                header=region.y1<=page.rect.height*.15
                footer=region.y0>=page.rect.height*.85
                boundary_ok=not region.is_empty and region.height<=page.rect.height*.15 and (
                    header if reason=="running_header" else footer if reason=="running_footer" else header or footer)
            if reason not in ("running_header", "running_footer", "page_number") or not boundary_ok or not _inside(unit["bbox"], r):
                errors.append(f"{ident}: only explicit running headers/footers/page numbers may be omitted")
            else:
                excluded.append(r)
        for i, block in enumerate(blocks):
            name = f"{ident}/b{i+1}"
            kind = block.get("type")
            if kind in ("text", "references"):
                en, zh = block.get("en", ""), block.get("zh", "")
                if not isinstance(en, str) or not isinstance(zh, str):
                    errors.append(f"{name}: en and zh must be strings")
                    continue
                if not en.strip():
                    errors.append(f"{name}: original English is empty")
                if kind == "references":
                    if zh.strip():
                        errors.append(f"{name}: references must remain English only; remove zh")
                elif not re.search(r"[\u3400-\u9fff]", zh):
                    errors.append(f"{name}: missing Chinese translation")
                if PLACEHOLDER.search(en + zh):
                    errors.append(f"{name}: placeholder text")
                if block.get("style", "body") not in STYLES:
                    errors.append(f"{name}: invalid text style")
                en_parts.append(en)
                if en.count("$") % 2 or zh.count("$") % 2:
                    errors.append(f"{name}: unbalanced inline math delimiters")
            elif kind in ("equation", "figure"):
                r = block.get("bbox")
                if not isinstance(r, list) or len(r) != 4 or not all(isinstance(v, (float,int)) and math.isfinite(v) for v in r):
                    errors.append(f"{name}: source bbox is required")
                    continue
                if fitz.Rect(r).is_empty or not _inside(unit["bbox"], r):
                    errors.append(f"{name}: asset bbox must be inside its source unit")
                    continue
                excluded.append(r)
                assets.append(r)
                if kind == "equation" and not str(block.get("latex", "")).strip():
                    errors.append(f"{name}: equation needs LaTeX-style source")
                if kind == "figure" and block.get("en_caption"):
                    en_parts.append(block["en_caption"])
                    if not re.search(r"[\u3400-\u9fff]",block.get("zh_caption","")):
                        errors.append(f"{name}: missing Chinese figure caption")
            else:
                errors.append(f"{name}: unsupported block type {kind!r}")
        prose = unit.get("ocr_text","") if unit.get("ocr_reflow") else _body_source(page, unit, excluded)
        if unit.get("ocr_reflow") and (not unit.get("scan_checked") or not prose.strip()):
            errors.append(f"{ident}: scanned prose requires a checked, nonempty OCR transcription")
        if canonical(prose) != canonical("\n".join(en_parts)):
            errors.append(f"{ident}: reflowed English does not match source prose outside figures/equations/explicit footers")
        # Every visible original raster/vector graphic must be explicitly accounted for.
        for kind, geometries in (("image", [fitz.Rect(im["bbox"]) for im in page.get_image_info()]),
                                 ("vector", list(page.cluster_drawings()))):
            for r in geometries:
                if not _overlaps(unit["bbox"], r):
                    continue
                if kind == "image" and unit.get("ocr_reflow") and unit.get("scan_checked") and unit.get("ocr_text", "").strip():
                    continue
                if not any(_inside(a, r) for a in assets):
                    errors.append(f"{ident}: original {kind} is not covered by an equation/figure block")
        if page.get_image_info() and any(_overlaps(unit["bbox"], im["bbox"]) for im in page.get_image_info()) and not unit.get("scan_checked"):
            errors.append(f"{ident}: raster content needs visual label/OCR review")
    return errors


def seed(data):
    """Upgrade a copied job. Prose extraction is only a draft, never final parsing."""
    result = copy.deepcopy(data)
    result["render_mode"] = "typeset"
    result["review"] = {}
    for u in result["units"]:
        if "blocks" in u:
            continue
        en = re.sub(r"(?<=\w)-\n(?=\w)", "", u["source_text"])
        en = re.sub(r"(?<!\n)\n(?!\n)", " ", en).strip()
        reference = bool(re.match(r"\s*(References|Bibliography)\b", en, re.I))
        u["blocks"] = [{"type": "references" if reference else "text", "en": en,
                        "zh": "" if reference else u.get("zh", ""), "style": "reference" if reference else "body"}] if en else []
    return result


def math_pdf(expression, size=11, display=False):
    """Render supported TeX mathematics without invoking a shell or TeX commands."""
    from matplotlib import rc_context
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import math_to_image
    if "$" in expression:
        raise ValueError("Math fields omit outer dollar delimiters")
    if display:
        expression = re.sub(r"\\frac\b", lambda _: r"\dfrac", expression)
    stream = io.BytesIO()
    try:
        with rc_context({"mathtext.fontset":"stix", "text.usetex":False}):
            math_to_image("$" + expression + "$", stream,
                          prop=FontProperties(size=size), format="pdf", dpi=300)
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"Cannot render math {expression!r}: {exc}. Split aligned equations into supported rows; never silently use raw TeX.") from exc
    return stream.getvalue()


def render_document(data, source, output, report, draft=False):
    from matplotlib import get_data_path
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase.pdfmetrics import registerFontFamily
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, KeepTogether, Flowable
    from reportlab.platypus import CondPageBreak

    pdfmetrics.registerFont(TTFont("PaperCJK", io.BytesIO(fitz.Font("cjk").buffer)))
    fonts = Path(get_data_path()) / "fonts" / "ttf"
    pdfmetrics.registerFont(TTFont("PaperLatin", str(fonts / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("PaperLatinBold", str(fonts / "DejaVuSans-Bold.ttf")))
    registerFontFamily("PaperLatin", normal="PaperLatin", bold="PaperLatinBold", italic="PaperLatin", boldItalic="PaperLatinBold")
    registerFontFamily("PaperCJK", normal="PaperCJK", bold="PaperCJK", italic="PaperCJK", boldItalic="PaperCJK")
    output = Path(output).resolve()
    width, height = PAGE_SIZE
    body_width = width - 2 * SIDE_MARGIN
    vector_placements, text_placements, cache = [], [], {}

    with tempfile.TemporaryDirectory(prefix="bilingual-typeset-") as temp_dir:
        work = Path(temp_dir)

        def math_asset(expr, size, display=False):
            key = (expr, size, display)
            if key not in cache:
                raw = math_pdf(expr, size, display)
                doc = fitz.open(stream=raw, filetype="pdf")
                w,h = doc[0].rect.width, doc[0].rect.height
                png = work / (hashlib.sha256(repr(key).encode()).hexdigest() + ".png")
                doc[0].get_pixmap(matrix=fitz.Matrix(6,6), alpha=True).save(png)
                doc.close()
                cache[key] = (raw, w, h, png)
            return cache[key]

        def markup(text, language, size):
            pieces = []
            for i, part in enumerate(re.split(r"\$([^$]+)\$", text)):
                if i % 2:
                    _, mw, mh, png = math_asset(part, size)
                    pieces.append(f'<img src="{png}" width="{mw}" height="{mh}" valign="-3"/>')
                else:
                    for run in re.findall(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+|[^\u3000-\u303f\u3400-\u9fff\uff00-\uffef]+", part):
                        font = "PaperCJK" if re.search(r"[\u3000-\u303f\u3400-\u9fff\uff00-\uffef]", run) else "PaperLatin"
                        pieces.append(f'<font name="{font}">{html.escape(run).replace(chr(10), "<br/>")}</font>')
            return "".join(pieces)

        class TracedParagraph(Paragraph):
            def __init__(self, value, style, trace=None, **kwargs):
                super().__init__(value, style, **kwargs)
                self.trace = trace or {}
            def draw(self):
                super().draw()
                x,y = self.canv.absolutePosition(0,0)
                text_placements.append(dict(self.trace, output_page=self.canv.getPageNumber()-1,
                    bbox=[x,height-y-self.height,x+self.width,height-y], font_size=self.style.fontSize,
                    leading=self.style.leading))

        class VectorBlock(Flowable):
            def __init__(self, trace, source_doc=None, source_page=0, clip=None, pdf_bytes=None, number=""):
                super().__init__()
                self.trace, self.source_doc, self.source_page, self.clip = trace, source_doc, source_page, clip
                self.pdf_bytes, self.number = pdf_bytes, str(number)
                if pdf_bytes:
                    with fitz.open(stream=pdf_bytes,filetype="pdf") as d:
                        self.source_width, self.source_height = d[0].rect.width, d[0].rect.height
                    self.factor = min(1, (body_width - 45) / self.source_width)
                else:
                    self.source_width, self.source_height = clip.width, clip.height
                    self.factor = min(1.25,body_width/self.source_width,340/self.source_height)
                if pdf_bytes and self.factor < .85:
                    raise ValueError(f"{trace['id']}: equation too wide; split it into shorter equation blocks")
                self.width, self.height = body_width, self.source_height*self.factor + 14
            def draw(self):
                x,y = self.canv.absolutePosition(0,0)
                h,w = self.source_height*self.factor,self.source_width*self.factor
                start = x+(body_width-w)/2
                rect=[start,height-(y+7+h),start+w,height-y-7]
                vector_placements.append(dict(self.trace,output_page=self.canv.getPageNumber()-1,
                    bbox=rect,source_page=self.source_page,source_bbox=list(self.clip) if self.clip else self.trace.get("source_bbox"),
                    pdf_bytes=self.pdf_bytes, number=self.number))
                if self.number:
                    self.canv.setFont("PaperLatin",BODY_SIZE)
                    self.canv.setFillColor(HexColor("#253743"))
                    self.canv.drawRightString(body_width,7+h/2-3,f"({self.number})")

        styles={}
        for style,(size,leading) in STYLES.items():
            for language in ("zh","en"):
                styles[(style,language)]=ParagraphStyle(style+language,fontName="PaperLatin",fontSize=size,leading=leading,
                    textColor=HexColor("#152D3A" if style in ("title","heading") else "#24313B"),
                    alignment=TA_CENTER if style=="title" else TA_LEFT, wordWrap="CJK" if language=="zh" else None,
                    spaceBefore=0,spaceAfter=0,allowWidows=0,allowOrphans=0,
                    splitLongWords=True)
        story=[]
        for unit in data["units"]:
            for index,block in enumerate(unit.get("blocks",[])):
                ident=f"{unit['id']}/b{index+1}"
                kind=block["type"]
                trace={"id":ident,"source_unit":unit["id"],"type":kind}
                if kind in ("text","references"):
                    style=block.get("style","reference" if kind=="references" else "body")
                    size,_=STYLES[style]
                    paired=[]
                    if style in ("heading","title"):
                        paired.append(Spacer(1,10 if style=="heading" else 3))
                    languages=("en",) if kind=="references" else ("zh","en")
                    for language in languages:
                        text=block.get(language,"")
                        if not text.strip():
                            text="[未翻译草稿]" if language=="zh" else "[UNTRANSCRIBED DRAFT]"
                        paragraph=TracedParagraph(markup(text,language,size),styles[(style,language)],dict(trace,language=language,style=style))
                        paired.append(paragraph)
                        paired.append(Spacer(1,4 if language=="zh" else 8))
                    pair_height = sum(p.wrap(body_width,10000)[1] for p in paired)
                    if pair_height > height-100:
                        raise ValueError(f"{ident}: bilingual passage too long; split it into shorter corresponding text blocks")
                    pair_group=KeepTogether(paired)
                    pair_group.paper_children=paired
                    pair_group.paper_heading=style in ("heading","title")
                    story.append(pair_group)
                elif kind=="equation":
                    raw,_,_,_=math_asset(block["latex"],BODY_SIZE,True)
                    story.append(VectorBlock(dict(trace,source_bbox=block["bbox"]),source_page=unit["page"],pdf_bytes=raw,number=block.get("number","")))
                    story.append(Spacer(1,6))
                elif kind=="figure":
                    clip=fitz.Rect(block["bbox"])
                    group=[]
                    if block.get("zh"):
                        group.append(TracedParagraph(markup(block["zh"],"zh",9.5),styles[("caption","zh")],
                            dict(trace,language="zh",style="caption",type="figure-label-translation")))
                        group.append(Spacer(1,6))
                    group.append(VectorBlock(trace,source,unit["page"],clip))
                    if block.get("en_caption"):
                        for language in ("zh","en"):
                            group.append(TracedParagraph(markup(block[language+"_caption"],language,9.5),styles[("caption",language)],
                                dict(trace,language=language,style="caption",type="figure-caption")))
                            group.append(Spacer(1,4))
                    group.append(Spacer(1,10))
                    group_height=sum(p.wrap(body_width,10000)[1] for p in group)
                    if group_height>height-100:
                        raise ValueError(f"{ident}: complete figure/caption group too tall; review its layout")
                    figure_group=KeepTogether(group)
                    figure_group.paper_children=group
                    story.append(figure_group)
        # Flatten each heading and its next complete pair into one atomic group.
        # Nested KeepTogether.keepWithNext does not reliably bind those pairs.
        grouped,pending=[],[]
        for flow in story:
            if getattr(flow,"paper_heading",False):
                pending.extend(flow.paper_children)
                continue
            if pending:
                children=pending+getattr(flow,"paper_children",[flow])
                if sum(p.wrap(body_width,10000)[1] for p in children)>height-100:
                    raise ValueError("Heading and following passage are too long; shorten the corresponding passage")
                grouped.append(KeepTogether(children))
                pending=[]
            else:
                grouped.append(flow)
        if pending:
            raise ValueError("A final heading has no following content")
        story=grouped
        if not story:
            story=[Spacer(1,1)]

        def page_furniture(canvas,doc):
            canvas.saveState()
            canvas.setStrokeColor(HexColor("#B9CBD4")); canvas.setLineWidth(.45)
            canvas.line(SIDE_MARGIN,height-33,width-SIDE_MARGIN,height-33)
            canvas.setFont("PaperLatin",7.5);canvas.setFillColor(HexColor("#70818B"))
            canvas.drawString(SIDE_MARGIN,height-25,"BILINGUAL RESEARCH EDITION")
            canvas.drawRightString(width-SIDE_MARGIN,23,str(doc.page))
            if draft:
                canvas.drawString(SIDE_MARGIN,23,"DRAFT - NOT REVIEWED")
            canvas.restoreState()
        base=work/"text.pdf"
        doc=BaseDocTemplate(str(base),pagesize=PAGE_SIZE,leftMargin=SIDE_MARGIN,rightMargin=SIDE_MARGIN,
            topMargin=48,bottomMargin=43,title="Chinese-English bilingual paper",author="")
        frame=Frame(SIDE_MARGIN,43,body_width,height-48-43,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)
        doc.addPageTemplates(PageTemplate(id="paper",frames=frame,onPage=page_furniture))
        doc.build(story)
        final=fitz.open(base)
        assets=[]
        for position in vector_placements:
            p=final[position["output_page"]]
            target=fitz.Rect(position["bbox"])
            raw=position.pop("pdf_bytes")
            if raw:
                with fitz.open(stream=raw,filetype="pdf") as equation:
                    p.show_pdf_page(target,equation,0)
                position["renderer"]="latex-style MathText / vector PDF"
                position["math_sha256"]=hashlib.sha256(raw).hexdigest()
            else:
                src=source[position["source_page"]]
                if list(src.annots() or []) or list(src.widgets() or []):
                    # Include visible annotations while preserving the exact figure crop.
                    raster=fitz.open(); rp=raster.new_page(width=src.rect.width,height=src.rect.height)
                    rp.insert_image(rp.rect,pixmap=src.get_pixmap(matrix=fitz.Matrix(240/72,240/72),annots=True))
                    p.show_pdf_page(target,raster,0,clip=fitz.Rect(position["source_bbox"]))
                    raster.close()
                else:
                    p.show_pdf_page(target,source,position["source_page"],clip=fitz.Rect(position["source_bbox"]))
                position["renderer"]="original PDF figure"
            assets.append(position)
        final.set_metadata({"title":"Chinese-English bilingual paper","creator":"paper-bilingual-pdf 2.0.0",
                            "subject":f"Source SHA256: {data['source_sha256']}"})
        output.parent.mkdir(parents=True,exist_ok=True)
        target=work/"final.pdf";final.save(target,garbage=4,deflate=True);final.close()
        import os,shutil
        fd,atomic=tempfile.mkstemp(suffix=".pdf",dir=output.parent);os.close(fd)
        try:
            shutil.copyfile(target,atomic);os.replace(atomic,output)
        finally:
            if os.path.exists(atomic):os.unlink(atomic)
    report.update({"render_mode":"typeset","draft":draft,"output":str(output),"body_font_size":BODY_SIZE,
        "body_leading":BODY_LEADING,"side_margin":SIDE_MARGIN,"text_placements":text_placements,"assets":assets,
        "references":"English only","source_partition_retained":True,
        "output_sha256":hashlib.sha256(output.read_bytes()).hexdigest()})
    return report
