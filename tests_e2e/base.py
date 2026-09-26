"""
Base classes and test infrastructure utilities for MainchApp E2E testing suite.
Provides opaque-box test helpers, dynamic model/service getters for progressive testability,
and assertions for requirement compliance.
"""

import os
import sys
from pathlib import Path
import unittest
import importlib
from decimal import Decimal

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = PROJECT_ROOT.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MainchApp.settings')

import django
try:
    django.setup()
except Exception:
    pass

from django.test import TestCase, SimpleTestCase, Client
from django.apps import apps
from django.conf import settings


class E2EBaseMixin:
    """Shared utilities for all E2E test cases."""
    PROJECT_ROOT = PROJECT_ROOT
    WORKSPACE_ROOT = WORKSPACE_ROOT

    def get_client(self):
        client = Client()
        from django.contrib.auth.models import User
        try:
            admin_user, _ = User.objects.get_or_create(
                username="e2e_admin",
                defaults={"is_staff": True, "is_superuser": True}
            )
            if not admin_user.is_staff:
                admin_user.is_staff = True
                admin_user.save()
            client.force_login(admin_user)
        except Exception:
            pass
        return client

    def require_model(self, app_label, model_name, feature_id=""):
        """Dynamically get model from Django app registry or fail test cleanly."""
        try:
            model = apps.get_model(app_label, model_name)
            if model is not None:
                return model
        except (LookupError, Exception):
            pass
        prefix = f"[{feature_id}] " if feature_id else ""
        self.fail(f"{prefix}Required model '{app_label}.{model_name}' is not implemented in Django apps registry.")

    def require_service(self, module_path, func_name, feature_id=""):
        """Dynamically import a service function or class or fail test cleanly."""
        prefix = f"[{feature_id}] " if feature_id else ""
        try:
            mod = importlib.import_module(module_path)
            if hasattr(mod, func_name):
                return getattr(mod, func_name)
            self.fail(f"{prefix}Module '{module_path}' exists, but function/class '{func_name}' was not found.")
        except ImportError as e:
            self.fail(f"{prefix}Required service module '{module_path}' could not be imported: {e}")

    def require_file(self, relative_path, base_dir=None, feature_id=""):
        """Verify existence of a project file or fail test cleanly."""
        base = base_dir or self.PROJECT_ROOT
        path = base / relative_path
        prefix = f"[{feature_id}] " if feature_id else ""
        self.assertTrue(path.exists(), f"{prefix}Expected file '{relative_path}' at {path} does not exist.")
        return path

    def assert_model_has_field(self, model_cls, field_name, expected_type=None):
        """Assert that a model contains a given field, optionally checking field class name."""
        field_names = [f.name for f in model_cls._meta.get_fields()]
        self.assertIn(
            field_name,
            field_names,
            f"Model {model_cls.__name__} is missing field '{field_name}'. Found fields: {field_names}"
        )
        if expected_type:
            field = model_cls._meta.get_field(field_name)
            self.assertEqual(
                field.get_internal_type(),
                expected_type,
                f"Field '{field_name}' on {model_cls.__name__} should be {expected_type}, but got {field.get_internal_type()}"
            )


class E2ESimpleTestCase(SimpleTestCase, E2EBaseMixin):
    """Test case for environment, settings, templates, and static asset verification without DB."""
    pass


class E2ETestCase(TestCase, E2EBaseMixin):
    """Test case for database, models, transactional flows, and live API endpoints."""
    pass
