"""Property-based test for image size validation (Property 2).

**Property 2: Image Size Validation**
*For any* image data with size greater than 5MB, the system SHALL reject the upload
and return an appropriate error message, regardless of the image format or content.

**Validates: Requirements 1.4, 2.5**

This test uses hypothesis to generate random image sizes above 5MB and verifies
they are rejected with 413 status code.
"""
from __future__ import annotations

import asyncio
import io
import socket
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from hypothesis import HealthCheck, given, settings as hsettings, strategies as st


# Maximum image size in bytes (5MB)
MAX_IMAGE_SIZE = 5 * 1024 * 1024


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
# Hypothesis strategies for image generation
# ---------------------------------------------------------------------------

def image_extension_strategy() -> st.SearchStrategy[str]:
    """Generate valid image extensions."""
    return st.sampled_from(["png", "jpg", "jpeg", "gif", "webp"])


def oversized_image_size_strategy() -> st.SearchStrategy[int]:
    """Generate image sizes that exceed the 5MB limit.
    
    Generates sizes from just over 5MB (5MB + 1 byte) up to 10MB.
    This tests the boundary and various sizes above the limit.
    """
    return st.integers(
        min_value=MAX_IMAGE_SIZE + 1,
        max_value=10 * 1024 * 1024,  # Up to 10MB
    )


def valid_image_size_strategy() -> st.SearchStrategy[int]:
    """Generate image sizes within the 5MB limit.
    
    Generates sizes from 100 bytes up to exactly 5MB.
    """
    return st.integers(
        min_value=100,
        max_value=MAX_IMAGE_SIZE,
    )


def create_test_image_content(size_bytes: int, ext: str) -> bytes:
    """Create test image content with appropriate header for the extension.
    
    Creates a minimal valid-looking image header followed by padding
    to reach the desired size.
    """
    # Create appropriate header based on extension
    if ext == "png":
        # PNG signature
        header = b'\x89PNG\r\n\x1a\n'
    elif ext in ("jpg", "jpeg"):
        # JPEG signature
        header = b'\xff\xd8\xff\xe0'
    elif ext == "gif":
        # GIF signature
        header = b'GIF89a'
    elif ext == "webp":
        # WebP signature
        header = b'RIFF\x00\x00\x00\x00WEBP'
    else:
        header = b''

    # Pad to desired size
    padding_size = max(0, size_bytes - len(header))
    content = header + b'\x00' * padding_size
    return content


def filename_strategy() -> st.SearchStrategy[str]:
    """Generate random filenames for images."""
    return st.builds(
        lambda name, ext: f"{name}.{ext}",
        st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
            min_size=1,
            max_size=20,
        ).filter(lambda s: s.strip()),
        image_extension_strategy(),
    )


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
    username = f"pbt_size_user_{uuid.uuid4().hex[:8]}"
    reg_res = await client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "pass123"
    })
    
    if reg_res.status_code != 200:
        username = f"pbt_size_user_{uuid.uuid4().hex[:8]}"
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
class TestImageSizeValidation:
    """Property-based tests for image size validation.
    
    **Property 2: Image Size Validation**
    *For any* image data with size greater than 5MB, the system SHALL reject
    the upload and return an appropriate error message, regardless of the
    image format or content.
    
    **Validates: Requirements 1.4, 2.5**
    """

    @hsettings(
        max_examples=100,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture, HealthCheck.large_base_example],
    )
    @given(
        size=oversized_image_size_strategy(),
        ext=image_extension_strategy(),
    )
    async def test_oversized_image_is_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
        size: int,
        ext: str,
    ) -> None:
        """Property: Any image > 5MB is rejected with 413 status.
        
        **Validates: Requirements 1.4, 2.5**
        
        For any image upload attempt with size greater than 5MB,
        the system SHALL reject the upload with HTTP 413 status code,
        regardless of the image format.
        """
        # Create test image content of the specified size
        content = create_test_image_content(size, ext)
        filename = f"test_image_{uuid.uuid4().hex[:8]}.{ext}"
        
        # Determine MIME type based on extension
        mime_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mime_type = mime_types.get(ext, "application/octet-stream")
        
        files = {"file": (filename, io.BytesIO(content), mime_type)}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        # The request should be rejected with 413 Payload Too Large
        assert res.status_code == 413, (
            f"Expected 413 for {size} bytes ({size / (1024*1024):.2f} MB) {ext} image, "
            f"got {res.status_code}. Response: {res.text}"
        )
        
        # Verify the error message mentions size limit
        response_detail = res.json().get("detail", "").lower()
        assert "too large" in response_detail or "5mb" in response_detail, (
            f"Expected error message about size limit, got: {res.json()}"
        )

    @hsettings(
        max_examples=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture, HealthCheck.large_base_example],
    )
    @given(
        size=valid_image_size_strategy(),
        ext=image_extension_strategy(),
    )
    async def test_valid_size_image_is_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
        size: int,
        ext: str,
    ) -> None:
        """Property: Any image <= 5MB with valid format is accepted.
        
        **Validates: Requirements 1.4, 2.5**
        
        For any image upload attempt with size <= 5MB and valid format,
        the system SHALL accept the upload with HTTP 200 status code.
        """
        # Create test image content of the specified size
        content = create_test_image_content(size, ext)
        filename = f"test_image_{uuid.uuid4().hex[:8]}.{ext}"
        
        # Determine MIME type based on extension
        mime_types = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mime_type = mime_types.get(ext, "application/octet-stream")
        
        files = {"file": (filename, io.BytesIO(content), mime_type)}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        # The request should be accepted with 200 OK
        assert res.status_code == 200, (
            f"Expected 200 for {size} bytes ({size / (1024*1024):.2f} MB) {ext} image, "
            f"got {res.status_code}. Response: {res.text}"
        )
        
        # Verify the response contains expected fields
        data = res.json()
        assert "url" in data
        assert data["url"].startswith("/uploads/feedbacks/")
        assert data["url"].endswith(f".{ext}")
        assert data["filename"] == filename


