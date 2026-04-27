import pytest
from src.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    decode_refresh_token,
    hash_password,
    verify_password,
)


class TestPassword:
    def test_hash_and_verify(self):
        hashed = hash_password("secret123")
        assert hashed != "secret123"
        assert verify_password("secret123", hashed)
        assert not verify_password("wrong", hashed)

    def test_hash_is_stable(self):
        h1 = hash_password("test")
        h2 = hash_password("test")
        assert h1 != h2  # bcrypt salts are random


class TestAccessToken:
    def test_create_and_decode(self):
        token = create_access_token({"sub": "user-1", "username": "admin"})
        payload = decode_token(token)
        assert payload["sub"] == "user-1"
        assert payload["username"] == "admin"
        assert payload["type"] == "access"
        assert "exp" in payload

    def test_expired_token(self):
        from datetime import timedelta
        token = create_access_token({"sub": "u1"}, expires_delta=timedelta(seconds=-1))
        with pytest.raises(Exception):
            decode_token(token)


class TestRefreshToken:
    def test_create_and_decode(self):
        token = create_refresh_token({"sub": "user-1"})
        payload = decode_refresh_token(token)
        assert payload["sub"] == "user-1"
        assert payload["type"] == "refresh"
        assert "exp" in payload

    def test_rejects_access_token(self):
        access = create_access_token({"sub": "u1"})
        with pytest.raises(ValueError, match="Not a refresh token"):
            decode_refresh_token(access)

    def test_rejects_bad_token(self):
        with pytest.raises(Exception):
            decode_refresh_token("not.a.valid.token")
