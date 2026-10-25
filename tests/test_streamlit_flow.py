import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest
import audio_generation as audio

HERE = Path(__file__).resolve().parents[1]


class StreamlitFlowTests(unittest.TestCase):
    @patch("dotenv.load_dotenv", return_value=False)
    def test_generation_and_reruns(self, _load_dotenv):
        intro = json.loads((HERE / 'data' / 'intro questionnaire.json').read_text())
        app = AppTest.from_file(str(HERE / 'app.py')).run()
        app.session_state['signed_up'] = True
        app.session_state['guest'] = True
        app.session_state['username'] = 'Test guest'
        app.session_state['questionnaire_done'] = True
        app.session_state['answers'] = [
            {'Question': q['text'], 'Answer': q['options']['A']['text'],
             'field': q['field'], 'value': q['options']['A']['value']}
            for q in intro['items']
        ]
        app.run()
        self.assertFalse(app.exception)
        for _ in range(8):
            app.button(key='session_answer_a').click().run()
            self.assertFalse(app.exception)
        self.assertFalse(app.slider)
        self.assertFalse(app.radio)
        next(b for b in app.button if b.label == 'Prepare audio plan').click().run()
        self.assertFalse(app.exception)
        self.assertNotIn('Review session choices', {e.label for e in app.expander})
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'ELEVENLABS_API_KEY': ''}):
            app.button(key='generate_session').click().run()
            self.assertTrue(app.error)
        script = {'title': 'Test', 'paragraphs': ['Quiet.'], 'nature_prompt': 'Rain', 'asmr_prompt': 'Brush'}
        with tempfile.TemporaryDirectory() as tmp:
            # The HTML only embeds bytes; actual decoding is covered by audio tests.
            path = Path(tmp) / 'voice.mp3'
            path.write_bytes(b'test-mp3-placeholder')
            session = {'title': 'Test', 'voice': str(path), 'background': None,
                       'duration_seconds': 120, 'script': 'Quiet.'}
            with patch.dict(os.environ, {'GEMINI_API_KEY': 'test', 'ELEVENLABS_API_KEY': 'test',
                                         'ELEVENLABS_MALE_VOICE_1_ID': 'male-1',
                                         'ELEVENLABS_MALE_VOICE_2_ID': 'male-2',
                                         'ELEVENLABS_FEMALE_VOICE_1_ID': 'female-1',
                                         'ELEVENLABS_FEMALE_VOICE_2_ID': 'female-2',
                                         'ELEVENLABS_MALE_VOICE_1_WHISPER_ID': 'male-1-whisper',
                                         'ELEVENLABS_MALE_VOICE_2_WHISPER_ID': 'male-2-whisper',
                                         'ELEVENLABS_FEMALE_VOICE_1_WHISPER_ID': 'female-1-whisper',
                                         'ELEVENLABS_FEMALE_VOICE_2_WHISPER_ID': 'female-2-whisper',
                                         'ELEVENLABS_VOICE_ID': 'must-not-be-used'}), \
                 patch.object(audio, 'generate_script', return_value=script) as gemini, \
                 patch.object(audio, 'render_tracks', return_value=(None, None)) as eleven, \
                 patch.object(audio, 'save_session', return_value=session):
                app.button(key='generate_session').click().run()
                self.assertFalse(app.exception)
                self.assertIn('generated_session', app.session_state)
                self.assertTrue(app.get('iframe'))
                visible = ' '.join(x.value for x in app.markdown)
                self.assertNotIn('Using your prepared script', visible)
                self.assertNotIn('Creating voice section', visible)
                self.assertFalse(app.get('status'))
                app.button(key='theme_🌌').click().run()
                self.assertFalse(app.exception)
                self.assertEqual(gemini.call_count, 1)
                self.assertEqual(eleven.call_count, 1)
                self.assertEqual(eleven.call_args.args[2], 'male-1-whisper')
                for label, expected in [('Male voice 2', 'male-2'), ('Female voice 1', 'female-1'), ('Female voice 2', 'female-2')]:
                    app.selectbox(key='selected_narrator').set_value(label).run()
                    self.assertEqual(app.session_state['audio_plan']['voice_choice'], label)
                    self.assertNotIn('generated_session', app.session_state)
                    app.button(key='generate_session').click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(eleven.call_args.args[2], expected + '-whisper')
                previous_calls = eleven.call_count
                with patch.dict(os.environ, {'ELEVENLABS_FEMALE_VOICE_2_WHISPER_ID': ''}):
                    app.button(key='generate_session').click().run()
                    self.assertIn('ELEVENLABS_FEMALE_VOICE_2_WHISPER_ID', app.error[0].value)
                    self.assertEqual(eleven.call_count, previous_calls)
                # Turning ASMR off must select each normal voice instead.
                next(b for b in app.button if b.label == '← Edit session answers').click().run()
                for _ in range(6):
                    next(b for b in app.button if b.label == '← Previous session question').click().run()
                app.button(key='session_answer_b').click().run()
                for _ in range(6):
                    app.button(key='session_answer_a').click().run()
                next(b for b in app.button if b.label == 'Prepare audio plan').click().run()
                for label, expected in [('Male voice 1', 'male-1'), ('Male voice 2', 'male-2'), ('Female voice 1', 'female-1'), ('Female voice 2', 'female-2')]:
                    app.selectbox(key='selected_narrator').set_value(label).run()
                    app.button(key='generate_session').click().run()
                    self.assertFalse(app.exception)
                    self.assertEqual(eleven.call_args.args[2], expected)
                    self.assertFalse(eleven.call_args.args[0]['session_choices']['asmr_enabled'])
                next(b for b in app.button if b.label == 'Edit my preferences').click().run()
                self.assertNotIn('generated_session', app.session_state)


if __name__ == '__main__':
    unittest.main()