# ---------------------------------------------------------------------------
# Boundary tests for image size limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(scope="module")
@db_required
class TestImageSizeBoundary:
    """Boundary tests for the image size limit.
    
    **Validates: Requirements 1.4, 2.5**
    
    These tests verify the exact boundary behavior at 5MB.
    """

    async def test_exactly_5mb_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that exactly 5MB image is accepted (boundary case)."""
        content = create_test_image_content(MAX_IMAGE_SIZE, "png")
        filename = f"exactly_5mb_{uuid.uuid4().hex[:8]}.png"
        
        files = {"file": (filename, io.BytesIO(content), "image/png")}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        assert res.status_code == 200, (
            f"Expected 200 for exactly 5MB image, got {res.status_code}. "
            f"Response: {res.text}"
        )

    async def test_5mb_plus_1_byte_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that 5MB + 1 byte image is rejected (boundary case)."""
        content = create_test_image_content(MAX_IMAGE_SIZE + 1, "png")
        filename = f"over_5mb_{uuid.uuid4().hex[:8]}.png"
        
        files = {"file": (filename, io.BytesIO(content), "image/png")}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        assert res.status_code == 413, (
            f"Expected 413 for 5MB + 1 byte image, got {res.status_code}. "
            f"Response: {res.text}"
        )

    async def test_small_image_accepted(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that a small image (1KB) is accepted."""
        content = create_test_image_content(1024, "jpg")
        filename = f"small_image_{uuid.uuid4().hex[:8]}.jpg"
        
        files = {"file": (filename, io.BytesIO(content), "image/jpeg")}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        assert res.status_code == 200, (
            f"Expected 200 for 1KB image, got {res.status_code}. "
            f"Response: {res.text}"
        )

    async def test_large_oversized_image_rejected(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that a significantly oversized image (10MB) is rejected."""
        content = create_test_image_content(10 * 1024 * 1024, "gif")
        filename = f"large_image_{uuid.uuid4().hex[:8]}.gif"
        
        files = {"file": (filename, io.BytesIO(content), "image/gif")}
        
        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        
        assert res.status_code == 413, (
            f"Expected 413 for 10MB image, got {res.status_code}. "
            f"Response: {res.text}"
        )

    async def test_all_formats_rejected_when_oversized(
        self,
        client: AsyncClient,
        auth_headers: dict,
    ) -> None:
        """Test that all valid image formats are rejected when oversized."""
        formats = [
            ("png", "image/png"),
            ("jpg", "image/jpeg"),
            ("jpeg", "image/jpeg"),
            ("gif", "image/gif"),
            ("webp", "image/webp"),
        ]
        
        oversized = MAX_IMAGE_SIZE + 1024  # 5MB + 1KB
        
        for ext, mime_type in formats:
            content = create_test_image_content(oversized, ext)
            filename = f"oversized_{uuid.uuid4().hex[:8]}.{ext}"
            
            files = {"file": (filename, io.BytesIO(content), mime_type)}
            
            res = await client.post(
                "/api/v1/feedbacks/images",
                files=files,
                headers=auth_headers,
            )
            
            assert res.status_code == 413, (
                f"Expected 413 for oversized {ext} image, got {res.status_code}. "
                f"Response: {res.text}"
            )
