"""Load demo customers, orders and refunds for the tools and evals.

``python -m app.seed`` clears the business tables (customers, orders, items, refunds,
tickets and chat data) and reloads them. Dates are relative to now, so the return-window and
refund-frequency boundaries are always in the same place. The knowledge base is untouched.

Passwords are an unusable placeholder until phase 4 adds login.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.db import SessionLocal, engine

UNUSABLE_PASSWORD_HASH = "!"

BUSINESS_TABLES = "chat_messages, chat_sessions, refunds, tickets, order_items, orders, customers"


@dataclass(frozen=True)
class Line:
    sku: str
    name: str
    price_cents: int
    quantity: int = 1
    category: str = "other"
    final_sale: bool = False


@dataclass
class Seeded:
    customers: dict[str, models.Customer]
    orders: dict[str, models.Order]
    items: dict[str, models.OrderItem]  # by sku
    refunds: list[models.Refund]


class _Builder:
    def __init__(self, session: AsyncSession, now: datetime):
        self.session = session
        self.now = now
        self.seeded = Seeded({}, {}, {}, [])

    def ago(self, days: int) -> datetime:
        return self.now - timedelta(days=days)

    async def customer(self, key: str, name: str, membership: str = "none", role: str = "customer"):
        row = models.Customer(
            email=f"{key}@example.com",
            name=name,
            role=role,
            membership=membership,
            password_hash=UNUSABLE_PASSWORD_HASH,
        )
        self.session.add(row)
        await self.session.flush()
        self.seeded.customers[key] = row
        return row

    async def order(
        self,
        key: str,
        owner: str,
        lines: Sequence[Line],
        delivered_days_ago: int | None = None,
        status: str | None = None,
    ) -> models.Order:
        delivered = delivered_days_ago is not None
        placed_days_ago = (delivered_days_ago + 4) if delivered else 2
        row = models.Order(
            customer_id=self.seeded.customers[owner].id,
            status=status or ("delivered" if delivered else "shipped"),
            placed_at=self.ago(placed_days_ago),
            delivered_at=self.ago(delivered_days_ago) if delivered else None,
            total_cents=sum(line.price_cents * line.quantity for line in lines),
        )
        self.session.add(row)
        await self.session.flush()
        for line in lines:
            item = models.OrderItem(
                order_id=row.id,
                sku=line.sku,
                name=line.name,
                price_cents=line.price_cents,
                quantity=line.quantity,
                is_final_sale=line.final_sale,
                category=line.category,
            )
            self.session.add(item)
            await self.session.flush()
            self.seeded.items[line.sku] = item
        self.seeded.orders[key] = row
        return row

    async def refund(
        self, order_key: str, sku: str, days_ago: int, quantity: int = 1, status: str = "completed"
    ) -> None:
        order, item = self.seeded.orders[order_key], self.seeded.items[sku]
        row = models.Refund(
            order_id=order.id,
            order_item_id=item.id,
            customer_id=order.customer_id,
            quantity=quantity,
            amount_cents=item.price_cents * quantity,
            reason="changed_mind",
            status=status,
            created_at=self.ago(days_ago),
        )
        self.session.add(row)
        await self.session.flush()
        self.seeded.refunds.append(row)


async def seed(session: AsyncSession, now: datetime | None = None) -> Seeded:
    """Clear the business tables and load the demo data. The caller commits."""
    now = now or datetime.now(UTC)
    await session.execute(text(f"TRUNCATE {BUSINESS_TABLES} RESTART IDENTITY CASCADE"))
    session.expunge_all()  # rows from an earlier seed in this session no longer exist
    b = _Builder(session, now)

    await b.customer("alex", "Alex Rivera", "basic")
    await b.customer("priya", "Priya Shah", "plus")
    await b.customer("sam", "Sam Okafor", "none")
    await b.customer("jordan", "Jordan Lee", "basic")
    await b.customer("morgan", "Morgan Chen", "basic")
    await b.customer("approver", "Taylor Brooks", role="approver")

    # Alex (Basic): one order per point on the 30-day window, plus other rule cases
    await b.order(
        "alex-5d", "alex", [Line("BOOT-TR", "Trail boots", 12900, category="footwear")], 5
    )
    await b.order(
        "alex-29d", "alex", [Line("JKT-DWN", "Down jacket", 15900, category="apparel")], 29
    )
    await b.order("alex-30d", "alex", [Line("LAMP-HD", "Headlamp", 3900)], 30)
    await b.order("alex-31d", "alex", [Line("BAG-SLP", "Sleeping bag", 12000)], 31)
    await b.order("alex-45d", "alex", [Line("POLE-TR", "Trekking poles", 6000)], 45)
    await b.order("alex-shipped", "alex", [Line("BTL-WTR", "Water bottle", 1800)])
    await b.order(
        "alex-final-sale",
        "alex",
        [Line("SHL-RN", "Rain shell (last season)", 5900, category="apparel", final_sale=True)],
        10,
    )
    await b.order(
        "alex-gift-card", "alex", [Line("GC-50", "Gift card", 5000, category="gift_card")], 3
    )
    await b.order("alex-socks", "alex", [Line("SOCK-TR", "Trail socks", 1400, 3, "apparel")], 9)
    await b.refund("alex-socks", "SOCK-TR", days_ago=2, quantity=1)  # 2 of 3 units left

    # Priya (Plus): 60-day window, items over the $200 approval threshold
    await b.order(
        "priya-45d", "priya", [Line("TENT-4S", "Four-season tent", 42900, category="tent")], 45
    )
    await b.order(
        "priya-60d", "priya", [Line("PACK-65", "65L backpack", 21900, category="pack")], 60
    )
    await b.order(
        "priya-61d", "priya", [Line("GLV-WN", "Winter gloves", 3500, category="apparel")], 61
    )
    await b.order("priya-20d", "priya", [Line("SOCK-MR", "Merino socks", 2200, 2, "apparel")], 20)
    await b.order(
        "priya-parka", "priya", [Line("PRK-INS", "Insulated parka", 24000, category="apparel")], 12
    )
    await b.refund(
        "priya-parka", "PRK-INS", days_ago=1, status="pending"
    )  # waiting for an approver

    # Sam (no membership): consumable, a processing order
    await b.order(
        "sam-30d", "sam", [Line("WTR-FLT", "Water filter", 3500, category="consumable")], 30
    )
    await b.order("sam-31d", "sam", [Line("STV-BP", "Backpacking stove", 7900)], 31)
    await b.order(
        "sam-processing",
        "sam",
        [Line("TENT-2P", "Two-person tent", 18900, category="tent")],
        status="processing",
    )

    # Jordan (Basic): three refunds in the last 90 days, so the next one goes to a person
    for key, sku, name, age in (
        ("jordan-r1", "CUP-TI", "Titanium cup", 5),
        ("jordan-r2", "HAT-SUN", "Sun hat", 30),
        ("jordan-r3", "BELT-HK", "Hiking belt", 60),
    ):
        await b.order(key, "jordan", [Line(sku, name, 2500)], age + 8)
        await b.refund(key, sku, days_ago=age)
    await b.order("jordan-current", "jordan", [Line("LNT-CMP", "Camp lantern", 4500)], 7)

    # Morgan (Basic): three refunds, but the oldest is 91 days old, so only two count
    for key, sku, name, age in (
        ("morgan-r1", "KNF-FLD", "Folding knife", 20),
        ("morgan-r2", "MAT-FM", "Foam mat", 45),
        ("morgan-r3", "TWL-QD", "Quick-dry towel", 91),
    ):
        await b.order(key, "morgan", [Line(sku, name, 3000)], age + 8)
        await b.refund(key, sku, days_ago=age)
    await b.order("morgan-current", "morgan", [Line("CMP-MG", "Camp mug set", 7900)], 6)

    return b.seeded


async def main() -> None:
    try:
        async with SessionLocal.begin() as session:
            seeded = await seed(session)
        print(
            f"seeded {len(seeded.customers)} customers, {len(seeded.orders)} orders, "
            f"{len(seeded.items)} items, {len(seeded.refunds)} refunds"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
