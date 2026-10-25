import os
import unittest
from unittest.mock import patch
from deployment_config import load_database_secrets

class DeploymentConfigTests(unittest.TestCase):
    def test_top_level_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            load_database_secrets({'TIMESCALE_SERVICE_URL':' postgres://example '})
            self.assertEqual(os.environ['TIMESCALE_SERVICE_URL'],'postgres://example')
    def test_environment_wins(self):
        with patch.dict(os.environ, {'TIMESCALE_SERVICE_URL':'existing'}, clear=True):
            load_database_secrets({'TIMESCALE_SERVICE_URL':'other'})
            self.assertEqual(os.environ['TIMESCALE_SERVICE_URL'],'existing')
    def test_database_url_alias(self):
        with patch.dict(os.environ, {}, clear=True):
            load_database_secrets({'DATABASE_URL':'postgres://example'})
            self.assertEqual(os.environ['TIMESCALE_SERVICE_URL'],'postgres://example')
    def test_empty_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            load_database_secrets({'TIMESCALE_SERVICE_URL':' '})
            self.assertNotIn('TIMESCALE_SERVICE_URL',os.environ)
