"""Load deployment secrets before importing database helpers.

Existing environment variables take priority; no secret values are logged.
"""
import os

KEYS = ('TIMESCALE_SERVICE_URL', 'DATABASE_URL')

def load_database_secrets(secrets):
    # With either environment variable set, avoid requiring a secrets file.
    if not any(os.environ.get(key) for key in KEYS):
        for key in KEYS:
            value = secrets.get(key)
            if value is not None and str(value).strip():
                os.environ[key] = str(value).strip()
                break
    if not os.environ.get('TIMESCALE_SERVICE_URL') and os.environ.get('DATABASE_URL'):
        os.environ['TIMESCALE_SERVICE_URL'] = os.environ['DATABASE_URL']
