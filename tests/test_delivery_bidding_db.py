"""DB-backed integration tests for post-confirmation delivery bidding.

These exercise the real SQL statements (atomic claim UPDATEs, invoice idempotency,
bid replacement, job re-opening) against an in-memory SQLite database, with the
payment gateway stubbed out. They complement the pure unit tests in
`test_delivery_bidding.py` / `test_delivery_bidding_api.py`.

PostgreSQL-only column types are mapped to SQLite equivalents below, so the ORM
DDL can be created in memory; production still runs on PostgreSQL.
"""
import asyncio
import contextlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import JSON as SAJSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(PGUUID, "sqlite")
def _uuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


from app.core.database import Base
from app.models.delivery import DeliveryJob
from app.models.delivery_bid import BID_EXPIRED, BID_PENDING, BID_REJECTED, DeliveryBid
from app.models.driver import DriverProfile
from app.models.order import Order, OrderItem
from app.models.shipping_invoice import INVOICE_COMPLETED, INVOICE_FAILED, ShippingInvoice
from app.models.user import User
from app.models.vendor import Vendor
from app.services import bid_dispatch_service as bids
from app.services import payment_service, shipping_payment_service as shipping

_TABLES = [
    "users", "vendors", "vendor_addresses", "customer_addresses", "orders",
    "order_items", "driver_profiles", "delivery_jobs", "delivery_bids",
    "delivery_job_events", "shipping_invoices", "notifications",
]


def _tables():
    return [Base.metadata.tables[name] for name in _TABLES if name in Base.metadata.tables]


def _shim_pg_types():
    """Give PostgreSQL ARRAY/JSONB columns a SQLite-compatible JSON type.

    Mutating the mapped columns (not just the DDL) is required: the default ARRAY
    bind processor would otherwise hand a Python list straight to sqlite3.
    """
    from sqlalchemy.dialects.postgresql import ARRAY as PGARRAY
    from sqlalchemy.dialects.postgresql import JSONB as PGJSONB

    for table in _tables():
        for column in table.columns:
            if isinstance(column.type, (PGARRAY, PGJSONB)):
                column.type = SAJSON()


_shim_pg_types()


@contextlib.asynccontextmanager
async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=_tables()))
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _run(case):
    return asyncio.run(case())


async def _seed(db, *, vendors=1):
    """A paid order whose items span `vendors` vendor groups."""
    buyer = User(email=f"buyer-{uuid.uuid4().hex[:6]}@test.dev",
                 role="customer", status="active")
    db.add(buyer)
    await db.flush()

    vendor_objs = []
    for i in range(vendors):
        vendor_user = User(email=f"vendor-{uuid.uuid4().hex[:6]}@test.dev",
                           role="vendor", status="active")
        db.add(vendor_user)
        await db.flush()
        vendor = Vendor(
            user_id=vendor_user.id, business_name=f"Vendor {i} Ltd", business_type="supplier",
            city="Lagos", state="Lagos", business_address=f"{i} Market Road",
        )
        db.add(vendor)
        await db.flush()
        vendor_objs.append(vendor)

    order = Order(
        order_number=f"BC-TEST-{uuid.uuid4().hex[:8].upper()}",
        user_id=buyer.id, status="confirmed", payment_status="completed",
        subtotal=Decimal("10000.00"), total_amount=Decimal("10000.00"),
    )
    db.add(order)
    await db.flush()

    for i, vendor in enumerate(vendor_objs):
        db.add(OrderItem(
            order_id=order.id, product_id=uuid.uuid4(), vendor_id=vendor.id,
            product_name=f"Cement {i}", sku=f"SKU-{i}", quantity=2,
            unit_price=Decimal("5000.00"), total_price=Decimal("10000.00"),
        ))
    await db.commit()
    return buyer, vendor_objs, order


async def _driver(db, vendor, *, name="Driver"):
    user = User(email=f"driver-{uuid.uuid4().hex[:6]}@test.dev",
                role="driver", status="active")
    db.add(user)
    await db.flush()
    profile = DriverProfile(
        user_id=user.id, vendor_id=vendor.id, source="vendor", status="active",
        full_name=name, phone=f"0801{uuid.uuid4().int % 10**7:07d}",
    )
    db.add(profile)
    await db.commit()
    return profile


def _stub_gateway(monkeypatch, *, provider="mock"):
    async def _initialize_payment(self, **kwargs):
        return {"success": True, "provider": provider,
                "authorization_url": "https://pay.test/x"}

    monkeypatch.setattr(payment_service.PaymentService, "initialize_payment",
                        _initialize_payment)


