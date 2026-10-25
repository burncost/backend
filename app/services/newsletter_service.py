"""Newsletter service (Phase 3) - composes a market "price range of the day"
email and respects the opt-in record in `user_email_preferences`.

Real sends go through NotificationService/Brevo (the same transport already used
for transactional email). This service only decides WHO is emailed (consent) and
WHAT goes in the message (real material_rates min/max), plus builds the HTML.
"""
import asyncio
import html as html_lib
import logging
import uuid as _uuid
from datetime import datetime
from typing import List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_preference import EmailPreference
from app.models.material_rate import MaterialRate
from app.models.user import User, UserRole, UserStatus

logger = logging.getLogger(__name__)

PRICE_OF_DAY_SUBJECT = "Burncost Market Update - today's construction prices"


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
                return "-"
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


    # ── Blog newsletter (admin publish -> vendors/suppliers) ────────────────

    async def _token_for(self, db: AsyncSession, user_id, email: str) -> str:
        """Return (creating if needed) the one-click unsubscribe token."""
        pref = (await db.execute(
            select(EmailPreference).where(EmailPreference.user_id == user_id)
        )).scalar_one_or_none()
        if pref is None:
            pref = EmailPreference(
                user_id=user_id,
                email=(email or "").strip().lower(),
                marketing_opt_in=False,
                unsubscribe_token=_uuid.uuid4().hex,
            )
            db.add(pref)
            await db.flush()
            return pref.unsubscribe_token

        changed = False
        if not pref.unsubscribe_token:
            pref.unsubscribe_token = _uuid.uuid4().hex
            changed = True
        if email and (pref.email or "").lower() != email.lower():
            pref.email = email.strip().lower()
            changed = True
        if changed:
            await db.flush()
        return pref.unsubscribe_token

    async def vendor_supplier_recipients(self, db: AsyncSession) -> List[Dict[str, str]]:
        """Every active/pending vendor account, with an unsubscribe token.

        Editorial/market newsletters are sent to the vendor base as a supplier
        communication; each message carries a one-click unsubscribe. Marketing
        opt-in (`marketing_opt_in`) is left untouched - it is not claimed as
        consent for this send.
        """
        rows = (await db.execute(
            select(User.id, User.email).where(
                User.role == UserRole.VENDOR,
                User.status.in_([UserStatus.ACTIVE, UserStatus.PENDING_VERIFICATION]),
                User.email.isnot(None),
            )
        )).all()

        # A vendor who clicked unsubscribe must not be emailed again, even though
        # they are still in the vendor base. `opted_out_at` is the suppression
        # marker; a plain False `marketing_opt_in` is just the default.
        user_ids = [user_id for user_id, _ in rows]
        prefs = {}
        if user_ids:
            prefs = {
                pref.user_id: pref
                for pref in (await db.execute(
                    select(EmailPreference).where(EmailPreference.user_id.in_(user_ids))
                )).scalars().all()
            }

        out: List[Dict[str, str]] = []
        seen: set = set()
        for user_id, raw_email in rows:
            email = (raw_email or "").strip().lower()
            if not email or email in seen:
                continue
            pref = prefs.get(user_id)
            if pref is not None and pref.opted_out_at is not None and not pref.marketing_opt_in:
                continue
            seen.add(email)
            token = (
                pref.unsubscribe_token
                if pref is not None and pref.unsubscribe_token
                else await self._token_for(db, user_id, email)
            )
            out.append({"email": email, "token": token})
        return out

    async def blog_recipients(self, db: AsyncSession) -> List[Dict[str, str]]:
        """Vendor base plus any explicitly opted-in address (de-duplicated)."""
        recipients = await self.vendor_supplier_recipients(db)
        seen = {r["email"] for r in recipients}

        opted_in = (await db.execute(
            select(EmailPreference.user_id, EmailPreference.email, EmailPreference.unsubscribe_token)
            .where(
                EmailPreference.marketing_opt_in.is_(True),
                EmailPreference.email.isnot(None),
            )
        )).all()
        for user_id, raw_email, token in opted_in:
            email = (raw_email or "").strip().lower()
            if not email or email in seen:
                continue
            seen.add(email)
            recipients.append({
                "email": email,
                "token": token or await self._token_for(db, user_id, email),
            })
        return recipients

    def build_blog_html(
        self,
        post: Dict[str, Any],
        base_url: str = "",
        unsubscribe_url: str = "",
    ) -> str:
        """Render a published blog post as a transactional-style newsletter.

        `content_html` is already sanitised server-side on write, so it is safe
        to embed. A missing excerpt falls back to the post body.
        """
        site = (base_url or "").rstrip("/")
        post_url = html_lib.escape(f"{site}/blog/{post.get('slug', '')}", quote=True)
        title = html_lib.escape(str(post.get("title") or ""))
        cover = html_lib.escape(str(post.get("cover_image_url") or ""), quote=True)
        cover_html = (
            f'<img src="{cover}" alt="{title}" '
            'style="width:100%;border-radius:8px;margin:0 0 18px" />'
            if cover else ""
        )
        excerpt_html = (
            f'<p style="color:#4b5563;font-size:15px;margin:0 0 16px">'
            f'{html_lib.escape(str(post.get("excerpt") or ""))}</p>'
            if post.get("excerpt") else ""
        )
        unsubscribe_html = (
            f'<a href="{unsubscribe_url}" style="color:#9ca3af;text-decoration:underline">Unsubscribe</a>'
            if unsubscribe_url else ""
        )
        return f"""
<div style="max-width:640px;margin:auto;background:#ffffff;border:1px solid #ececec;border-radius:8px;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1e1e1e">
  <p style="color:#FF6B00;font-weight:bold;margin:0 0 4px">BURNCOST</p>
  <h1 style="margin:0 0 14px;font-size:24px;line-height:1.3">{post.get("title", "")}</h1>
  {cover_html}
  {excerpt_html}
  <div style="font-size:15px;line-height:1.65">{post.get("content_html", "")}</div>
  <p style="margin:24px 0 0">
    <a href="{post_url}" style="background:#FF6B00;color:#ffffff;text-decoration:none;padding:12px 18px;border-radius:6px;display:inline-block">Read on Burncost &rarr;</a>
  </p>
  <p style="color:#9ca3af;font-size:11px;margin:18px 0 0">
    You are receiving this as a Burncost supplier update.
    {unsubscribe_html}
  </p>
</div>
"""

    async def send_blog_newsletter(
        self,
        db: AsyncSession,
        post: Any,
        base_url: str,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Email a published post to the vendor base (idempotent).

        Returns a summary rather than raising: a mail outage must not undo a
        publish. `newsletter_sent_at` is only stamped after at least one send.
        """
        from app.services.notification_service import NotificationService

        if post.newsletter_sent_at is not None and not force:
            return {
                "sent": 0,
                "skipped": True,
                "reason": "newsletter already sent",
                "recipient_count": int(post.newsletter_recipient_count or 0),
            }

        recipients = await self.blog_recipients(db)
        if not recipients:
            return {"sent": 0, "skipped": True, "reason": "no recipients", "recipient_count": 0}

        notifier = NotificationService()
        subject = f"{post.title} | Burncost Blog"

        gate = asyncio.Semaphore(5)
        sent = 0

        async def _send(recipient: Dict[str, str]) -> bool:
            unsubscribe = f"{(base_url or '').rstrip('/')}/unsubscribe?token={recipient['token']}"
            html_body = self.build_blog_html(
                {
                    "title": post.title,
                    "slug": post.slug,
                    "excerpt": post.excerpt,
                    "content_html": post.content_html,
                    "cover_image_url": post.cover_image_url,
                },
                base_url=base_url,
                unsubscribe_url=unsubscribe,
            )
            async with gate:
                try:
                    return bool(await notifier.send_email(
                        to=recipient["email"], subject=subject, html_content=html_body
                    ))
                except Exception as exc:  # per-recipient failure must not stop the batch
                    logger.warning("Blog newsletter to %s failed: %s", recipient["email"], exc)
                    return False

        results = await asyncio.gather(*[_send(r) for r in recipients])
        sent = sum(1 for ok in results if ok)

        if sent:
            post.newsletter_sent_at = datetime.utcnow()
            post.newsletter_recipient_count = sent
            await db.commit()
        return {"sent": sent, "skipped": False, "recipient_count": len(recipients)}

    async def send_blog_newsletter_for_post(
        self, post_id: str, base_url: str, *, force: bool = False
    ) -> Dict[str, Any]:
        """Background entry point: opens its own session and never raises."""
        from uuid import UUID as _UUID

        from app.core.database import AsyncSessionLocal
        from app.models.blog_post import BlogPost

        try:
            async with AsyncSessionLocal() as db:
                post = await db.get(BlogPost, _UUID(str(post_id)))
                if post is None:
                    return {"sent": 0, "skipped": True, "reason": "post not found"}
                return await self.send_blog_newsletter(db, post, base_url, force=force)
        except Exception as exc:
            logger.error("Blog newsletter background send failed: %s", exc)
            return {"sent": 0, "skipped": True, "reason": str(exc)}

