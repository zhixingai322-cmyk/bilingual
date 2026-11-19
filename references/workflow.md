# Semantic bilingual layout, version 2

## Job and source geometry

`source.pdf` is an immutable original with a recorded SHA-256. The manifest's
`units` are an ordered, disjoint, complete partition of each source page, including
margins. Coordinates are `[x0,y0,x1,y1]` in source PDF points from the **top left**;
`page` is zero-based. Inspect `layout.pdf` and all layout PNGs before translation.
Refine the source regions only at safe boundaries and run `refresh` after doing so.
It clears changed regions' translations/blocks, regenerates draft blocks and
invalidates approvals. `typeset JOB` upgrades a pre-2.0 job without changing its
source. Neither proposal nor upgrade is an automatic semantic parser.

Keep the source partition for accounting; do **not** use its screenshots for body
text. Edit a unit's ordered `blocks` to reflow the actual English and Chinese.
Multiple short corresponding passages may belong to one rectangular source unit.

## Block schema

```json
{
  "id": "p001-model",
  "page": 0,
  "bbox": [0, 100, 306, 450],
  "source_text": "immutable extracted source text",
  "blocks": [
    {"type": "text", "style": "heading", "zh": "2. 模型", "en": "2. Model"},
    {"type": "text", "zh": "时延 $\\tau$ 决定响应。", "en": "The delay $\\tau$ determines the response."},
    {"type": "equation", "bbox": [42, 200, 290, 235], "latex": "R(t)=\\frac{1}{1+t^2}", "number": "1"}
  ]
}
```

Text styles are `body`, `title`, `heading`, `meta`, `caption`, and `reference`.
Body is 11 pt/16 pt in **both** languages; heading is 12.5 pt/18.5 pt, title
17 pt/23 pt, captions and references 9.5 pt/13.5 pt. Both body languages use the
same sans-serif typographic family, color, margins and paragraph spacing. Latin
and CJK font files differ only to provide the correct glyph coverage. Fonts are
embedded, including CJK punctuation. Inline math is rendered at the paragraph's
point size and placed at the baseline; it is not shown as raw dollar/TeX text.

A figure block preserves all original panels/axes/labels as one source crop:

```json
{
  "type": "figure",
  "bbox": [42, 490, 570, 675],
  "zh": "图中标签：加热通道、参考通道；横轴为时间（秒）。",
  "zh_caption": "图 1. 合成系统及其响应。",
  "en_caption": "Figure 1. The synthetic system and its response."
}
```

Exclude the original caption from the figure bbox and transcribe it into the
caption fields, so captions can use uniform typography. Keep every graphic panel,
label and axis inside the bbox. The complete figure/caption group stays together.
A source page containing annotations/widgets is rasterized at 240 dpi for its
figure crop to retain visible appearances; ordinary figure crops stay vector.

Reference headings and entries are English-only blocks:

```json
{"type": "references", "style": "heading", "en": "References"}
{"type": "references", "en": "[1] A. Author. Original paper title. Journal, 2026."}
```

Do not supply `zh` to reference blocks. `typeset` automatically recognizes a unit
beginning with References/Bibliography, but inspect the section boundary manually,
including appendices that may follow references.

## Original English and content accounting

Rejoin extraction line wraps and preserve English prose, headings, captions,
numbers, citations, names and negations. Use inline LaTeX-style notation without
changing the original mathematical meaning. The checker compares canonical English
text outside figure/equation regions with the ordered reflowed English/captions.
It ignores line wrapping, punctuation and mathematical font commands, but detects
missing or reordered prose. Keep a glossary for Chinese technical terminology.

Remove only repeated page furniture by recording an explicit source rectangle:

```json
"omit": [{"bbox": [0, 745, 612, 792], "reason": "running_footer"}]
```

Allowed reasons are `running_header`, `running_footer`, and `page_number`. Never
use these to remove substantive content. Original footnotes still need translation.
Source-relative phrases such as “on the next page” should remain literal English;
translate them as “原文下一页” where repagination would otherwise mislead.

Every source image/vector graphic must be accounted for by a figure/equation
region. Geometry checks, English comparison and approvals complement visual review;
they cannot certify mathematics, diagram semantics or the fidelity of a translation.

## Mathematics

Display equations use Matplotlib's LaTeX-style MathText parser and are embedded as
**vector PDF**, with centered math and its original right-aligned number. `latex`
omits outer `$` delimiters. Supported syntax includes fractions, roots, sums,
integrals, Greek symbols, accents, bold symbols, superscripts and subscripts.
Display fractions use display style for readable numerators/denominators. Inline
math uses `$...$` and is rendered at high resolution with matching point size.
No external TeX process, shell command or user-supplied macro is executed.

This is a TeX mathematical-syntax subset, not a complete LaTeX document compiler.
Unsupported commands/environments produce a hard error, never raw TeX or a silent
fallback. Split multiline aligned derivations into consecutive short equation
blocks with the same source region, preserving row order and equation numbering.
Check every symbol and condition visually. Do not claim an unsupported matrix or
custom macro was successfully rendered; resolve its mathematical representation
before delivering the PDF.

## Scans

Inspect every raster-bearing unit even if its caption is extractable. Set
`scan_checked=true` after reviewing it. Preserve raster figures as figure blocks;
translate labels or scanned notes in `figure.zh` and preserve the image below.
For a whole scanned prose page, supply faithful `ocr_text` and reflow its English
into text blocks, with `ocr_reflow=true`. Check all original figures separately;
that flag is not permission to discard diagrams. There is no fake OCR completion.

## Rendering and review

`check` and `approve` reject untranslated text, translated references, omitted
English prose, uncovered graphics, stale source extraction and broken geometry.
Final `render` requires up-to-date layout and translation digests. Edits invalidate
those digests. `render --draft` is internal only and visibly marked as draft.

Each short bilingual pair stays together. Headings stay with the following pair;
figures stay with their captions. Long pairs and overly wide equations fail with
an instruction to divide them into readable corresponding passages/rows. Reflow
uses fixed A4 pages, understated page furniture and consistent embedded fonts.
The `.audit.json` sidecar records body size/leading, text boxes, source figure/math
rectangles, output positions, math renderer and source/output hashes.

Inspect **all output pages**, including punctuation, math baselines/numbering,
figure completeness and reference language. Run the bundled `scripts/selftest.py`
after changing the implementation. Keep papers, translation jobs and proofs out of
GitHub; publish only the implementation and synthetic tests.
