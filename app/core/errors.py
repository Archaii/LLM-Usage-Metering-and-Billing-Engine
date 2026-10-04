"""Domain errors. Services raise these; app/api/errors.py maps them to HTTP statuses."""


class DomainError(Exception):
    code = "internal_error"

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class Unauthorized(DomainError):
    code = "unauthorized"


class IdempotencyKeyMissing(DomainError):
    code = "idempotency_key_missing"


class IdempotencyKeyReused(DomainError):
    code = "idempotency_key_reused"


class PaymentRequired(DomainError):
    code = "payment_required"


class UpgradeRequired(DomainError):
    code = "upgrade_required"

    def __init__(self, message: str, details: dict | None = None, upgrade_url: str = "/billing/checkout"):
        super().__init__(message, details)
        self.upgrade_url = upgrade_url


class QuotaExceeded(DomainError):
    code = "quota_exceeded"

    def __init__(self, message: str, details: dict | None = None, retry_after: int = 1):
        super().__init__(message, details)
        self.retry_after = retry_after


class BillingProviderError(DomainError):
    code = "billing_provider_error"


class InvalidSignature(DomainError):
    code = "invalid_signature"


class InvalidPayload(DomainError):
    code = "invalid_payload"


class PaymentNotSettled(DomainError):
    """The webhook arrived before PayMongo finished the payment. The worker retries."""

    code = "payment_not_settled"
