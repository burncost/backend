"""Admin report/download endpoints (Phase 5).

All endpoints are RBAC-protected (admin-tier roles only) and return
in-memory file downloads. XLSX is produced with openpyxl (available); the
optional PDF invoice uses reportlab and falls back to CSV when reportlab is
not installed - matching the existing boq_generator.py pattern.
"""
import csv
import io
from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.api.deps import require_roles, get_current_verified_vendor
from app.models.user import User, UserProfile
from app.models.vendor import Vendor
from app.models.order import Order, OrderItem
from app.models.product import Product

import logging

logger = logging.getLogger(__name__)

router = APIRouter()

# Admin-tier roles allowed on all report endpoints.
report_guard = require_roles("manager", "support", "marketing")


def _csv_response(filename: str, rows: list[list[object]]) -> StreamingResponse:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _xlsx_response(filename: str, header: list[str], rows: list[list[object]]) -> StreamingResponse:
    import openpyxl
    from openpyxl.styles import Font

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Report"
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append(["" if v is None else v for v in row])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── A4 print-friendly writers (Admin → User Management exports) ──────────────
# Both writers stay inside what requirements.txt already pins (openpyxl,
# python-docx) and follow the A4 conventions of boq_report_service.py: A4 page,
# 1.6 cm margins, compact 9 pt table text. They are separate from the helpers
# above so the existing report downloads keep their exact byte layout.
_A4_MARGIN_CM = 1.6


def _export_date(value) -> str:
    """Render a date/time the way the desk reads it (dd/mm/yyyy); blank otherwise."""
    if not value:
        return ""
    try:
        return value.strftime("%d/%m/%Y")
    except AttributeError:
        return str(value)


