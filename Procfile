web: gunicorn MainchApp.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
release: python manage.py migrate --noinput
