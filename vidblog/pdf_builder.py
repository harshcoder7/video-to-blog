"""Render a BlogPost into a nicely laid-out PDF using reportlab."""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from PIL import Image as PILImage

from vidblog.writer import BlogPost

ACCENT = colors.HexColor("#1f6f5c")
MUTED = colors.HexColor("#6b6b6b")
DARK = colors.HexColor("#1a1a1a")

PAGE_W, PAGE_H = LETTER
MARGIN = 0.85 * inch
CONTENT_W = PAGE_W - 2 * MARGIN


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    styles = {
        "Title": ParagraphStyle(
            "BlogTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=25, leading=30, textColor=DARK, spaceAfter=6,
        ),
        "Subtitle": ParagraphStyle(
            "BlogSubtitle", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=13, leading=18, textColor=MUTED, spaceAfter=10,
        ),
        "Byline": ParagraphStyle(
            "Byline", parent=base["Normal"], fontName="Helvetica",
            fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=4,
        ),
        "H2": ParagraphStyle(
            "SectionHeading", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=16, leading=20, textColor=ACCENT, spaceBefore=18, spaceAfter=4,
        ),
        "Timestamp": ParagraphStyle(
            "Timestamp", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=8,
        ),
        "Body": ParagraphStyle(
            "Body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=11.3, leading=16.5, textColor=DARK, spaceAfter=10,
            alignment=4,  # justify
        ),
        "Caption": ParagraphStyle(
            "Caption", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9, leading=12, textColor=MUTED, alignment=1, spaceAfter=14,
        ),
    }
    return styles


def _fit_image(path: str, max_w: float, max_h: float | None = None) -> Image:
    with PILImage.open(path) as im:
        w, h = im.size
    scale = max_w / w
    draw_w, draw_h = max_w, h * scale
    if max_h and draw_h > max_h:
        scale = max_h / h
        draw_w, draw_h = w * scale, max_h
    img = Image(path, width=draw_w, height=draw_h)
    img.hAlign = "CENTER"
    return img


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _footer(canvas, doc, source_url: str):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(MARGIN, 0.55 * inch, f"Source: {source_url}")
    canvas.drawRightString(PAGE_W - MARGIN, 0.55 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#dddddd"))
    canvas.line(MARGIN, 0.72 * inch, PAGE_W - MARGIN, 0.72 * inch)
    canvas.restoreState()


def build_pdf(
    post: BlogPost,
    out_path: str,
    thumbnail_path: str | None = None,
    byline_extra: str = "",
) -> str:
    styles = _styles()
    story = []

    story.append(Paragraph(_escape(post.title), styles["Title"]))
    if post.subtitle:
        story.append(Paragraph(_escape(post.subtitle), styles["Subtitle"]))

    byline_bits = []
    if byline_extra:
        byline_bits.append(_escape(byline_extra))
    if post.source_url:
        byline_bits.append(f'Watch the original: <link href="{post.source_url}" color="#1f6f5c">{_escape(post.source_url)}</link>')
    if byline_bits:
        story.append(Paragraph(" &nbsp;|&nbsp; ".join(byline_bits), styles["Byline"]))

    story.append(Spacer(1, 6))
    story.append(HRFlowable(width=CONTENT_W, thickness=1.2, color=ACCENT))
    story.append(Spacer(1, 10))

    if thumbnail_path:
        try:
            story.append(_fit_image(thumbnail_path, CONTENT_W, max_h=3.2 * inch))
            story.append(Spacer(1, 14))
        except Exception:
            pass

    for para in post.intro:
        story.append(Paragraph(_escape(para), styles["Body"]))

    for sec in post.sections:
        story.append(Paragraph(_escape(sec.heading), styles["H2"]))
        if sec.timestamp_label:
            story.append(Paragraph(f"Video timestamp: {sec.timestamp_label}", styles["Timestamp"]))
        if sec.screenshot_path:
            try:
                story.append(_fit_image(sec.screenshot_path, CONTENT_W, max_h=3.6 * inch))
                caption = sec.screenshot_caption or f"Captured at {sec.timestamp_label}"
                story.append(Paragraph(_escape(caption), styles["Caption"]))
            except Exception:
                pass
        for para in sec.paragraphs:
            if para.strip():
                story.append(Paragraph(_escape(para), styles["Body"]))

    if post.conclusion:
        story.append(Paragraph("Wrapping Up", styles["H2"]))
        for para in post.conclusion:
            story.append(Paragraph(_escape(para), styles["Body"]))

    doc = SimpleDocTemplate(
        out_path,
        pagesize=LETTER,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=post.title,
    )

    def on_page(canvas, doc_):
        _footer(canvas, doc_, post.source_url)

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return out_path
