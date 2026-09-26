"""
Defensive Tenant Scoping Managers and QuerySets for Django Models.
Automatically applies tenant filtering when context is active, while providing
safe `.unscoped()` and `.for_tenant(tenant)` bypasses.
"""
from django.db import models
from Menu.tenant_context import get_current_tenant


class TenantQuerySet(models.QuerySet):
    """Custom QuerySet supporting tenant filtering, manual scoping, and clean un-scoping."""

    def for_tenant(self, tenant):
        """Scopes the queryset explicitly to the given tenant instance or ID."""
        if tenant is not None:
            if hasattr(tenant, "pk"):
                return self.filter(restaurante=tenant)
            return self.filter(restaurante_id=tenant)
        return self

    def unscoped(self):
        """
        Bypasses tenant scoping cleanly by returning an unfiltered QuerySet from all_objects.
        Avoids mutating or attempting to strip WHERE clauses from an existing QuerySet AST.
        """
        return self.model.all_objects.all()


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """
    Default manager for multi-tenant models.
    Automatically scopes queries to `current_tenant` when an active tenant is set in contextvars.
    When current_tenant is None (CLI, migrations, unauthenticated tests), returns unfiltered query.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        tenant = get_current_tenant()
        if tenant is not None:
            return qs.filter(restaurante=tenant)
        return qs

    def unscoped(self):
        """Direct access to all records across all tenants."""
        return self.model.all_objects.all()

    def for_tenant(self, tenant):
        """Direct access to records for an explicit tenant."""
        if hasattr(tenant, "pk"):
            return self.model.all_objects.filter(restaurante=tenant)
        return self.model.all_objects.filter(restaurante_id=tenant)
