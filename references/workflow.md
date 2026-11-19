# Layout and translation jobs

## Files and fields

`source.pdf` is an immutable copy. `manifest.json` records its SHA-256, dimensions,
ordered units, Chinese translations, glossary, and review digests. `layout.pdf`
and `layout-*.png` show numbered source regions. Rendering writes the bilingual
PDF and a `.audit.json` trace of the source and output rectangles.

A unit has `id`, **zero-based** `page`, `bbox=[x0,y0,x1,y1]` in source PDF points
from the **top left**, extracted `source_text`, editable `zh`, optional `ocr_text`,
and `scan_checked`. List order is reading order. Rectangles cover each page once,
including margins. Split a rectangle at a safe boundary into adjacent rectangles;
merge only adjacent regions with a rectangular union. Assign unique IDs. After
changing geometry/order run `refresh`, which clears translations of changed
regions, invalidates approvals, re-extracts text, and rebuilds overlays.

## Automatic layout

The proposal infers columns from text blocks, reserves full-width bands for
cross-column content, expands boundaries to avoid cutting detected objects, then
orders each remaining band's left column before its right column. Within regions
it chooses whitespace near the target height, preferring paragraph boundaries.
If no safe split exists, it keeps a larger region. `--layout single` uses full-width
strips; `--layout double` forces the center-gutter proposal. Always inspect it.

Guards use text lines, image rectangles, vector drawing clusters, and annotation/
widget bounds. Disconnected paths may belong to the same figure without extraction
revealing that relationship. Manually group panels, labels, legends, and captions
into one region when needed. Conservative complex pages may remain one complete
region. Never weaken coverage or cut-object checks to force an unsafe layout.

## Scans and extraction problems

There is no fake OCR fallback. Read rendered pages or use an available OCR tool,
then fill `ocr_text` and `zh`. Mark `scan_checked=true` after reviewing each
image-only unit. A checked text-free visual unit may have empty `zh`; a scanned
text unit needs an OCR transcription and complete Chinese. The scan remains intact.
English labels inside raster images also need translation within the Chinese
passage. Automated geometry checks cannot establish that all labels are translated.
Every unit overlapping a raster image requires `scan_checked=true`, even when a
caption or other text is extractable. This explicitly covers mixed raster/vector
pages, not just completely scanned pages.

## Scientific translation

Keep a glossary in `manifest.glossary`: e.g. beat signal = 拍频信号, dechirp = 去斜,
fractional Fourier transform = 分数傅里叶变换. Distinguish t/τ, k/K, and scalars/
vectors. Preserve assumptions, negations, units, references, captions, and numbers.
Do not add teaching commentary to a faithful translation. Pair a formula with its
introducing/defining sentence and a figure with its caption when safe. Use coherent
short passages when the geometry permits. Large complex visual units may remain
longer to keep all original elements intact.

## Checks and rendering

`check` verifies source integrity, exact coverage, object containment, IDs,
unchanged extraction, Chinese completeness heuristics, and scan review. Nonzero
exit means errors remain. `approve` records an assistant/human review attestation,
not a semantic guarantee. Editing content or geometry invalidates its digest.

Originals are selectable vector crops with embedded original fonts. A page with
annotations or widgets is rasterized at 240 dpi including visible appearances;
its original crop is then an image. Chinese uses embedded fallback fonts and
literal escaped text. Markdown/LaTeX is not interpreted: use Unicode/plain notation
for Chinese and retain complex display math in the original crop.

Before embedding, render a measurement preview and bound every nonwhite pixel,
including faint strokes and visible annotations. Trim only white display padding,
with a protective inset around ink. Use shared horizontal bounds for each source
page/column, retaining original indentation and alignment of headings/equations.
Fill the same output body width used by Chinese, with uniform aspect-preserving
scaling. A short heading does not independently expand to an oversized font.

The reviewed full-page partition and immutable source remain unchanged. Audit
`source_bbox` records its complete region, `render_bbox` the white-margin-trimmed
display clip, and `ink_bbox` its measured visible bounds. Blank-only originals
may have null display/output rectangles; their source region remains accounted
for. Pages with nonwhite backgrounds are cropped conservatively to preserve all
visible pixels; their internal graphic margins can remain visible.

Output width fits the widest display region at original physical size. Original
crops and Chinese share the same 36 pt left/right body margins. Narrow source
columns are enlarged to that width. Chinese/source pairs stay together; tall pairs
get taller pages. Split units safely if a pair would exceed the PDF size limit.
Keep jobs/proofs/private papers out of version control. Publish only implementation,
documentation, and synthetic tests to GitHub.
