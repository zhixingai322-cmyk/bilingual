#!/usr/bin/env python3
"""Offline synthetic regression tests. Never uses or publishes a user's paper."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont
import paper_bilingual as pb


def make_fixture(path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((42, 43), "A Synthetic Fractional Fourier Receiver", fontsize=17)
    p.insert_text((42, 84), "The received chirp is multiplied by the reference signal.\nThe beat frequency depends on the propagation delay.", fontsize=11)
    p.insert_text((42, 145), "The matched response is defined by the following equation:", fontsize=11)
    p.insert_text((125, 184), "R(t) = integral s(u) s*(u-t) du", fontsize=14)
    p.insert_text((535, 184), "(1)", fontsize=11)
    p.insert_text((42, 226), "The equation retains its original symbols and numbering.", fontsize=11)
    # Multipart vector figure; one outer frame intentionally protects all panels.
    p.draw_rect(fitz.Rect(55, 265, 557, 455), color=(.1, .2, .5), width=1)
    p.draw_line((300, 265), (300, 455), color=(.1, .2, .5))
    for offset in (70, 325):
        p.draw_line((offset, 430), (offset+190, 430))
        p.draw_line((offset, 430), (offset, 285))
        points = [(offset+i*4, 425-120*(i/47)**2) for i in range(48)]
        for a, b in zip(points, points[1:]):
            p.draw_line(a, b, color=(.2,.4,.7), width=1)
    p.insert_text((90, 282), "(a) Chirp", fontsize=9)
    p.insert_text((345, 282), "(b) Spectrum", fontsize=9)
    p.insert_text((42, 480), "Fig. 1. The two panels form one complete scientific figure.", fontsize=10)
    p.insert_text((42, 535), "The measured peak identifies the target delay.\nAll source content remains in the original reading order.", fontsize=11)
    p2 = doc.new_page(width=612, height=792)
    for x, column in ((40, "Left"), (330, "Right")):
        for y, word in ((70, "first"), (170, "second"), (290, "third"), (420, "fourth")):
            p2.insert_text((x,y), f"{column} column, {word} passage.\nA delay produces a measurable beat.\nThe original figure stays complete.", fontsize=10)
    im = Image.new("RGB", (1224, 1584), "white")
    draw = ImageDraw.Draw(im)
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    draw.text((84,130), "This passage exists only as a scanned image.", fill="black", font=font)
    draw.text((84,190), "Its Chinese translation must still be provided.", fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    scan = doc.new_page(width=612, height=792)
    scan.insert_image(scan.rect, stream=buf.getvalue())
    doc.save(path)
    doc.close()


class WorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.pdf = cls.root / "synthetic.pdf"
        make_fixture(cls.pdf)
        cls.job = cls.root / "job"
        pb.prepare(cls.pdf, cls.job, target=140)
        cls.data, cls.doc = pb.load_job(cls.job)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()
        cls.tmp.cleanup()

    def translated(self):
        data = copy.deepcopy(self.data)
        for u in data["units"]:
            u["scan_checked"] = True
            if u["page"] == 2:
                u["ocr_text"] = "This passage exists only as a scanned image. Its Chinese translation must still be provided."
            if u["source_text"] or u["ocr_text"]:
                u["zh"] = "这是离线结构测试使用的中文段落。传播时延决定拍频，原文中的公式、编号和图片完整保留。"
        return data

    def test_safe_partition_and_two_column_reading_order(self):
        self.assertEqual(pb.geometry_errors(self.data, self.doc), [])
        units = [u for u in self.data["units"] if u["page"] == 1]
        left = [i for i,u in enumerate(units) if "Left column" in u["source_text"]]
        right = [i for i,u in enumerate(units) if "Right column" in u["source_text"]]
        self.assertTrue(left and right)
        self.assertLess(max(left), min(right))
        self.assertTrue(any("(1)" in u["source_text"] for u in self.data["units"]))

    def test_untranslated_and_unreviewed_jobs_are_blocked(self):
        report = pb.check_job(self.data, self.doc, require_review=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("translation" in e for e in report["errors"]))
        with self.assertRaises(ValueError):
            pb.render(self.job, self.root / "blocked.pdf")

    def test_gap_and_overlap_are_both_rejected(self):
        gap = copy.deepcopy(self.data)
        gap["units"].pop(0)
        self.assertTrue(any("coverage gap" in e for e in pb.geometry_errors(gap, self.doc)))
        overlap = copy.deepcopy(self.data)
        extra = copy.deepcopy(overlap["units"][0])
        extra["id"] += "-duplicate"
        overlap["units"].append(extra)
        self.assertTrue(any("coverage overlap" in e for e in pb.geometry_errors(overlap, self.doc)))

    def test_cut_formula_or_figure_is_rejected(self):
        d = copy.deepcopy(self.data)
        d["units"] = [u for u in d["units"] if u["page"] != 0]
        for ident, box in (("cut-a", [0,0,612,350]), ("cut-b", [0,350,612,792])):
            d["units"].append(pb.new_unit(self.doc[0], fitz.Rect(box), ident))
        errors = pb.geometry_errors(d, self.doc)
        self.assertTrue(any("vector object is cut" in e for e in errors))

    def test_scans_need_visual_check_and_translation(self):
        d = self.translated()
        scan = next(u for u in d["units"] if u["page"] == 2)
        scan["ocr_text"], scan["zh"], scan["scan_checked"] = "", "", False
        self.assertTrue(any("visual scan review" in e for e in pb.text_errors(d, self.doc)))
        scan["scan_checked"], scan["ocr_text"] = True, "This is scanned English text."
        self.assertTrue(any("missing Chinese" in e for e in pb.text_errors(d, self.doc)))

    def test_stale_review_is_rejected(self):
        d = self.translated()
        d["review"] = dict.fromkeys(("layout", "translation"), pb.digest(d))
        self.assertTrue(pb.check_job(d, self.doc, True)["ok"])
        d["units"][0]["zh"] += "修改。"
        self.assertTrue(any("stale" in e for e in pb.check_job(d, self.doc, True)["errors"]))

    def test_render_preserves_source_and_zh_before_en(self):
        d = self.translated()
        d["review"] = dict.fromkeys(("layout", "translation"), pb.digest(d))
        pb.write_json(self.job / "manifest.json", d)
        output = self.root / "bilingual.pdf"
        pb.render(self.job, output)
        audit = json.loads(output.with_suffix(".audit.json").read_text())
        self.assertTrue(audit["ok"])
        self.assertEqual(len(audit["placements"]), len(d["units"]))
        with fitz.open(output) as out:
            en = "\n".join(p.get_text() for p in out)
            self.assertIn("R(t) = integral", en)
            self.assertIn("(a) Chirp", en)
            self.assertIn("(b) Spectrum", en)
            self.assertIn("(1)", en)
            self.assertIn("传播时延", en)
            for pos in audit["placements"]:
                if pos["original_bbox"] is None:
                    self.assertEqual(pos["original_mode"], "blank")
                    continue
                if pos["chinese_bbox"]:
                    self.assertLessEqual(pos["chinese_bbox"][3], pos["original_bbox"][1])
                self.assertGreaterEqual(fitz.Rect(pos["original_bbox"]).width,
                                        fitz.Rect(pos["render_bbox"]).width - .001)
                output_page = out[pos["output_page"]]
                self.assertAlmostEqual(pos["original_bbox"][0], pb.MARGIN)
                self.assertAlmostEqual(pos["original_bbox"][2], output_page.rect.width - pb.MARGIN, places=3)
                if pos["chinese_bbox"]:
                    self.assertEqual(pos["chinese_bbox"][0], pos["original_bbox"][0])
                    self.assertEqual(pos["chinese_bbox"][2], pos["original_bbox"][2])
                # Render every placed original to catch invalid PDF references.
                self.assertGreater(out[pos["output_page"]].get_pixmap(
                    clip=fitz.Rect(pos["original_bbox"])).width, 0)
            self.assertIn("Fig. 1.", en)
        pb.proof(output, self.root / "proof", dpi=72)
        # Restore base job for independent test methods.
        pb.write_json(self.job / "manifest.json", self.data)

    def test_refresh_retains_unchanged_translation(self):
        d = self.translated()
        pb.write_json(self.job / "manifest.json", d)
        pb.refresh(self.job)
        new, doc = pb.load_job(self.job)
        doc.close()
        self.assertEqual([u["zh"] for u in d["units"]], [u["zh"] for u in new["units"]])
        self.assertEqual(new["review"], {})
        pb.write_json(self.job / "manifest.json", self.data)

    def test_annotated_page_uses_visible_raster(self):
        pdf = self.root / "annotated.pdf"
        with fitz.open() as doc:
            p = doc.new_page(width=300, height=400)
            p.insert_text((20,40), "An annotated English passage.")
            p.add_freetext_annot(fitz.Rect(20,80,250,130), "Visible annotation", fontsize=12)
            doc.save(pdf)
        job = self.root / "annotated-job"
        pb.prepare(pdf, job)
        d, doc = pb.load_job(job)
        for u in d["units"]:
            u["zh"], u["scan_checked"] = "此页包含可见批注。", True
        d["review"] = dict.fromkeys(("layout", "translation"), pb.digest(d))
        pb.write_json(job / "manifest.json", d)
        doc.close()
        output = self.root / "annotated-bilingual.pdf"
        pb.render(job, output)
        audit = json.loads(output.with_suffix(".audit.json").read_text())
        self.assertTrue(all(p["original_mode"] == "raster-240dpi" for p in audit["placements"]))

    def test_blank_padding_is_removed_and_faint_vector_ink_is_kept(self):
        with fitz.open() as doc:
            page = doc.new_page(width=300, height=400)
            page.insert_text((60, 80), "A source passage.", fontsize=12)
            page.draw_rect(fitz.Rect(20, 120, 280, 170), color=(.98, .98, .98), width=.4)
            region = pb.visible_bounds(page, page.rect)
            self.assertIsNotNone(region)
            self.assertLess(region.x0, 20)
            self.assertGreater(region.x1, 280)
            self.assertGreater(region.y0, 50)
            self.assertLess(region.y1, 180)
            self.assertTrue(pb.contains(region, fitz.Rect(20,120,280,170)))

    def test_short_heading_uses_shared_column_body_width(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=400)
            page.insert_text((40, 50), "Heading", fontsize=12)
            page.insert_text((40, 160), "A longer sentence spans the body column.", fontsize=10)
            units = [pb.new_unit(page, fitz.Rect(0,0,300,100), "heading"),
                     pb.new_unit(page, fitz.Rect(0,100,300,400), "body")]
            clips, ink = pb.display_regions({"units": units}, doc)
            self.assertAlmostEqual(clips["heading"].x0, clips["body"].x0)
            self.assertAlmostEqual(clips["heading"].x1, clips["body"].x1)
            self.assertGreater(clips["heading"].width, ink["heading"].width * 2)
            self.assertLess(clips["body"].width, 300)

    def test_source_tampering_is_blocked(self):
        job = self.root / "tampered-job"
        pb.prepare(self.pdf, job)
        with (job / "source.pdf").open("ab") as f:
            f.write(b"\nmodified\n")
        with self.assertRaisesRegex(ValueError, "Source PDF changed"):
            pb.load_job(job)

    def test_extractable_caption_does_not_bypass_raster_review(self):
        with fitz.open() as doc:
            p = doc.new_page(width=300, height=400)
            im = Image.new("RGB", (400, 200), "white")
            ImageDraw.Draw(im).text((20, 50), "English labels inside image", fill="black")
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            p.insert_image(fitz.Rect(20,20,280,150), stream=buf.getvalue())
            p.insert_text((20,180), "Fig. 2. An extractable caption.")
            u = pb.new_unit(p, p.rect, "mixed-image")
            u["zh"] = "图二，可提取的图注。"
            data = {"units": [u]}
            self.assertTrue(any("raster content" in e for e in pb.text_errors(data, doc)))
            u["ocr_text"] = "English labels inside image"
            u["zh"] += "图内的英文标签。"
            u["scan_checked"] = True
            self.assertEqual(pb.text_errors(data, doc), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
