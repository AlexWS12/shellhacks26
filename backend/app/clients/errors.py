# Every provider failure becomes one of these, so the registry applies one fallback policy to all of them.
# Messages are redacted when the error is made, so no key can ride along into a log, an event or the UI.

from typing import Any

from app.clients.redact import redact


class ModelError(Exception):
    def __init__(self, message: str, provider: str = "", model: str = "", retry_after: float | None = None) -> None:
        self.message = redact(message)[:300]
        self.provider, self.model, self.retry_after = provider, model, retry_after
        super().__init__(self.message)

    @property
    def error_class(self) -> str:
        return type(self).__name__


class AuthError(ModelError):
    """Missing or invalid key, 401/403."""


class ModelNotFound(ModelError):
    """Unknown or deprecated model name."""


class RateLimited(ModelError):
    """429: too many requests right now."""


class QuotaExceeded(ModelError):
    """The key's quota or credits are used up (daily limit, billing)."""


class Timeout(ModelError):
    """No answer in time."""


class BadResponse(ModelError):
    """Invalid JSON, a failed schema check, or a request the model rejected."""


class ProviderUnavailable(ModelError):
    """5xx, overload or a network failure."""


class RoleExhausted(Exception):
    # Every model configured for a role failed or was skipped. attempts: one dict per try, oldest first.
    def __init__(self, role: str, attempts: list[dict[str, Any]]) -> None:
        self.role, self.attempts = role, attempts
        tried = "; ".join(f"{a['provider']}/{a['model']}: {a['error_class']}" for a in attempts) or "no model configured"
        super().__init__(redact(f"every model for '{role}' failed ({tried})"))


def describe(e: BaseException) -> str:
    # Short, key-free text for logs, events and the UI.
    if isinstance(e, ModelError):
        return f"{e.error_class}" + (f" ({e.provider}/{e.model})" if e.model else "") + (f": {e.message}" if e.message else "")
    return redact(f"{type(e).__name__}: {e}")[:300]