# ── Job creation ─────────────────────────────────────────────────────────────

def test_one_bid_job_per_vendor_group_with_a_48h_window():
    async def case():
        async with _db() as db:
            _, vendors, order = await _seed(db, vendors=2)
            jobs = await bids.create_bid_jobs_for_order(db, order)
            await db.commit()

            assert len(jobs) == 2
            for job in jobs:
                assert job.assignment_type == "bid"
                assert job.status == "available"
                assert job.shipping_paid is False
                assert job.delivery_fee == Decimal("0.00")
                assert job.pickup_address  # vendor snapshot copied
                delta = job.bidding_closes_at - datetime.utcnow()
                assert timedelta(hours=47) < delta <= timedelta(hours=48)

            # Idempotent: a retried confirmation must not open duplicate jobs.
            assert await bids.create_bid_jobs_for_order(db, order) == []
            await db.commit()
    _run(case)


# ── Bids ─────────────────────────────────────────────────────────────────────

def test_rebidding_replaces_the_amount_instead_of_stacking_rows():
    async def case():
        async with _db() as db:
            _, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            await db.commit()

            await bids.place_bid(db, job, driver, 3000)
            await db.commit()
            await bids.place_bid(db, job, driver, 2600, note="  flexible  ")
            await db.commit()

            rows = (await db.execute(
                DeliveryBid.__table__.select().where(DeliveryBid.job_id == job.id)
            )).fetchall()
            assert len(rows) == 1
            assert rows[0].amount == Decimal("2600.00")
            assert rows[0].note == "flexible"
            assert rows[0].status == BID_PENDING
    _run(case)


def test_accepting_a_bid_assigns_the_driver_and_keeps_payment_pending():
    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            cheap = await _driver(db, vendor, name="Cheap")
            dear = await _driver(db, vendor, name="Dear")
            bid_a = await bids.place_bid(db, job, cheap, 2500)
            bid_b = await bids.place_bid(db, job, dear, 4100)
            await db.commit()

            accepted_job, accepted_bid = await bids.accept_bid(db, bid_a, buyer.id)
            await db.commit()

            assert accepted_job.status == "accepted"
            assert accepted_job.driver_profile_id == cheap.id
            assert accepted_job.driver_phone == cheap.phone
            assert accepted_job.delivery_fee == Decimal("2500.00")
            # Nobody is auto-charged: the fee still has to be paid by the buyer.
            assert accepted_job.shipping_paid is False
            assert accepted_job.shipping_invoice_id is None

            other = (await db.execute(
                DeliveryBid.__table__.select().where(DeliveryBid.id == bid_b.id)
            )).first()
            assert other.status == BID_REJECTED
            assert order.shipping_payment_status == "pending"
            assert accepted_bid.status == "accepted"
    _run(case)


def test_bids_are_refused_after_acceptance_or_window_close():
    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            late = await _driver(db, vendor, name="Late")
            winning = await bids.place_bid(db, job, driver, 2000)
            await db.commit()

            await bids.accept_bid(db, winning, buyer.id)
            await db.commit()

            with pytest.raises(Exception) as accepted_closed:
                await bids.place_bid(db, job, late, 1900)
            assert "closed" in str(accepted_closed.value).lower()
            await db.rollback()

            # The window itself also closes the job for new bids.
            _, (vendor2,), order2 = await _seed(db)
            (fresh,) = await bids.create_bid_jobs_for_order(db, order2)
            fresh.bidding_closes_at = datetime.utcnow() - timedelta(minutes=1)
            await db.commit()
            with pytest.raises(Exception) as expired:
                await bids.place_bid(db, fresh, late, 1500)
            assert "closed" in str(expired.value).lower()
            await db.rollback()
    _run(case)


def test_buyer_only_operations_are_scoped_to_the_owning_order():
    async def case():
        async with _db() as db:
            _, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 1800)
            await db.commit()

            stranger = uuid.uuid4()
            job_id = job.id
            with pytest.raises(Exception) as reject_forbidden:
                await bids.reject_bid(db, bid, stranger)
            assert "not on your order" in str(reject_forbidden.value).lower()
            await db.rollback()

            # The rollback expires the instance; reload before the next call.
            job = await db.get(DeliveryJob, job_id)
            with pytest.raises(Exception) as extend_forbidden:
                await bids.extend_bidding(db, job, buyer_id=stranger)
            assert "not on your order" in str(extend_forbidden.value).lower()
            await db.rollback()
    _run(case)


