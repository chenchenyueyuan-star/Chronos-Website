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

class DiagnosticTests(unittest.TestCase):
    def test_diagnostic_redacts_encoded_and_decoded_credentials(self):
        url = 'postgresql://exampleuser:p%40ssword@example.invalid:33070/exampledb?sslmode=require'
        error = psycopg.OperationalError('failed: '+url+' user exampleuser password=p@ssword host example.invalid database exampledb; server closed the connection unexpectedly')
        text = store.connection_diagnostic(error, url)
        for secret in ('p%40ssword','p@ssword','exampleuser','example.invalid','exampledb',url):
            self.assertNotIn(secret,text)
        self.assertIn('server closed the connection unexpectedly',text)

    def test_invalid_url_is_not_echoed(self):
        text = store.connection_diagnostic(Exception('super-secret'), 'psql "not-a-url-super-secret"')
        self.assertIn('Invalid connection URL',text)
        self.assertNotIn('super-secret',text)

    def test_only_connection_failures_are_wrapped(self):
        with patch.dict(os.environ, {'TIMESCALE_SERVICE_URL':'postgresql://u:password@example.invalid/db'}), patch('psycopg.connect', side_effect=psycopg.OperationalError('connection closed unexpectedly')):
            with self.assertRaises(store.DatabaseConnectionError) as caught:
                with store.connect():
                    self.fail('No connection should have been yielded')
            self.assertIn('connection diagnostic:',store.safe_db_error(caught.exception))
