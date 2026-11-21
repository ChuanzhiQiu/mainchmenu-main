"""
WSGI config for MainchApp project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/wsgi/
"""

import os
import logging
from django.core.wsgi import get_wsgi_application

logger = logging.getLogger(__name__)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'MainchApp.settings')

application = get_wsgi_application()

# Optional auto-migrate on boot (Supabase / Postgres).
# Disabled by default: on Vercel every cold start would run migrate, wasting
# the invocation budget and racing concurrent instances. Apply migrations once
# per release instead (`manage.py migrate` in the build step or a Procfile
# release command) and opt in here only when the platform has no release hook.
_auto_migrate = os.environ.get('AUTO_MIGRATE_ON_BOOT', '').lower() in ('1', 'true')
if _auto_migrate and (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')):
    try:
        from django.core.management import call_command
        logger.info("Executing pending migrations against cloud database...")
        call_command('migrate', interactive=False)
        logger.info("Cloud database migrations applied successfully.")
    except Exception as exc:
        logger.error("Auto-migration on startup failed: %s", exc)

app = application

