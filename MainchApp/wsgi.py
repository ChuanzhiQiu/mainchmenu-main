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

# Auto-migrate on cloud serverless startup (Supabase / Postgres)
if os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL'):
    try:
        from django.core.management import call_command
        logger.info("Executing pending migrations against cloud database...")
        call_command('migrate', interactive=False)
        logger.info("Cloud database migrations applied successfully.")
    except Exception as exc:
        logger.error("Auto-migration on startup failed: %s", exc)

app = application

