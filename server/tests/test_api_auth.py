import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _db_available():
    try:
        from sqlalchemy import create_engine, text

        from src.config import settings
        sync_url = settings.sync_database_url.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_available(), reason="PostgreSQL not available")

from src.main import app  # noqa: E402


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
@db_required
class TestAuthAPI:
    async def test_register_and_login(self, client: AsyncClient):
        # register
        res = await client.post("/api/v1/auth/register", json={
            "username": "testuser1", "email": "test1@test.com", "password": "pass123"
        })
        assert res.status_code == 200
        data = res.json()
        assert data["username"] == "testuser1"
        assert data["email"] == "test1@test.com"

        # login
        res = await client.post("/api/v1/auth/login", json={
            "username": "testuser1", "password": "pass123"
        })
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_refresh_token(self, client: AsyncClient):
        # register & login
        await client.post("/api/v1/auth/register", json={
            "username": "testuser2", "email": "test2@test.com", "password": "pass123"
        })
        login_res = await client.post("/api/v1/auth/login", json={
            "username": "testuser2", "password": "pass123"
        })
        refresh = login_res.json()["refresh_token"]

        # refresh
        res = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert "refresh_token" in data

    async def test_invalid_refresh(self, client: AsyncClient):
        res = await client.post("/api/v1/auth/refresh", json={"refresh_token": "bad.token.here"})
        assert res.status_code == 401

    async def test_me_endpoint(self, client: AsyncClient):
        await client.post("/api/v1/auth/register", json={
            "username": "testuser3", "email": "test3@test.com", "password": "pass123"
        })
        login_res = await client.post("/api/v1/auth/login", json={
            "username": "testuser3", "password": "pass123"
        })
        token = login_res.json()["access_token"]

        res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 200
        data = res.json()
        assert data["username"] == "testuser3"
        assert data["email"] == "test3@test.com"

    async def test_unauthorized(self, client: AsyncClient):
        res = await client.get("/api/v1/auth/me")
        assert res.status_code == 401

    async def test_wrong_password(self, client: AsyncClient):
        await client.post("/api/v1/auth/register", json={
            "username": "testuser4", "email": "test4@test.com", "password": "correct"
        })
        res = await client.post("/api/v1/auth/login", json={
            "username": "testuser4", "password": "wrong"
        })
        assert res.status_code == 401

    async def test_duplicate_username(self, client: AsyncClient):
        await client.post("/api/v1/auth/register", json={
            "username": "dupuser", "email": "a@test.com", "password": "pass123"
        })
        res = await client.post("/api/v1/auth/register", json={
            "username": "dupuser", "email": "b@test.com", "password": "pass123"
        })
        assert res.status_code == 400