def test_withdrawing_a_bid_frees_the_driver_to_rebid():
    async def case():
        async with _db() as db:
            _, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            other = await _driver(db, vendor, name="Other")
            bid = await bids.place_bid(db, job, driver, 3000)
            await db.commit()

            with pytest.raises(Exception):
                await bids.withdraw_bid(db, bid, other.id)  # not your bid
            await db.rollback()

            withdrawn = await bids.withdraw_bid(db, bid, driver.id)
            await db.commit()
            assert withdrawn.status == "withdrawn"

            rebid = await bids.place_bid(db, job, driver, 2200)
            await db.commit()
            assert rebid.id == bid.id
            assert rebid.status == BID_PENDING
            assert rebid.amount == Decimal("2200.00")
    _run(case)


# ── Vendor quote / decline ───────────────────────────────────────────────────

def test_vendor_quote_closes_bidding_and_expires_pending_bids():
    async def case():
        async with _db() as db:
            _, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 3200)
            await db.commit()

            quoted = await bids.apply_vendor_quote(db, job, 3500)
            await db.commit()

            assert quoted.delivery_fee == Decimal("3500.00")
            assert bids.is_bidding_open(quoted) is False
            row = (await db.execute(
                DeliveryBid.__table__.select().where(DeliveryBid.id == bid.id)
            )).first()
            assert row.status == BID_EXPIRED
            assert quoted.shipping_paid is False  # buyer still pays to accept
    _run(case)


def test_declining_the_vendor_quote_reopens_a_fresh_bid_job():
    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            await bids.apply_vendor_quote(db, job, 4000)
            await db.commit()

            opened = await bids.decline_vendor_delivery(db, job, buyer.id)
            await db.commit()

            assert job.status == "cancelled"
            assert job.vendor_fee_declined_at is not None
            assert len(opened) == 1
            fresh = opened[0]
            assert fresh.id != job.id
            assert fresh.assignment_type == "bid"
            assert fresh.shipping_paid is False
            assert fresh.delivery_fee == Decimal("0.00")
            assert fresh.bidding_closes_at > datetime.utcnow()
    _run(case)


# ── Delivery payment ─────────────────────────────────────────────────────────

def test_delivery_invoice_settles_once_and_releases_only_its_jobs(monkeypatch):
    _stub_gateway(monkeypatch)

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 2750)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            invoice = await shipping.build_invoice(db, order)
            assert invoice["success"] is True
            assert invoice["amount"] == 2750.0
            assert invoice["job_count"] == 1
            reference = invoice["reference"]

            first = await shipping.confirm_reference(
                db, reference, provider="mock", user_id=str(buyer.id))
            assert first["status"] == "settled"
            assert first["job_count"] == 1

            await db.refresh(job)
            assert job.shipping_paid is True
            assert job.shipping_invoice_id is not None
            await db.refresh(order)
            assert order.shipping_payment_status == "paid"
            assert order.delivery_fee_total == Decimal("2750.00")

            again = await shipping.confirm_reference(
                db, reference, provider="mock", user_id=str(buyer.id))
            assert again["status"] == "already_settled"

            nothing_due = await shipping.build_invoice(db, order)
            assert nothing_due["success"] is False
            assert nothing_due["error"] == "nothing_due"

            # Another user can never settle this invoice.
            stranger = await shipping.confirm_reference(
                db, reference, provider="mock", user_id=str(uuid.uuid4()))
            assert stranger["status"] in ("already_settled", "forbidden")
    _run(case)


def test_second_link_supersedes_the_first_and_cannot_double_release(monkeypatch):
    _stub_gateway(monkeypatch)

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 5000)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            first = await shipping.build_invoice(db, order)
            second = await shipping.build_invoice(db, order)
            assert first["reference"] != second["reference"]

            old = (await db.execute(
                ShippingInvoice.__table__.select()
                .where(ShippingInvoice.reference == first["reference"])
            )).first()
            assert old.status == INVOICE_FAILED  # only one live link may exist

            settled = await shipping.confirm_reference(
                db, second["reference"], provider="mock", user_id=str(buyer.id))
            assert settled["status"] == "settled"

            stale = await shipping.confirm_reference(
                db, first["reference"], provider="mock", user_id=str(buyer.id))
            assert stale["status"] == "already_covered"

            new = (await db.execute(
                ShippingInvoice.__table__.select()
                .where(ShippingInvoice.reference == second["reference"])
            )).first()
            assert new.status == INVOICE_COMPLETED
            await db.refresh(job)
            assert job.shipping_paid is True
    _run(case)


