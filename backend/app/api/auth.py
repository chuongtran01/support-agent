from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_customer
from app.auth import CurrentCustomer, authenticate, create_token
from app.db import get_session

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class Profile(BaseModel):
    name: str
    role: str
    membership: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    customer: Profile


def _profile(customer: CurrentCustomer) -> Profile:
    return Profile(name=customer.name, role=customer.role, membership=customer.membership)


@router.post("/auth/login")
async def login(
    body: LoginRequest, session: Annotated[AsyncSession, Depends(get_session)]
) -> LoginResponse:
    customer = await authenticate(session, body.email, body.password)
    if customer is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return LoginResponse(token=create_token(customer.id), customer=_profile(customer))


@router.get("/me")
async def me(customer: Annotated[CurrentCustomer, Depends(current_customer)]) -> Profile:
    return _profile(customer)
