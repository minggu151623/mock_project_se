"""Render issued BE1 quotes as printable PDFs with Vietnamese text support."""

from html import escape
from functools import lru_cache
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@lru_cache(maxsize=1)
def _register_font() -> tuple[str, str]:
    """Register the bundled Unicode font so PDFs render Vietnamese on every OS."""
    font_dir = Path(__file__).resolve().parent / "fonts"
    regular_path = font_dir / "DejaVuSans.ttf"
    bold_path = font_dir / "DejaVuSans-Bold.ttf"
    if not regular_path.is_file() or not bold_path.is_file():
        raise RuntimeError(
            "Thiếu font DejaVu Sans đi kèm; không thể tạo PDF báo giá tiếng Việt."
        )
    pdfmetrics.registerFont(TTFont("QuoteSans", str(regular_path)))
    pdfmetrics.registerFont(TTFont("QuoteSans-Bold", str(bold_path)))
    return "QuoteSans", "QuoteSans-Bold"


def _paragraph(text: object, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), style)


def render_quote_pdf(quote: dict) -> bytes:
    """Build a compact, multi-page quote PDF in memory."""
    from io import BytesIO

    regular_font, bold_font = _register_font()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "QuoteTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=24,
        textColor=colors.HexColor("#17365D"),
        alignment=1,
        spaceAfter=8 * mm,
    )
    body_style = ParagraphStyle(
        "QuoteBody",
        parent=styles["BodyText"],
        fontName=regular_font,
        fontSize=9,
        leading=13,
    )
    heading_style = ParagraphStyle(
        "QuoteHeading",
        parent=body_style,
        fontName=bold_font,
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#17365D"),
        spaceBefore=4 * mm,
        spaceAfter=2 * mm,
    )
    total_style = ParagraphStyle(
        "QuoteTotal",
        parent=body_style,
        fontName=bold_font,
        fontSize=12,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#17365D"),
    )

    story = [_paragraph("BÁO GIÁ DỊCH VỤ", title_style)]
    metadata = [
        ["Mã báo giá", quote["code"], "Phiên bản", quote["version_no"]],
        ["Mã yêu cầu", quote["request_code"], "Hiệu lực đến", quote["valid_until"]],
        ["Trạng thái", quote["status"], "", ""],
    ]
    metadata_table = Table(
        metadata,
        colWidths=[28 * mm, 62 * mm, 28 * mm, 48 * mm],
        hAlign="LEFT",
    )
    metadata_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), regular_font),
                ("FONTNAME", (0, 0), (0, -1), bold_font),
                ("FONTNAME", (2, 0), (2, -1), bold_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#52657A")),
                ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#52657A")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#D7DFE8")),
            ]
        )
    )
    story.extend([metadata_table, Spacer(1, 7 * mm)])
    story.append(_paragraph("Phạm vi và chi phí", heading_style))

    rows = [["#", "Nội dung", "Số lượng", "Đơn giá (VND)", "Thành tiền (VND)"]]
    for index, line in enumerate(quote["lines"], start=1):
        rows.append(
            [
                str(index),
                _paragraph(line["description"], body_style),
                f"{line['quantity']:,}",
                f"{line['unit_price_vnd']:,}",
                f"{line['line_total_vnd']:,}",
            ]
        )
    lines_table = Table(
        rows,
        colWidths=[10 * mm, 72 * mm, 20 * mm, 34 * mm, 36 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    lines_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), regular_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCD5DF")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F5F8FB")],
                ),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([lines_table, Spacer(1, 4 * mm)])
    story.append(_paragraph(f"Tổng cộng: {quote['total_vnd']:,} VND", total_style))
    if quote.get("terms"):
        story.append(_paragraph("Điều khoản", heading_style))
        story.append(_paragraph(quote["terms"], body_style))

    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=f"Báo giá {quote['code']}",
        author="Mock Project",
    )
    document.build(story)
    return output.getvalue()
