import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio_generation as audio
from audio_player import player_html

HERE = Path(__file__).resolve().parents[1]
INTRO = json.loads((HERE / 'data' / 'intro questionnaire.json').read_text())


def plan(narration=True, nature=True, asmr=True):
    return {
        'duration_minutes': 2, 'voice_choice': 'Male voice 1',
        'onboarding_preferences': {'asmr_character': 'flowing', 'nature_environment_family': 'water',
                                   'narration_density': 'sparse', 'language_style': 'plain'},
        'session_choices': {'goal': 'sleep', 'narration_enabled': narration,
                            'nature_enabled': nature, 'asmr_enabled': asmr,
                            'body_awareness_allowed': False, 'muscle_tensing_allowed': True,
                            'breathing_allowed': True, 'imagery_allowed': False},
    }


class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.segment = audio.load_audio_tools()
        from pydub.generators import Sine
        cls.tone = Sine(220).to_audio_segment(duration=500).set_frame_rate(22050)
        data = io.BytesIO()
        cls.tone.export(data, format='mp3', bitrate='64k')
        cls.mp3 = data.getvalue()

    def test_session_permissions_and_no_layers(self):
        original = plan(narration=False)
        cleaned = audio.clean_plan(original)
        self.assertIsNone(cleaned['voice_choice'])
        self.assertFalse(cleaned['session_choices']['breathing_allowed'])
        self.assertFalse(cleaned['session_choices']['muscle_tensing_allowed'])
        self.assertTrue(original['session_choices']['breathing_allowed'])
        with self.assertRaises(audio.GenerationError):
            audio.clean_plan(plan(False, False, False))

    def test_prompt_length_and_inactive_preferences(self):
        prompt, schema, target = audio.script_request(plan(False, True, False), INTRO)
        self.assertEqual(target, 0)
        self.assertIn('water', prompt)
        self.assertNotIn('"asmr_character": "flowing"', prompt)
        self.assertIn('paragraphs', schema['required'])
        self.assertEqual(audio.script_request(plan(), INTRO)[2], 180)

    def test_short_script_retries_before_accepting_full_minute_sections(self):
        short = {'title': 'Calm', 'paragraphs': ['Too short.'],
                 'nature_prompt': 'Rain', 'asmr_prompt': 'Soft textures'}
        complete = dict(short, paragraphs=[' '.join(['word'] * 90)] * 2)
        responses = [SimpleNamespace(output_text=json.dumps(value)) for value in [short, complete]]
        with patch('google.genai.Client') as client:
            create = client.return_value.__enter__.return_value.interactions.create
            create.side_effect = responses
            result = audio.generate_script(plan(), INTRO, 'fake-key')
        self.assertEqual(create.call_count, 2)
        self.assertEqual([len(p.split()) for p in result['paragraphs']], [90, 90])
        self.assertIn('Length correction', create.call_args.kwargs['input'])

    def test_five_minute_463_word_script_is_accepted_without_retry(self):
        current = plan()
        current['duration_minutes'] = 5
        current['onboarding_preferences']['narration_density'] = 'frequent'
        result = {'title': 'Calm',
                  'paragraphs': [' '.join(['word'] * n) for n in [80, 90, 95, 98, 100]],
                  'nature_prompt': 'Rain', 'asmr_prompt': 'Soft textures'}
        with patch('google.genai.Client') as client:
            create = client.return_value.__enter__.return_value.interactions.create
            create.return_value = SimpleNamespace(output_text=json.dumps(result))
            accepted = audio.generate_script(current, INTRO, 'fake-key')
            self.assertEqual(create.call_count, 1)
        self.assertEqual(sum(len(p.split()) for p in accepted['paragraphs']), 463)

    def test_repeated_short_scripts_are_rejected(self):
        short = {'title': 'Calm', 'paragraphs': ['Too short.'],
                 'nature_prompt': 'Rain', 'asmr_prompt': 'Soft textures'}
        with patch('google.genai.Client') as client:
            create = client.return_value.__enter__.return_value.interactions.create
            create.return_value = SimpleNamespace(output_text=json.dumps(short))
            with self.assertRaisesRegex(audio.GenerationError, 'after 3 attempts'):
                audio.generate_script(plan(), INTRO, 'fake-key')
            self.assertEqual(create.call_count, 3)

    def test_one_minute_session_and_frequent_budget(self):
        one = plan()
        one['duration_minutes'] = 1
        one['onboarding_preferences']['narration_density'] = 'frequent'
        self.assertEqual(audio.clean_plan(one)['duration_minutes'], 1)
        self.assertEqual(audio.script_request(one, INTRO)[2], 105)

    def test_provider_errors_are_specific_and_do_not_expose_keys(self):
        class ProviderError(Exception):
            status_code = 400
        message = audio.gemini_error_message(ProviderError('API key not valid. API_KEY_INVALID secret-test-key'))
        self.assertIn('Google rejected', message)
        self.assertNotIn('secret-test-key', message)
        error = ProviderError('Quota reached')
        error.status_code = 429
        self.assertIn('quota', audio.gemini_error_message(error).lower())
        error.status_code = 404
        self.assertIn('model', audio.gemini_error_message(error).lower())

    def test_missing_gemini_package_has_actionable_error(self):
        import builtins
        original_import = builtins.__import__
        def missing_package(name, *args, **kwargs):
            if name == 'google':
                raise ModuleNotFoundError("No module named 'google'")
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=missing_package):
            with self.assertRaisesRegex(audio.GenerationError, 'Gemini package is missing'):
                audio.generate_script(plan(), INTRO, 'fake-key')

    def test_gemini_contract_and_disabled_output_filtering(self):
        generated = {'title': 'Calm', 'paragraphs': ['This must not be spoken.'],
                     'nature_prompt': 'Gentle rain', 'asmr_prompt': 'Forbidden layer'}
        fake = SimpleNamespace(interactions=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(output_text=json.dumps(generated))))
        with patch('google.genai.Client') as client:
            client.return_value.__enter__.return_value = fake
            result = audio.generate_script(plan(False, True, False), INTRO, 'fake-key')
        self.assertEqual(result['paragraphs'], [])
        self.assertEqual(result['asmr_prompt'], '')

    def test_decode_and_timing_without_truncating_speech(self):
        decoded = audio.decode_audio(self.mp3, self.segment)
        self.assertGreater(len(decoded), 450)
        timeline = audio.fit_narration([decoded, decoded], 120000, self.segment)
        self.assertEqual(len(timeline), 120000)
        self.assertGreater(timeline[:300].rms, 0)
        with self.assertRaises(audio.GenerationError):
            audio.fit_narration([decoded], 100, self.segment)
        self.assertEqual(len(audio.loop_sound(decoded, 120000)), 120000)

    def test_only_enabled_provider_layers_and_saved_outputs(self):
        script = {'title': 'Test session', 'paragraphs': ['A quiet moment.'],
                  'nature_prompt': 'Rain', 'asmr_prompt': 'Brushing'}
        for narration, nature, asmr in [(True, True, True), (False, True, False), (True, False, False), (False, False, True)]:
            with self.subTest(narration=narration, nature=nature, asmr=asmr):
                with patch.object(audio, 'eleven_request', return_value=self.mp3) as provider:
                    voice, background = audio.render_tracks(plan(narration, nature, asmr), script, 'voice-id', 'fake-key')
                endpoints = [call.args[0] for call in provider.call_args_list]
                self.assertEqual(sum(e.startswith('text-to-speech/') for e in endpoints), int(narration))
                self.assertEqual(sum(e.startswith('sound-generation') for e in endpoints), int(nature) + int(asmr))
                self.assertEqual(voice is not None, narration)
                self.assertEqual(background is not None, nature or asmr)
                if voice is not None: self.assertEqual(len(voice), 120000)
                if background is not None: self.assertEqual(len(background), 120000)
                with tempfile.TemporaryDirectory() as tmp:
                    session = audio.save_session(Path(tmp) / 'session', plan(narration, nature, asmr), script, voice, background)
                    html = player_html(session)
                    self.assertIn('Voice <span', html)
                    self.assertIn('Background <span', html)
                    self.assertNotIn('type="range"', html)
                    self.assertIn('Promise.all(tracks.map', html)
                    self.assertEqual(session['duration_seconds'], 120)
                    if voice is not None:
                        measured = audio.decode_audio(Path(session['voice']).read_bytes(), self.segment)
                        self.assertLess(abs(len(measured) - 120000), 100)

    def test_selected_background_requests_overlap_narration_without_extra_api_calls(self):
        import threading
        overlap = threading.Event()
        lock = threading.Lock()
        calls = []
        active = 0
        maximum = 0
        def provider(endpoint, payload, key):
            nonlocal active, maximum
            with lock:
                calls.append(endpoint)
                active += 1
                maximum = max(maximum, active)
                if active >= 2:
                    overlap.set()
            if not overlap.wait(timeout=3):
                raise AssertionError('Independent requests did not overlap')
            with lock:
                active -= 1
            return self.mp3
        script = {'title': 'Test', 'paragraphs': ['Quiet.'], 'nature_prompt': 'Rain', 'asmr_prompt': 'Soft textures'}
        current = plan(); current['duration_minutes'] = 1
        with patch.dict(os.environ, {'ELEVENLABS_CONCURRENT_REQUESTS': '2'}), patch.object(audio, 'eleven_request', side_effect=provider):
            voice, background = audio.render_tracks(current, script, 'voice-id', 'fake-key')
        self.assertEqual(len(calls), 3)
        self.assertEqual(maximum, 2)
        self.assertIsNotNone(voice)
        self.assertIsNotNone(background)

    def test_chunking_preserves_all_words(self):
        paragraphs = ['A sentence with enough words. ' * 200]
        chunks = audio.speech_chunks(paragraphs)
        self.assertTrue(all(len(chunk) <= 2400 for chunk in chunks))
        self.assertEqual(' '.join(chunks).split(), ' '.join(paragraphs).split())


if __name__ == '__main__':
    unittest.main()
