from datetime import UTC, datetime, timedelta
from typing import Annotated

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app import models
from app.api.auth import router
from app.auth import (
    ALGORITHM,
    CurrentCustomer,
    create_token,
    current_customer,
    hash_password,
    read_token,
    require_approver,
    verify_password,
)
from app.config import settings
from app.db import get_session
from app.seed import seed

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
PASSWORD = "demo"


# Passwords and tokens: no database


def test_a_password_checks_against_its_hash():
    stored = hash_password("correct horse")
    assert stored != "correct horse"
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)


def test_the_same_password_hashes_differently_each_time():
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize("stored", ["!", "", "not a hash"])
def test_an_unusable_stored_hash_never_matches(stored):
    assert not verify_password("anything", stored)
    assert not verify_password("", stored)


def test_a_token_gives_back_its_customer():
    assert read_token(create_token(42)) == 42


def test_an_expired_token_is_refused():
    long_ago = datetime.now(UTC) - timedelta(minutes=settings.token_ttl_minutes + 1)
    assert read_token(create_token(42, now=long_ago)) is None


def test_a_token_lasts_until_its_expiry():
    almost = datetime.now(UTC) - timedelta(minutes=settings.token_ttl_minutes - 1)
    assert read_token(create_token(42, now=almost)) == 42


def test_a_tampered_token_is_refused():
    header, payload, signature = create_token(42).split(".")
    other = create_token(43).split(".")[1]
    assert read_token(f"{header}.{other}.{signature}") is None
    assert read_token(f"{header}.{payload}.{signature[:-2]}xx") is None


def test_a_token_signed_with_another_secret_is_refused():
    claims = {"sub": "42", "exp": datetime.now(UTC) + timedelta(hours=1)}
    forged = jwt.encode(claims, "some other secret that is long enough", algorithm=ALGORITHM)
    assert read_token(forged) is None


def test_an_unsigned_token_is_refused():
    claims = {"sub": "42", "exp": datetime.now(UTC) + timedelta(hours=1)}
    assert read_token(jwt.encode(claims, None, algorithm="none")) is None


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "42"},  # never expires
        {"exp": datetime.now(UTC) + timedelta(hours=1)},  # no customer
        {"sub": "forty-two", "exp": datetime.now(UTC) + timedelta(hours=1)},
    ],
)
def test_a_token_missing_or_mangling_a_claim_is_refused(claims):
    token = jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)
    assert read_token(token) is None


@pytest.mark.parametrize("token", ["", "abc", "a.b.c"])
def test_garbage_is_refused(token):
    assert read_token(token) is None


# The endpoints, against the test database


def _probe_app() -> FastAPI:
    """The real login router plus routes that show what the dependencies hand over."""
    app = FastAPI()
    app.include_router(router)

    @app.get("/approver-only")
    async def approver_only(customer: Annotated[CurrentCustomer, Depends(require_approver)]):
        return {"id": customer.id}

    @app.get("/transaction-state")
    async def transaction_state(
        _: Annotated[CurrentCustomer, Depends(current_customer)],
        session: Annotated[AsyncSession, Depends(get_session)],
    ):
        return {"in_transaction": session.in_transaction()}

    return app


@pytest_asyncio.fixture
async def world(session, test_database_url):
    async with session.begin():
        seeded = await seed(session, NOW, PASSWORD)
    ids = {key: row.id for key, row in seeded.customers.items()}

    engine = create_async_engine(test_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_session():
        async with sessions() as request_session:
            yield request_session

    app = _probe_app()
    app.dependency_overrides[get_session] = get_test_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.ids = ids
        client.sessions = sessions
        yield client
    await engine.dispose()


def bearer(customer_id: int, now: datetime | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_token(customer_id, now)}"}


async def login(client, email="alex@example.com", password=PASSWORD):
    return await client.post("/auth/login", json={"email": email, "password": password})


async def test_seeded_customers_all_use_the_seed_password(session):
    async with session.begin():
        await seed(session, NOW, "s3cret")
    hashes = (await session.scalars(select(models.Customer.password_hash))).all()
    assert len(hashes) == 6
    assert all(verify_password("s3cret", stored) for stored in hashes)
    assert not any(verify_password("demo", stored) for stored in hashes)


async def test_login_returns_a_token_and_the_profile(world):
    response = await login(world)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["customer"] == {"name": "Alex Rivera", "role": "customer", "membership": "basic"}
    assert read_token(body["token"]) == world.ids["alex"]


async def test_login_ignores_case_and_spaces_in_the_email(world):
    assert (await login(world, "  ALEX@Example.com ")).status_code == 200


async def test_a_wrong_password_and_an_unknown_email_look_the_same(world):
    wrong_password = await login(world, password="nope")
    unknown_email = await login(world, email="nobody@example.com")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


async def test_login_does_not_return_the_password_hash(world):
    assert "password" not in (await login(world)).text


@pytest.mark.parametrize(
    "body",
    [
        {"email": "alex@example.com"},
        {"password": PASSWORD},
        {"email": "alex@example.com", "password": PASSWORD, "role": "approver"},
    ],
)
async def test_login_rejects_a_malformed_body(world, body):
    assert (await world.post("/auth/login", json=body)).status_code == 422


async def test_me_returns_the_token_holders_profile(world):
    response = await world.get("/me", headers=bearer(world.ids["priya"]))
    assert response.status_code == 200
    assert response.json() == {"name": "Priya Shah", "role": "customer", "membership": "plus"}


async def test_a_token_from_login_works_on_me(world):
    token = (await login(world)).json()["token"]
    response = await world.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.json()["name"] == "Alex Rivera"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Basic YWxleDpkZW1v"},
    ],
)
async def test_me_without_a_usable_token_is_401(world, headers):
    response = await world.get("/me", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_an_expired_token_is_401(world):
    old = datetime.now(UTC) - timedelta(minutes=settings.token_ttl_minutes + 1)
    assert (await world.get("/me", headers=bearer(world.ids["alex"], old))).status_code == 401


async def test_a_token_for_a_customer_who_no_longer_exists_is_401(world):
    assert (await world.get("/me", headers=bearer(9999))).status_code == 401


async def test_a_customer_is_not_an_approver(world):
    response = await world.get("/approver-only", headers=bearer(world.ids["alex"]))
    assert response.status_code == 403


async def test_an_approver_gets_through(world):
    response = await world.get("/approver-only", headers=bearer(world.ids["approver"]))
    assert response.status_code == 200
    assert response.json() == {"id": world.ids["approver"]}


async def test_no_token_on_an_approver_route_is_401_not_403(world):
    assert (await world.get("/approver-only")).status_code == 401


async def test_demoting_an_approver_takes_effect_on_their_existing_token(world):
    headers = bearer(world.ids["approver"])
    assert (await world.get("/approver-only", headers=headers)).status_code == 200
    async with world.sessions.begin() as other:
        await other.execute(
            update(models.Customer)
            .where(models.Customer.id == world.ids["approver"])
            .values(role="customer")
        )
    assert (await world.get("/approver-only", headers=headers)).status_code == 403


async def test_the_session_is_left_outside_a_transaction(world):
    response = await world.get("/transaction-state", headers=bearer(world.ids["alex"]))
    assert response.json() == {"in_transaction": False}
