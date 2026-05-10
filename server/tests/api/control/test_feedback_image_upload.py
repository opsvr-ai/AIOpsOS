"""Unit tests for feedback image upload and retrieval endpoints.

Tests the POST /api/v1/feedbacks/images endpoint.
Tests the GET /api/v1/feedbacks/{id} and GET /api/v1/feedbacks endpoints for images.
Requirements: 5.1, 5.2, 5.3, 6.1
"""

import asyncio
import io
import socket

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


def _db_available():
    """Check if PostgreSQL is available via socket connection."""
    try:
        from src.config import settings
        # Parse host and port from database URL
        # Format: postgresql+asyncpg://user:pass@host:port/dbname
        url = settings.database_url
        # Extract host:port from URL
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

        # Try to connect via socket
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except Exception:
        return False


db_required = pytest.mark.skipif(not _db_available(), reason="PostgreSQL not available")

from src.main import app  # noqa: E402


# Use module-scoped event loop to avoid "attached to a different loop" errors
# when SQLAlchemy async engine connections span multiple tests.
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
    import uuid
    username = f"testuser_{uuid.uuid4().hex[:8]}"
    reg_res = await client.post("/api/v1/auth/register", json={
        "username": username, "email": f"{username}@test.com", "password": "pass123"
    })
    # Handle case where registration might fail (e.g., user already exists)
    if reg_res.status_code != 200:
        # Try with a different username
        username = f"testuser_{uuid.uuid4().hex[:8]}"
        reg_res = await client.post("/api/v1/auth/register", json={
            "username": username, "email": f"{username}@test.com", "password": "pass123"
        })
    
    if reg_res.status_code != 200:
        pytest.skip(f"Failed to register: {reg_res.text}")
    
    user_body = reg_res.json()
    
    # Activate the user (new registrations default to status="pending")
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


def create_test_image(size_bytes: int = 1024, ext: str = "png") -> tuple[bytes, str]:
    """Create a minimal valid image-like content for testing."""
    # Create a simple PNG header followed by padding
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
    return content, ext