def _xlsx_a4_response(
    filename: str,
    header: list[str],
    rows: list[list[object]],
    sheet_title: str = "Users",
) -> StreamingResponse:
    """XLSX laid out to print on A4: portrait, one page wide, repeating header."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.page import PageMargins

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title[:31] or "Users"
    ws.append(header)

    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1A3C6B")
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for row in rows:
        ws.append(["" if v is None else v for v in row])

    # Keep dates as real date cells so Excel can still sort/filter them.
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, (datetime, date)):
                cell.number_format = "DD/MM/YYYY"

    # Widths from a sample (a 50k-row export must not get slower than the query),
    # capped so the sheet still lands on A4 portrait once scaled to one page wide.
    sample = rows[:500]
    for idx, title in enumerate(header, start=1):
        lengths = [len(str(title))]
        lengths += [
            len(str(r[idx - 1]))
            for r in sample
            if idx - 1 < len(r) and r[idx - 1] is not None
        ]
        ws.column_dimensions[get_column_letter(idx)].width = min(max(10, max(lengths) + 2), 45)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.print_title_rows = "1:1"
    ws.print_options.horizontalCentered = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5, header=0.2, footer=0.2)
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _docx_a4_response(
    filename: str,
    header: list[str],
    rows: list[list[object]],
    title: str = "Burncost export",
    subtitle: Optional[str] = None,
) -> StreamingResponse:
    """Word .docx table on A4 - landscape automatically when the sheet is wide."""
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    section = doc.sections[0]
    landscape = len(header) > 4
    if landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = Cm(29.7), Cm(21)
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = Cm(21), Cm(29.7)
    for edge in ("top_margin", "bottom_margin", "left_margin", "right_margin"):
        setattr(section, edge, Cm(_A4_MARGIN_CM))

    heading = doc.add_paragraph()
    head_run = heading.add_run(title)
    head_run.bold = True
    head_run.font.size = Pt(13)
    heading.paragraph_format.space_after = Pt(2)
    if subtitle:
        sub = doc.add_paragraph()
        sub_run = sub.add_run(subtitle)
        sub_run.font.size = Pt(8.5)
        sub_run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
        sub.paragraph_format.space_after = Pt(6)

    table = doc.add_table(rows=1, cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    head_row = table.rows[0]
    # Repeat the header on every printed page.
    tr_pr = head_row._tr.get_or_add_trPr()
    tbl_header = OxmlElement("w:tblHeader")
    tbl_header.set(qn("w:val"), "true")
    tr_pr.append(tbl_header)
    for cell, value in zip(head_row.cells, header):
        para = cell.paragraphs[0]
        para.paragraph_format.space_after = Pt(0)
        run = para.add_run(str(value))
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        shading = OxmlElement("w:shd")
        shading.set(qn("w:fill"), "1A3C6B")
        cell._tc.get_or_add_tcPr().append(shading)

    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row):
            para = cell.paragraphs[0]
            para.paragraph_format.space_after = Pt(0)
            if isinstance(value, (datetime, date)):
                text = _export_date(value)
            else:
                text = "" if value is None else str(value)
            para.add_run(text).font.size = Pt(9)

    # Proportional column widths from a content sample, scaled to the printable
    # width so the table never spills over the page edge.
    usable_cm = (29.7 if landscape else 21.0) - (_A4_MARGIN_CM * 2)
    sample = rows[:300]
    weights: list[int] = []
    for idx, value in enumerate(header):
        lengths = [len(str(value))]
        lengths += [
            len(str(r[idx]))
            for r in sample
            if idx < len(r) and r[idx] is not None
        ]
        weights.append(max(8, min(max(lengths), 60)))
    total = sum(weights) or 1
    for idx, weight in enumerate(weights):
        width = Cm(usable_cm * weight / total)
        table.columns[idx].width = width
        for cell in table.columns[idx].cells:
            cell.width = width

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/vendors/export")

async def export_vendors(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    verification_status: Optional[str] = Query(None),
    current_user: dict = Depends(report_guard),
    db: AsyncSession = Depends(get_db),
):
    """Export vendor list to CSV or XLSX."""
    query = select(Vendor).order_by(Vendor.business_name)
    if verification_status:
        query = query.where(Vendor.verification_status == verification_status)
    result = await db.execute(query)
    vendors = result.scalars().all()

    header = [
        "Business Name", "Business Type", "City", "State", "CAC Number",
        "Verification Status", "Rating", "Total Reviews", "Total Sales", "Created At",
    ]
    rows = [
        [
            v.business_name, v.business_type, v.city, v.state,
            v.cac_business_registration_number,
            v.verification_status.value if hasattr(v.verification_status, "value") else str(v.verification_status),
            float(v.rating or 0), v.total_reviews or 0, float(v.total_sales or 0),
            v.created_at.strftime("%Y-%m-%d") if v.created_at else "",
        ]
        for v in vendors
    ]

    filename = f"vendors.{format}"
    if format == "xlsx":
        return _xlsx_response(filename, header, rows)
    return _csv_response(filename, [header, *rows])


@router.get("/financial/export")
async def export_financial_report(
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    current_user: dict = Depends(report_guard),
    db: AsyncSession = Depends(get_db),
):
    """Export financial report (orders + payments summary) to XLSX or CSV."""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.user).selectinload(User.profile))
        .order_by(Order.created_at.desc())
    )
    orders = result.scalars().all()

    header = [
        "Order Number", "Buyer", "Email", "Subtotal", "Shipping", "Tax",
        "Discount", "Total", "Status", "Payment Status", "Payment Method", "Created At",
    ]
    rows = [
        [
            o.order_number,
            f"{o.user.profile.first_name} {o.user.profile.last_name}".strip() if o.user and o.user.profile else (o.user.email if o.user else ""),
            o.user.email if o.user else "",
            float(o.subtotal), float(o.shipping_fee or 0), float(o.tax_amount or 0),
            float(o.discount_amount or 0), float(o.total_amount),
            o.status.value if hasattr(o.status, "value") else str(o.status),
            o.payment_status.value if hasattr(o.payment_status, "value") else str(o.payment_status),
            o.payment_method.value if hasattr(o.payment_method, "value") else "",
            o.created_at.strftime("%Y-%m-%d") if o.created_at else "",
        ]
        for o in orders
    ]

    # Totals row - 12 columns; place the 5 numeric totals into slots 4–8.
    totals = [
        sum(float(o.subtotal) for o in orders),
        sum(float(o.shipping_fee or 0) for o in orders),
        sum(float(o.tax_amount or 0) for o in orders),
        sum(float(o.discount_amount or 0) for o in orders),
        sum(float(o.total_amount) for o in orders),
    ]
    rows.append(["TOTAL", "", ""] + [round(t, 2) for t in totals] + ["", "", "", ""])

    filename = f"financial-report.{format}"
    if format == "xlsx":
        return _xlsx_response(filename, header, rows)
    return _csv_response(filename, [header, *rows])


@router.get("/orders/{order_id}/invoice")
async def export_order_invoice(
    order_id: UUID,
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    current_user: dict = Depends(report_guard),
    db: AsyncSession = Depends(get_db),
):
    """Export a single order invoice. PDF via reportlab (falls back to CSV)."""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.user).selectinload(User.profile), selectinload(Order.items))
        .where(Order.id == order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        from fastapi import HTTPException, status as status_mod
        raise HTTPException(status_code=status_mod.HTTP_404_NOT_FOUND, detail="Order not found")

    buyer = f"{order.user.profile.first_name} {order.user.profile.last_name}".strip() if order.user and order.user.profile else (order.user.email if order.user else "-")

    header = ["Item", "SKU", "Qty", "Unit Price", "Total"]
    rows = [
        [i.product_name, i.sku, i.quantity, float(i.unit_price), float(i.total_price)]
        for i in order.items
    ]
    rows.append(["", "", "", "Subtotal", float(order.subtotal)])
    rows.append(["", "", "", "Shipping", float(order.shipping_fee or 0)])
    rows.append(["", "", "", "Tax", float(order.tax_amount or 0)])
    rows.append(["", "", "", "Discount", float(order.discount_amount or 0)])
    rows.append(["", "", "", "GRAND TOTAL", float(order.total_amount)])

    filename = f"invoice-{order.order_number}.{format}"

    if format == "csv":
        return _csv_response(filename, [["INVOICE", order.order_number], ["Buyer", buyer], [], header, *rows])

    # PDF via reportlab - graceful CSV fallback if reportlab is unavailable.
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [
            Paragraph(f"Burncost Invoice - {order.order_number}", styles["Title"]),
            Spacer(1, 12),
            Paragraph(f"Buyer: {buyer}", styles["Normal"]),
            Paragraph(f"Date: {order.created_at.strftime('%Y-%m-%d') if order.created_at else ''}", styles["Normal"]),
            Spacer(1, 16),
        ]
        table_data = [header] + [[str(c) for c in r] for r in rows]
        table = Table(table_data)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A3C6B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]))
        story.append(table)
        doc.build(story)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except ImportError:
        logger.warning("reportlab not installed, falling back to CSV for invoice")
        return _csv_response(f"invoice-{order.order_number}.csv", [["INVOICE", order.order_number], ["Buyer", buyer], [], header, *rows])


# ── Vendor's own performance report (watermarked PDF) ─────────────────────────
# Vendor-facing, deliberately NOT the admin `report_guard`: a vendor can only ever
# export their own figures. Numbers come from the same handlers the dashboard
# renders, so the document can never disagree with the UI.
_VENDOR_REPORT_LINES = 25


@router.get("/vendor/summary")
async def export_vendor_report(
    current_vendor: dict = Depends(get_current_verified_vendor),
    db: AsyncSession = Depends(get_db),
):
    """Download a watermarked PDF of this vendor's performance."""
    from datetime import datetime as _dt

    from app.api.v1.endpoints import analytics, payments, reviews as reviews_api
    from app.api.v1.endpoints import orders as orders_api
    from app.services.vendor_report_service import build_vendor_report_pdf, money

    vendor_id = UUID(current_vendor["id"])
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalars().first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor profile not found")

    sales = await analytics.get_sales_analytics("30d", None, None, current_vendor, db)
    compare = await analytics.get_sales_comparison("30d", None, None, current_vendor, db)
    on_time = await analytics.get_on_time_delivery_rate(current_vendor, db)
    pay = await payments.list_vendor_payments(1, _VENDOR_REPORT_LINES, current_vendor, db)
    order_rows = await orders_api.get_orders_ui(current_vendor, db)
    try:
        review_stats = await reviews_api.list_vendor_product_reviews(current_vendor, db)
    except Exception:
        # The report must still render even if the reviews query fails.
        review_stats = {"reviews": [], "average_rating": 0, "total_reviews": 0,
                        "rating_distribution": {}}

    products = (await db.execute(
        select(Product).where(Product.vendor_id == vendor_id).order_by(Product.name).limit(200)
    )).scalars().all()

    ps = pay.get("summary") or {}
    period_label = "Last 30 days"

    summary_meta = [
        ("Delivered revenue", money(sales.get("total_revenue"))),
        ("Delivered orders", str(sales.get("total_orders", 0))),
        ("Average order value", money(sales.get("average_order_value"))),
        ("Revenue vs previous period", str(compare.get("revenue_change", "0%"))),
        ("In-flight orders", str(sales.get("pending_orders", 0))),
        ("Cancelled / refunded", str(sales.get("cancelled_orders", 0))),
        ("Payments captured", money(ps.get("total_earned"))),
        ("Awaiting payment", money(ps.get("pending_balance"))),
        ("Awaiting payout", money(ps.get("awaiting_payout"))),
        ("Next payout due", str(ps.get("next_payout_date") or "Nothing due")),
        ("Payout window", f"{ps.get('payout_window_hours', 48)}h after delivery confirmation"),
        ("On-time delivery", f"{on_time.get('on_time_rate', 0)}%"),
        ("Average rating", str(review_stats.get("average_rating", 0))),
        ("Verification", str(getattr(vendor.verification_status, "value", vendor.verification_status))),
    ]

    sales_rows = [
        ["Delivered revenue", money(sales.get("total_revenue"))],
        ["Delivered orders", str(sales.get("total_orders", 0))],
        ["Average order value (delivered only)", money(sales.get("average_order_value"))],
        ["In-flight orders (unpaid/processing)", str(sales.get("pending_orders", 0))],
        ["Cancelled / refunded orders", str(sales.get("cancelled_orders", 0))],
        ["On-time delivery rate",
         f"{on_time.get('on_time_rate', 0)}% ({on_time.get('on_time_delivered', 0)}/{on_time.get('total_delivered', 0)})"],
        ["Revenue change vs previous 30 days", str(compare.get("revenue_change", "0%"))],
        ["Order count change vs previous 30 days", str(compare.get("orders_change", "0%"))],
    ]

    payment_rows = [
        [
            row.get("reference", ""),
            row.get("payment_status", ""),
            money(row.get("amount")),
            money(row.get("order_total")),
            (row.get("payout_due_at") or "")[:10] or "-",
        ]
        for row in (pay.get("payments") or [])
    ]



    order_table = [
        [
            r.get("id", ""),
            r.get("date", ""),
            r.get("status", ""),
            r.get("payment_status", ""),
            str(r.get("item_count", 0)),
            money(r.get("vendor_total")),
            money(r.get("total")),
            r.get("driverName") or "-",
        ]
        for r in (order_rows or [])
    ]

    product_table = [
        [
            p.name,
            money(p.discount_price or p.base_price),
            str(p.quantity if p.quantity is not None else 0),
            str(p.sales_count or 0),
        ]
        for p in products
    ]

    review_table = [
        [
            (r.get("created_at") or "")[:10],
            r.get("product_name", ""),
            str(r.get("rating", "")),
            r.get("reviewer_name", "") or "Anonymous",
        ]
        for r in (review_stats.get("reviews") or [])[:_VENDOR_REPORT_LINES]
    ]

    sections = [
        {"heading": "Sales performance", "columns": ["Metric", "Value"], "rows": sales_rows},
        {
            "heading": "Payments and payouts",
            "columns": ["Order", "Payment status", "Your share", "Order total", "Payout due"],
            "rows": payment_rows,
            "empty": "No payments recorded yet.",
            "note": "Your share is the value of your own items on each order; an order can span several suppliers.",
        },
        {
            "heading": "Orders",
            "columns": ["Order", "Date", "Status", "Payment", "Items", "Your share", "Order total", "Driver"],
            "rows": order_table,
            "empty": "No orders yet.",
        },
        {
            "heading": "Product catalogue",
            "columns": ["Product", "Price", "Stock", "Lifetime units"],
            "rows": product_table,
            "empty": "No products listed yet.",
        },
        {
            "heading": "Customer reviews",
            "columns": ["Date", "Product", "Rating", "Reviewer"],
            "rows": review_table,
            "empty": "No reviews yet.",
            "note": (
                f"Average rating {review_stats.get('average_rating', 0)} from "
                f"{review_stats.get('total_reviews', 0)} review(s). Distribution: "
                + ", ".join(f"{k}*={v}" for k, v in (review_stats.get("rating_distribution") or {}).items())
            ),
        },
    ]

    pdf = build_vendor_report_pdf(
        vendor_name=vendor.business_name or "Vendor",
        period_label=f"{period_label} | report date {_dt.utcnow().strftime('%Y-%m-%d')}",
        summary_meta=summary_meta,
        sections=sections,
    )

    slug = "-".join(filter(None, "".join(
        ch if ch.isalnum() else "-" for ch in (vendor.business_name or "vendor").lower()
    ).split("-")))[:40] or "vendor"
    filename = f"burncost-{slug}-report-{_dt.utcnow().strftime('%Y-%m-%d')}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Admin → User Management: filtered user export (Excel / Word / CSV) ───────
