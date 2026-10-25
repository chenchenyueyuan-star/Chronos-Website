import os
import unittest
from unittest.mock import patch
import psycopg
import chronos_store as store

class ConnectionErrorTests(unittest.TestCase):
    def test_specific_errors_never_echo_connection_secrets(self):
        cases = [('password authentication failed', 'rejected'),
                 ('could not translate host name', 'resolved'),
                 ('SSL error', 'secure'),
                 ('connection refused', 'refused'),
                 ('connection timeout expired', 'timed out'),
                 ('database example does not exist', 'database name')]
        with patch.dict(os.environ, {'TIMESCALE_SERVICE_URL':'postgres://example.invalid/db'}):
            for detail, expected in cases:
                with self.subTest(detail=detail):
                    result = store.safe_db_error(psycopg.OperationalError(detail+' SECRET_SENTINEL'))
                    self.assertIn(expected, result)
                    self.assertNotIn('SECRET_SENTINEL', result)
