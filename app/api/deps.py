"""Request dependencies: tenant from API key, idempotency key from header."""
from typing import Annotated

from fastapi import Depends, Header

from app.core.db import transaction
from app.core.errors import IdempotencyKeyMissing, Unauthorized
from app.core.models import Tenant
from app.core.security import hash_api_key
from app.repositories import tenants


def current_tenant(x_api_key: Annotated[str | None, Header()] = None) -> Tenant:
    if not x_api_key:
        raise Unauthorized("Missing API key. Send it in the X-API-Key header.")
    with transaction() as conn:
        tenant = tenants.get_by_key_hash(conn, hash_api_key(x_api_key))
    if tenant is None:
        raise Unauthorized("Unknown API key.")
    return tenant


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if not idempotency_key or not idempotency_key.strip():
        raise IdempotencyKeyMissing(
            "The Idempotency-Key header is required on POST /generate. "
            "Send a unique value (for example a UUID) and reuse it only when retrying."
        )
    if len(idempotency_key) > 255:
        raise IdempotencyKeyMissing("The Idempotency-Key header must be at most 255 characters.")
    return idempotency_key


CurrentTenant = Annotated[Tenant, Depends(current_tenant)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
