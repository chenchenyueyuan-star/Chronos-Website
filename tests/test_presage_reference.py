import hashlib
import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class ReferenceLogicTests(unittest.TestCase):
    def test_reference_lifecycle_matches_with_input_selection_adapter(self):
        root=ROOT/'monitoring'
        manifest=json.loads((root/'reference_manifest.json').read_text())
        core=(root/'presage_core.js').read_text()
        for section in manifest['sections']:
            with self.subTest(section=section['name']):
                chunk=core[core.index(section['start']):core.index(section['end'])]
                if section.get('normalize_input_selection'):
                    chunk=chunk.replace('selectInput();', 'sdk.useCustomInput();')
                self.assertEqual(hashlib.sha256(chunk.encode()).hexdigest(),section['sha256'])
        self.assertIn('requestedMetrics: requestedMetrics ?? [...breathingMetrics, ...cardioMetrics, ...faceMetrics]',core)
        capture=(root/'capture.js').read_text()
        self.assertIn('setInterval(captureFrame,1000 / 30)',capture)
        self.assertIn('if(activeFrameRequest)try{await activeFrameRequest;}',capture)
