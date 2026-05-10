"""Property-based test for image count invariant (Property 1).

**Property 1: Image Count Invariant**
*For any* feedback submission attempt, if the number of images exceeds 5,
the system SHALL reject the submission and the feedback SHALL NOT be created.

**Validates: Requirements 3.5, 3.6**

This test uses hypothesis to generate random image arrays with more than 5 items
and verifies they are rejected with 422 status code.
"""
from __future__ import annotations

import asyncio
import socket
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st


def _db_available():
    """Check if PostgreSQL is available via socket connection."""
    try:
        from src.config import settings
        # Parse host and port from database URL
        url = settings.database_url
        at_idx = url.find("@")
        if at_idx == -1:
            return False
        rest = url[at_idx + 1:]
        slash_idx = rest.find("/")
        if slash_idx == -1:
            return False
        host_port = rest[:slash_idx]
        if ":" in host_port:
            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)
        else:
            host = host_port
            port = 5432

        with socket.create_connection((host, port), timeout=2.0):
            return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_available(), reason="PostgreSQL not available")


# ---------------------------------------------------------------------------
# Hypothesis strategies for image URL generation
# ---------------------------------------------------------------------------

def image_url_strategy() -> st.SearchStrategy[str]:
    """Generate valid-looking image URLs for testing.
    
    These URLs follow the pattern used by the feedback image upload endpoint.
    """
    # Generate a hex hash (16 chars like the real implementation)
    hash_strategy = st.text(
        alphabet="0123456789abcdef",
        min_size=16,
        max_size=16,
    )
    # Generate valid image extensions
    ext_strategy = st.sampled_from([".png", ".jpg", ".jpeg", ".gif", ".webp"])
    
    return st.builds(
        lambda h, e: f"/uploads/feedbacks/{h}{e}",
        hash_strategy,
        ext_strategy,
    )


def image_list_exceeding_limit() -> st.SearchStrategy[list[str]]:
    """Generate lists of image URLs with more than 5 items (the limit).
    
    This strategy generates lists with 6 to 20 images to test the invariant
    that feedback with > 5 images should be rejected.
    """
    return st.lists(
        image_url_strategy(),
        min_size=6,
        max_size=20,
    )


def image_list_within_limit() -> st.SearchStrategy[list[str]]:
    """Generate lists of image URLs with 0 to 5 items (within the limit).
    
    This strategy generates valid image lists that should be accepted.
    """
    return st.lists(
        image_url_strategy(),
        min_size=0,
        max_size=5,
    )


def feedback_type_strategy() -> st.SearchStrategy[str]:
    """Generate valid feedback types."""
    return st.sampled_from(["bug", "feature"])


def feedback_title_strategy() -> st.SearchStrategy[str]:
    """Generate non-empty feedback titles."""
    return st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
        min_size=1,
        max_size=100,
    ).filter(lambda s: s.strip())


