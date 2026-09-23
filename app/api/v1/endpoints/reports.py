"""Admin report/download endpoints (Phase 5).

All endpoints are RBAC-protected (admin-tier roles only) and return
in-memory file downloads. XLSX is produced with openpyxl (available); the
optional PDF invoice uses reportlab and falls back to CSV when reportlab is
not installed — matching the existing boq_generator.py pattern.
"""
import csv
import io
from datetime import datetime
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

    # Totals row — 12 columns; place the 5 numeric totals into slots 4–8.
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

    buyer = f"{order.user.profile.first_name} {order.user.profile.last_name}".strip() if order.user and order.user.profile else (order.user.email if order.user else "—")

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

    # PDF via reportlab — graceful CSV fallback if reportlab is unavailable.
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4)
        styles = getSampleStyleSheet()
        story = [
            Paragraph(f"Burncost Invoice — {order.order_number}", styles["Title"]),
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