# Column keys are a whitelist: the UI sends `columns=name,email,created_at`
# (its default) and anything unknown is rejected instead of silently dropped.
USER_EXPORT_COLUMNS: dict[str, str] = {
    "name": "Name",
    "email": "Email",
    "created_at": "Signup Date",
    "first_name": "First Name",
    "last_name": "Last Name",
    "phone": "Phone",
    "business_name": "Business Name",
    "role": "Role",
    "status": "Status",
    "email_verified": "Email Verified",
    "location": "Location",
    "last_login": "Last Login",
    "two_factor_method": "2FA Method",
}

DEFAULT_USER_EXPORT_COLUMNS: tuple[str, ...] = ("name", "email", "created_at")

_USER_EXPORT_MAX_ROWS = 50000

_ADMIN_TIER_ROLES = ("admin", "super_admin", "manager", "support", "marketing")


def _export_scope_roles(scope: Optional[str]) -> Optional[list[str]]:
    """Map the console's friendly scope to the role values stored in `users`.

    `builders` -> customer, `suppliers` -> vendor, `admins` -> every console
    tier. Anything else is passed through as a raw role, so `?role=admin`
    behaves exactly like GET /admin/users does (single role, not the tiers).
    """
    if not scope:
        return None
    tokens = [t.strip().lower() for t in scope.split(",") if t.strip()]
    if not tokens:
        return None

    roles: list[str] = []
    for token in tokens:
        if token in ("builder", "builders", "customer", "customers"):
            roles.append("customer")
        elif token in ("supplier", "suppliers", "vendor", "vendors"):
            roles.append("vendor")
        elif token in ("admins", "staff", "admin-tier", "admin_tier"):
            roles.extend(_ADMIN_TIER_ROLES)
        else:
            roles.append(token)
    # De-duplicate while keeping the caller's order.
    return list(dict.fromkeys(roles))


