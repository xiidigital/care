web: CARE_PROCESS_ROLE=api gunicorn config.wsgi:application
release: python manage.py collectstatic --noinput && ./scripts/initialize.sh