def test_gateway_amount_mismatch_never_releases_the_job(monkeypatch):
    _stub_gateway(monkeypatch, provider="paystack")

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 6000)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            invoice = await shipping.build_invoice(db, order)
            outcome = await shipping.confirm_reference(
                db, invoice["reference"], provider="paystack",
                paid_amount=60.0, user_id=str(buyer.id))
            assert outcome["status"] == "amount_mismatch"

            await db.refresh(job)
            assert job.shipping_paid is False
            assert job.shipping_invoice_id is None
            assert (await shipping.delivery_due(db, order.id)) == Decimal("6000.00")
    _run(case)


# ── Failed charges + return trip ───────────────────────────────────────────────

def test_failed_gateway_charge_marks_the_link_failed_and_allows_a_retry(monkeypatch):
    """A declined card must not leave the only live link pointing at a dead charge."""
    _stub_gateway(monkeypatch, provider="paystack")

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 4000)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            invoice = await shipping.build_invoice(db, order)
            await shipping.mark_failed(db, invoice["reference"])
            await db.commit()

            row = (await db.execute(
                ShippingInvoice.__table__.select()
                .where(ShippingInvoice.reference == invoice["reference"])
            )).first()
            assert row.status == INVOICE_FAILED

            # The buyer is quoted again on a new reference, and only a charge that
            # really landed can settle (mark_failed is a hint, never a block).
            retry = await shipping.build_invoice(db, order)
            assert retry["success"] is True
            assert retry["reference"] != invoice["reference"]

            await db.refresh(job)
            assert job.shipping_paid is False
    _run(case)


def test_mark_failed_never_downgrades_a_completed_invoice(monkeypatch):
    _stub_gateway(monkeypatch, provider="mock")

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 3000)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            invoice = await shipping.build_invoice(db, order)
            await shipping.confirm_reference(
                db, invoice["reference"], provider="mock", user_id=str(buyer.id))

            # A late/duplicate "failed" webhook must not undo a settled payment.
            await shipping.mark_failed(db, invoice["reference"])
            await db.commit()

            row = (await db.execute(
                ShippingInvoice.__table__.select()
                .where(ShippingInvoice.reference == invoice["reference"])
            )).first()
            assert row.status == INVOICE_COMPLETED
            await db.refresh(job)
            assert job.shipping_paid is True
    _run(case)


def test_return_trip_verify_is_scoped_to_the_invoice_owner(monkeypatch):
    """`GET /delivery/shipping/verify` settles only the caller's own delivery."""
    from fastapi import HTTPException

    from app.api.v1.endpoints import delivery_bidding as api

    _stub_gateway(monkeypatch, provider="mock")

    async def case():
        async with _db() as db:
            buyer, (vendor,), order = await _seed(db)
            (job,) = await bids.create_bid_jobs_for_order(db, order)
            driver = await _driver(db, vendor)
            bid = await bids.place_bid(db, job, driver, 3500)
            await bids.accept_bid(db, bid, buyer.id)
            await db.commit()

            invoice = await shipping.build_invoice(db, order)
            outsider = User(email=f"other-{uuid.uuid4().hex[:6]}@test.dev",
                            role="customer", status="active")
            db.add(outsider)
            await db.commit()

            with pytest.raises(HTTPException) as exc:
                await api.verify_shipping_payment(
                    reference=invoice["reference"], provider=None, gateway=None,
                    current_user=outsider, db=db)
            assert exc.value.status_code == 403
            await db.refresh(job)
            assert job.shipping_paid is False

            # The buyer's own return trip settles it exactly once.
            first = await api.verify_shipping_payment(
                reference=invoice["reference"], provider=None, gateway=None,
                current_user=buyer, db=db)
            assert first["applied"] is True
            assert first["shipping"]["status"] == "settled"
            assert first["message"]
            await db.refresh(job)
            assert job.shipping_paid is True

            # A refresh (or the gateway replaying the redirect) is a no-op. Detach
            # the loaded rows first: each real request arrives on a fresh session
            # and reads the invoice's committed status rather than a cached object.
            db.expunge_all()
            again = await api.verify_shipping_payment(
                reference=invoice["reference"], provider=None, gateway=None,
                current_user=buyer, db=db)
            assert again["applied"] is True
            assert again["shipping"]["status"] == "already_settled"
    _run(case)