def _user_export_value(key: str, user: User) -> object:
    """One cell of the user directory, in the caller's column order."""
    profile = user.profile
    if key == "name":
        parts = [
            profile.first_name if profile else None,
            profile.last_name if profile else None,
        ]
        return " ".join(p for p in parts if p) or (profile.business_name if profile else None) or user.email
    if key == "email":
        return user.email
    if key == "created_at":
        return user.created_at
    if key == "first_name":
        return (profile.first_name if profile else "") or ""
    if key == "last_name":
        return (profile.last_name if profile else "") or ""
    if key == "phone":
        return user.phone_number or ""
    if key == "business_name":
        return (profile.business_name if profile else "") or ""
    if key == "role":
        return getattr(user.role, "value", user.role)
    if key == "status":
        return getattr(user.status, "value", user.status)
    if key == "email_verified":
        return "Yes" if user.email_verified else "No"
    if key == "location":
        return (profile.location if profile else "") or ""
    if key == "last_login":
        return user.last_login
    if key == "two_factor_method":
        return getattr(user, "two_factor_method", None) or ""
    return ""


@router.get("/users/export")
async def export_users(
    format: str = Query("xlsx", pattern="^(xlsx|csv|docx)$"),
    role: Optional[str] = Query(
        None,
        description="Scope alias (builders | suppliers | admins) or a raw role / comma list.",
    ),
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = Query(None),
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    columns: str = Query(",".join(DEFAULT_USER_EXPORT_COLUMNS)),
    limit: int = Query(_USER_EXPORT_MAX_ROWS, ge=1, le=_USER_EXPORT_MAX_ROWS),
    current_user: dict = Depends(report_guard),
    db: AsyncSession = Depends(get_db),
):
    """Export the user directory (builders / suppliers / admins) to XLSX, DOCX or CSV.

    Defaults match the console's export dialog: every user, three columns
    (Name, Email, Signup Date), Excel. Rows are capped so a runaway export can
    never exhaust the worker's memory, and both file writers are laid out for A4.
    """
    requested = [c.strip() for c in columns.split(",") if c.strip()]
    unknown = [c for c in requested if c not in USER_EXPORT_COLUMNS]
    if unknown or not requested:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown export column(s): {', '.join(unknown) or '(none selected)'}. "
                f"Valid columns: {', '.join(USER_EXPORT_COLUMNS)}."
            ),
        )
    selected = list(dict.fromkeys(requested))

    query = select(User).options(selectinload(User.profile))
    roles = _export_scope_roles(role)
    if roles:
        query = query.where(User.role.in_(roles) if len(roles) > 1 else User.role == roles[0])
    if status_filter:
        query = query.where(User.status == status_filter)
    if search and search.strip():
        like = f"%{search.strip()}%"
        # Outer join: a user without a profile row must still be exported.
        query = query.join(UserProfile, UserProfile.user_id == User.id, isouter=True).where(
            (User.email.ilike(like))
            | (UserProfile.first_name.ilike(like))
            | (UserProfile.last_name.ilike(like))
            | (UserProfile.business_name.ilike(like))
        )
    if from_date:
        query = query.where(User.created_at >= datetime.combine(from_date, time.min))
    if to_date:
        query = query.where(User.created_at <= datetime.combine(to_date, time.max))

    result = await db.execute(query.order_by(User.created_at.desc()).limit(limit))
    users = result.scalars().all()
    if not users:
        raise HTTPException(status_code=404, detail="No users match these filters.")

    header = [USER_EXPORT_COLUMNS[key] for key in selected]
    rows = [[_user_export_value(key, u) for key in selected] for u in users]

    scope_slug = "all"
    if role:
        slugged = "".join(ch if ch.isalnum() else "-" for ch in role.strip().lower()).strip("-")
        scope_slug = "-".join(filter(None, slugged.split("-")))[:24] or "all"
    filename = f"burncost-users-{scope_slug}-{datetime.utcnow().strftime('%Y-%m-%d')}.{format}"

    if format == "xlsx":
        return _xlsx_a4_response(filename, header, rows)

    plain_rows = [
        [_export_date(v) if isinstance(v, (datetime, date)) else v for v in row]
        for row in rows
    ]
    if format == "docx":
        subtitle = (
            f"{len(rows)} user(s) - exported {datetime.utcnow().strftime('%d/%m/%Y')}"
            + (f" - scope: {role}" if role else " - all users")
        )
        return _docx_a4_response(
            filename,
            header,
            plain_rows,
            title="Burncost user directory",
            subtitle=subtitle,
        )
    return _csv_response(filename, [header, *plain_rows])

