from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_customer
from app.auth import CurrentCustomer
from app.db import get_session
from app.orders import orders_for
from app.tools.results import OrderView

router = APIRouter()


@router.get("/orders")
async def list_orders(
    customer: Annotated[CurrentCustomer, Depends(current_customer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[OrderView]:
    """The caller's own orders, newest first, with their items and refunds."""
    return await orders_for(session, customer.id)
