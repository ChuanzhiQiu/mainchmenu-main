import logging

from django.apps import AppConfig
from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)


def _configure_sqlite(sender, connection, **kwargs):
    """Tune every new SQLite connection for a read-heavy POS workload.

    WAL lets readers proceed while a writer holds the lock (critical when the
    caja writes orders while the kitchen polls), and a 20s busy timeout turns
    lock contention into a short wait instead of an immediate "database is
    locked" error. In-memory databases used by tests are left untouched beyond
    the harmless pragmas (WAL is a no-op there).
    """
    if connection.vendor != 'sqlite':
        return

    try:
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA temp_store=MEMORY;')
            cursor.execute('PRAGMA cache_size=-16000;')
            cursor.execute('PRAGMA busy_timeout=20000;')

            name = str(connection.settings_dict.get('NAME', ''))
            is_memory = name == ':memory:' or 'mode=memory' in name
            if not is_memory:
                cursor.execute('PRAGMA journal_mode=WAL;')
    except Exception as exc:  # pragma: no cover - diagnostics only
        logger.warning('Could not apply SQLite pragmas: %s', exc)


class MenuConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'Menu'

    def ready(self):
        connection_created.connect(
            _configure_sqlite,
            dispatch_uid='menu_sqlite_pragmas',
        )