@pytest.mark.asyncio(scope="module")
@db_required
class TestFeedbackImageUpload:
    """Tests for POST /api/v1/feedbacks/images endpoint."""

    async def test_upload_valid_png_image(self, client: AsyncClient, auth_headers: dict):
        """Test uploading a valid PNG image."""
        content, _ = create_test_image(1024, "png")
        files = {"file": ("screenshot.png", io.BytesIO(content), "image/png")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert "url" in data
        assert data["url"].startswith("/uploads/feedbacks/")
        assert data["url"].endswith(".png")
        assert data["filename"] == "screenshot.png"

    async def test_upload_valid_jpg_image(self, client: AsyncClient, auth_headers: dict):
        """Test uploading a valid JPG image."""
        content, _ = create_test_image(1024, "jpg")
        files = {"file": ("photo.jpg", io.BytesIO(content), "image/jpeg")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["url"].endswith(".jpg")
        assert data["filename"] == "photo.jpg"

    async def test_upload_valid_jpeg_image(self, client: AsyncClient, auth_headers: dict):
        """Test uploading a valid JPEG image."""
        content, _ = create_test_image(1024, "jpeg")
        files = {"file": ("photo.jpeg", io.BytesIO(content), "image/jpeg")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["url"].endswith(".jpeg")

    async def test_upload_valid_gif_image(self, client: AsyncClient, auth_headers: dict):
        """Test uploading a valid GIF image."""
        content, _ = create_test_image(1024, "gif")
        files = {"file": ("animation.gif", io.BytesIO(content), "image/gif")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["url"].endswith(".gif")

    async def test_upload_valid_webp_image(self, client: AsyncClient, auth_headers: dict):
        """Test uploading a valid WebP image."""
        content, _ = create_test_image(1024, "webp")
        files = {"file": ("image.webp", io.BytesIO(content), "image/webp")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 200
        data = res.json()
        assert data["url"].endswith(".webp")

    async def test_reject_invalid_file_type(self, client: AsyncClient, auth_headers: dict):
        """Test that invalid file types are rejected."""
        content = b"This is a text file"
        files = {"file": ("document.txt", io.BytesIO(content), "text/plain")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 400
        assert "Unsupported image type" in res.json()["detail"]

    async def test_reject_pdf_file(self, client: AsyncClient, auth_headers: dict):
        """Test that PDF files are rejected."""
        content = b"%PDF-1.4 fake pdf content"
        files = {"file": ("document.pdf", io.BytesIO(content), "application/pdf")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 400
        assert "Unsupported image type" in res.json()["detail"]

    async def test_reject_file_too_large(self, client: AsyncClient, auth_headers: dict):
        """Test that files larger than 5MB are rejected."""
        # Create a file slightly larger than 5MB
        content, _ = create_test_image(5 * 1024 * 1024 + 1, "png")
        files = {"file": ("large.png", io.BytesIO(content), "image/png")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )

        assert res.status_code == 413
        assert "too large" in res.json()["detail"].lower()

    async def test_reject_unauthenticated_request(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        content, _ = create_test_image(1024, "png")
        files = {"file": ("screenshot.png", io.BytesIO(content), "image/png")}

        res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
        )

        assert res.status_code == 401

    async def test_content_hash_deduplication(self, client: AsyncClient, auth_headers: dict):
        """Test that identical content produces the same filename (deduplication)."""
        content, _ = create_test_image(1024, "png")

        # Upload the same content twice with different original filenames
        files1 = {"file": ("screenshot1.png", io.BytesIO(content), "image/png")}
        res1 = await client.post(
            "/api/v1/feedbacks/images",
            files=files1,
            headers=auth_headers,
        )

        files2 = {"file": ("screenshot2.png", io.BytesIO(content), "image/png")}
        res2 = await client.post(
            "/api/v1/feedbacks/images",
            files=files2,
            headers=auth_headers,
        )

        assert res1.status_code == 200
        assert res2.status_code == 200

        # Same content should produce the same URL (content hash based)
        assert res1.json()["url"] == res2.json()["url"]

        # But original filenames should be preserved
        assert res1.json()["filename"] == "screenshot1.png"
        assert res2.json()["filename"] == "screenshot2.png"

    async def test_different_content_different_url(self, client: AsyncClient, auth_headers: dict):
        """Test that different content produces different URLs."""
        content1, _ = create_test_image(1024, "png")
        content2, _ = create_test_image(2048, "png")  # Different size = different content

        files1 = {"file": ("image1.png", io.BytesIO(content1), "image/png")}
        res1 = await client.post(
            "/api/v1/feedbacks/images",
            files=files1,
            headers=auth_headers,
        )

        files2 = {"file": ("image2.png", io.BytesIO(content2), "image/png")}
        res2 = await client.post(
            "/api/v1/feedbacks/images",
            files=files2,
            headers=auth_headers,
        )

        assert res1.status_code == 200
        assert res2.status_code == 200

        # Different content should produce different URLs
        assert res1.json()["url"] != res2.json()["url"]


@pytest.mark.asyncio(scope="module")
@db_required
class TestFeedbackRetrievalWithImages:
    """Tests for feedback retrieval endpoints returning images.

    Requirements: 6.1
    """

    async def test_get_feedback_returns_images(self, client: AsyncClient, auth_headers: dict):
        """Test that GET /api/v1/feedbacks/{id} returns images array."""
        # First upload an image
        content, _ = create_test_image(1024, "png")
        files = {"file": ("screenshot.png", io.BytesIO(content), "image/png")}
        upload_res = await client.post(
            "/api/v1/feedbacks/images",
            files=files,
            headers=auth_headers,
        )
        assert upload_res.status_code == 200
        image_url = upload_res.json()["url"]

        # Create feedback with the image
        feedback_data = {
            "type": "bug",
            "title": "Test bug with image",
            "description": "This is a test bug report with an attached image",
            "images": [image_url],
        }
        create_res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        assert create_res.status_code == 200
        feedback_id = create_res.json()["id"]

        # Retrieve the feedback and verify images are included
        get_res = await client.get(
            f"/api/v1/feedbacks/{feedback_id}",
            headers=auth_headers,
        )
        assert get_res.status_code == 200
        feedback = get_res.json()
        assert "images" in feedback
        assert feedback["images"] == [image_url]

    async def test_get_feedback_returns_empty_images_array(self, client: AsyncClient, auth_headers: dict):
        """Test that GET /api/v1/feedbacks/{id} returns empty images array when no images."""
        # Create feedback without images
        feedback_data = {
            "type": "feature",
            "title": "Test feature without image",
            "description": "This is a test feature request without images",
        }
        create_res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        assert create_res.status_code == 200
        feedback_id = create_res.json()["id"]

        # Retrieve the feedback and verify images is empty array
        get_res = await client.get(
            f"/api/v1/feedbacks/{feedback_id}",
            headers=auth_headers,
        )
        assert get_res.status_code == 200
        feedback = get_res.json()
        assert "images" in feedback
        assert feedback["images"] == []

    async def test_list_feedbacks_returns_images(self, client: AsyncClient, auth_headers: dict):
        """Test that GET /api/v1/feedbacks list endpoint returns images for each feedback."""
        # Upload two images
        content1, _ = create_test_image(1024, "png")
        content2, _ = create_test_image(2048, "jpg")

        files1 = {"file": ("img1.png", io.BytesIO(content1), "image/png")}
        upload_res1 = await client.post(
            "/api/v1/feedbacks/images",
            files=files1,
            headers=auth_headers,
        )
        image_url1 = upload_res1.json()["url"]

        files2 = {"file": ("img2.jpg", io.BytesIO(content2), "image/jpeg")}
        upload_res2 = await client.post(
            "/api/v1/feedbacks/images",
            files=files2,
            headers=auth_headers,
        )
        image_url2 = upload_res2.json()["url"]

        # Create feedback with multiple images
        import uuid
        unique_title = f"Test bug with multiple images {uuid.uuid4().hex[:8]}"
        feedback_data = {
            "type": "bug",
            "title": unique_title,
            "description": "This bug has multiple images",
            "images": [image_url1, image_url2],
        }
        create_res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        assert create_res.status_code == 200
        feedback_id = create_res.json()["id"]

        # List feedbacks and find our feedback
        list_res = await client.get(
            "/api/v1/feedbacks",
            headers=auth_headers,
        )
        assert list_res.status_code == 200
        feedbacks = list_res.json()

        # Find our feedback in the list
        our_feedback = next((f for f in feedbacks if f["id"] == feedback_id), None)
        assert our_feedback is not None
        assert "images" in our_feedback
        assert our_feedback["images"] == [image_url1, image_url2]

    async def test_get_feedback_preserves_image_order(self, client: AsyncClient, auth_headers: dict):
        """Test that images array order is preserved when retrieving feedback."""
        # Upload multiple images
        image_urls = []
        for i in range(3):
            content, _ = create_test_image(1024 + i * 100, "png")
            files = {"file": (f"img{i}.png", io.BytesIO(content), "image/png")}
            upload_res = await client.post(
                "/api/v1/feedbacks/images",
                files=files,
                headers=auth_headers,
            )
            assert upload_res.status_code == 200
            image_urls.append(upload_res.json()["url"])

        # Create feedback with images in specific order
        feedback_data = {
            "type": "bug",
            "title": "Test image order preservation",
            "description": "Testing that image order is preserved",
            "images": image_urls,
        }
        create_res = await client.post(
            "/api/v1/feedbacks",
            json=feedback_data,
            headers=auth_headers,
        )
        assert create_res.status_code == 200
        feedback_id = create_res.json()["id"]

        # Retrieve and verify order is preserved
        get_res = await client.get(
            f"/api/v1/feedbacks/{feedback_id}",
            headers=auth_headers,
        )
        assert get_res.status_code == 200
        feedback = get_res.json()
        assert feedback["images"] == image_urls
