"""
Thread-safe and async-safe tenant context management using contextvars.
Compatible with synchronous WSGI (Gunicorn), Serverless WSGI (Vercel), and ASGI runtimes.
"""
import contextvars
from contextlib import contextmanager
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from Menu.models import Restaurante

# Context variable holding the currently active Restaurante instance or None
_current_tenant_var: contextvars.ContextVar[Optional["Restaurante"]] = contextvars.ContextVar(
    "current_tenant", default=None
)


def get_current_tenant() -> Optional["Restaurante"]:
    """Returns the currently active Restaurante instance in the execution context, or None."""
    return _current_tenant_var.get()


def set_current_tenant(tenant: Optional["Restaurante"]) -> contextvars.Token:
    """Sets the active tenant for the current execution context and returns a reset Token."""
    return _current_tenant_var.set(tenant)


def reset_current_tenant(token: contextvars.Token) -> None:
    """Resets the tenant context back to its previous state using the provided token."""
    if token is not None:
        _current_tenant_var.reset(token)


@contextmanager
def tenant_context(tenant: Optional["Restaurante"]):
    """
    Context manager for safely executing blocks within a specific tenant context.
    Ensures guaranteed cleanup upon exit or exception.
    
    Usage:
        with tenant_context(my_tenant):
            platos = Plato.objects.all()  # Automatically scoped to my_tenant
    """
    token = set_current_tenant(tenant)
    try:
        yield tenant
    finally:
        reset_current_tenant(token)