def feedback_description_strategy() -> st.SearchStrategy[str]:
    """Generate non-empty feedback descriptions."""
    return st.text(
        alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
        min_size=1,
        max_size=500,
    ).filter(lambda s: s.strip())


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def event_loop():
    """Create a module-scoped event loop for all async tests."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def client():
    """Async client for testing (module-scoped to share event loop)."""
    from src.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(scope="module")
async def auth_headers(client: AsyncClient):
    """Create a test user and return auth headers (module-scoped)."""
    username = f"pbt_user_{uuid.uuid4().hex[:8]}"
    reg_res = await client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "pass123"
    })
    
    if reg_res.status_code != 200:
        username = f"pbt_user_{uuid.uuid4().hex[:8]}"
        reg_res = await client.post("/api/v1/auth/register", json={
            "username": username, "email": f"{username}@test.com", "password": "pass123"
        })
    
    if reg_res.status_code != 200:
        pytest.skip(f"Failed to register: {reg_res.text}")
    
    user_body = reg_res.json()
    
    # Activate the user
    await _activate_user(user_body["id"])
    
    login_res = await client.post("/api/v1/auth/login", json={
        "username": username, "password": "pass123"
    })
    
    if login_res.status_code != 200:
        pytest.skip(f"Failed to login: {login_res.text}")
    
    data = login_res.json()
    if "access_token" not in data:
        pytest.skip(f"No access_token in response: {data}")
    
    token = data["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _activate_user(user_id: str) -> None:
    """Force a user into ``status='active'`` + ``is_active=True``."""
    from sqlalchemy import update

    from src.models.base import async_session_factory
    from src.models.user import User

    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.id == user_id)
            .values(status="active", is_active=True)
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Property-Based Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(scope="module")
@db_required
class TestImageCountInvariant:
    """Property-based tests for image count invariant.
    
    **Property 1: Image Count Invariant**
    *For any* feedback submission attempt, if the number of images exceeds 5,
    the system SHALL reject the submission and the feedback SHALL NOT be created.
    
    **Validates: Requirements 3.5, 3.6**
    """

    @hsettings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        images=image_list_exceeding_limit(),
        feedback_type=feedback_type_strategy(),
        title=feedback_title_strategy(),
        description=feedback_description_strategy(),
    )
    async def test_feedback_with_more_than_5_images_is_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
        images: list[str],
        feedback_type: str,
        title: str,
        description: str,
    ) -> None:
        """Property: Feedback with > 5 images is always rejected with 422.
        
        **Validates: Requirements 3.5, 3.6**
        
        For any feedback submission attempt with more than 5 images,
        the system SHALL reject the submission with HTTP 422 status code.
        """
        feedback_data = {
            "type": feedback_type,
            "title": title,
            "description": description,
            "images": images,
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        # The request should be rejected with 422 Unprocessable Entity
        assert res.status_code == 422, (
            f"Expected 422 for {len(images)} images, got {res.status_code}. "
            f"Response: {res.text}"
        )

    @hsettings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        images=image_list_within_limit(),
        feedback_type=feedback_type_strategy(),
        title=feedback_title_strategy(),
        description=feedback_description_strategy(),
    )
    async def test_feedback_with_5_or_fewer_images_is_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
        images: list[str],
        feedback_type: str,
        title: str,
        description: str,
    ) -> None:
        """Property: Feedback with <= 5 images is accepted.
        
        **Validates: Requirements 3.5, 3.6**
        
        For any feedback submission attempt with 5 or fewer images,
        the system SHALL accept the submission (assuming other fields are valid).
        """
        feedback_data = {
            "type": feedback_type,
            "title": title,
            "description": description,
            "images": images,
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        # The request should be accepted with 200 OK
        assert res.status_code == 200, (
            f"Expected 200 for {len(images)} images, got {res.status_code}. "
            f"Response: {res.text}"
        )
        
        # Verify the feedback was created with the correct images
        data = res.json()
        assert "id" in data
        assert data["images"] == images


# ---------------------------------------------------------------------------
# Boundary tests for image count limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(scope="module")
@db_required
class TestImageCountBoundary:
    """Boundary tests for the image count limit.
    
    **Validates: Requirements 3.5, 3.6**
    
    These tests verify the exact boundary behavior at 5 and 6 images.
    """

    async def test_exactly_5_images_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that exactly 5 images is accepted (boundary case)."""
        images = [f"/uploads/feedbacks/{uuid.uuid4().hex[:16]}.png" for _ in range(5)]
        
        feedback_data = {
            "type": "bug",
            "title": "Test with exactly 5 images",
            "description": "Testing the boundary at 5 images",
            "images": images,
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        assert res.status_code == 200, f"Expected 200 for 5 images, got {res.status_code}"
        assert res.json()["images"] == images

    async def test_exactly_6_images_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that exactly 6 images is rejected (boundary case)."""
        images = [f"/uploads/feedbacks/{uuid.uuid4().hex[:16]}.png" for _ in range(6)]
        
        feedback_data = {
            "type": "bug",
            "title": "Test with exactly 6 images",
            "description": "Testing the boundary at 6 images",
            "images": images,
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        assert res.status_code == 422, f"Expected 422 for 6 images, got {res.status_code}"

    async def test_zero_images_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that zero images is accepted."""
        feedback_data = {
            "type": "feature",
            "title": "Test with zero images",
            "description": "Testing with no images",
            "images": [],
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        assert res.status_code == 200, f"Expected 200 for 0 images, got {res.status_code}"
        assert res.json()["images"] == []

    async def test_large_number_of_images_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that a large number of images (100) is rejected."""
        images = [f"/uploads/feedbacks/{uuid.uuid4().hex[:16]}.png" for _ in range(100)]
        
        feedback_data = {
            "type": "bug",
            "title": "Test with 100 images",
            "description": "Testing with many images",
            "images": images,
        }
        
        res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        
        assert res.status_code == 422, f"Expected 422 for 100 images, got {res.status_code}"
