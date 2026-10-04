"""API-key generation and hashing."""
import hashlib
import secrets

API_KEY_PREFIX = "mk_test_"


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
