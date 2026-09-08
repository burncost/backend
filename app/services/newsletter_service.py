"""Newsletter service (Phase 3) — composes a market "price range of the day"
email and respects the opt-in record in `user_email_preferences`.

Real sends go through NotificationService/Brevo (the same transport already used
for transactional email). This service only decides WHO is emailed (consent) and
WHAT goes in the message (real material_rates min/max), plus builds the HTML.
"""
from typing import List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_preference import EmailPreference
from app.models.material_rate import MaterialRate

PRICE_OF_DAY_SUBJECT = "Burncost Market Update — today's construction prices"


class NewsletterService:
    async def opted_in_recipients(self, db: AsyncSession) -> List[str]:
        """Unique email addresses that have explicitly opted in to marketing."""
        rows = (await db.execute(
            select(EmailPreference.email).where(
                EmailPreference.marketing_opt_in.is_(True),
                EmailPreference.email.isnot(None),
            )
        )).scalars().all()
        seen: set = set()
        out: List[str] = []
        for raw in rows:
            email = (raw or "").strip().lower()
            if email and email not in seen:
                seen.add(email)
                out.append(email)
        return out

    async def build_price_of_day(self, db: AsyncSession, limit: int = 6) -> List[Dict[str, Any]]:
        """Latest materials with their market min–max price range from material_rates."""
        rows = (await db.execute(
            select(
                MaterialRate.material_name,
                MaterialRate.specification,
                MaterialRate.unit,
                func.min(MaterialRate.current_price).label("low"),
                func.max(MaterialRate.current_price).label("high"),
            )
            .where(MaterialRate.current_price.isnot(None))
            .group_by(MaterialRate.material_name, MaterialRate.specification, MaterialRate.unit)
            .order_by(func.max(MaterialRate.created_at).desc())
            .limit(limit)
        )).all()
        items = []
        for name, spec, unit, low, high in rows:
            items.append({
                "name": name,
                "spec": spec,
                "unit": unit,
                "low": float(low) if low is not None else None,
                "high": float(high) if high is not None else None,
            })
        return items

    def build_html(self, items: List[Dict[str, Any]], base_url: str = "") -> str:
        """Render a simple transactional-style HTML newsletter body with a CTA."""
        def _range(it: Dict[str, Any]) -> str:
            lo, hi = it.get("low"), it.get("high")
            if lo is None and hi is None:
                return "—"
            if hi is None or lo is None or abs((hi or 0) - (lo or 0)) < 0.005:
                return f"₦{lo if lo is not None else hi:,.2f}"
            return f"₦{lo:,.2f} – ₦{hi:,.2f}"

        rows_html = ""
        for it in items:
            title = it.get("name") or "Material"
            extra = " · ".join([x for x in [str(it.get("spec") or "").strip(), str(it.get("unit") or "").strip()] if x])
            price = _range(it)
            extra_html = (f'<br/><span style="color:#6b7280;font-size:12px">{extra}</span>' if extra else "")
            rows_html += (
                "<tr>"
                f'<td style="padding:10px 12px;border-bottom:1px solid #ececec">{title}{extra_html}</td>'
                f'<td style="padding:10px 12px;border-bottom:1px solid #ececec;text-align:right;white-space:nowrap;color:#059669;font-weight:600">{price}</td>'
                "</tr>"
            )

        marketplace = (base_url or "").rstrip("/") + "/marketplace"
        return f"""
<div style="max-width:600px;margin:auto;background:#ffffff;border:1px solid #ececec;border-radius:8px;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1e1e1e">
  <p style="color:#FF6B00;font-weight:bold;margin:0 0 4px">BURNCOST</p>
  <h2 style="margin:0 0 4px">Today's market prices</h2>
  <p style="color:#6b7280;margin:0 0 18px;font-size:13px">Real, verified Nigerian construction material rates from our marketplace.</p>
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <thead>
      <tr>
        <th style="text-align:left;padding:8px 12px;border-bottom:2px solid #ececec">Material</th>
        <th style="text-align:right;padding:8px 12px;border-bottom:2px solid #ececec">Range today</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p style="margin:20px 0 0">
    <a href="{marketplace}" style="background:#FF6B00;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:6px;display:inline-block">Browse the marketplace →</a>
  </p>
  <p style="color:#9ca3af;font-size:11px;margin:18px 0 0">
    You are receiving this because you opted in to Burncost market updates. Prices are indicative and vary by supplier and location.
  </p>
</div>
"""
