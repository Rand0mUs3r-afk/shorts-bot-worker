import os
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import clip_metadata as m


class Clock:
    def __init__(self):
        self.now = 0
    def monotonic(self):
        return self.now
    def sleep(self, delay):
        self.now += delay


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY': 'test', 'GROQ_API_KEY': 'test'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.timer = patch.object(m, 'time', self.clock)
        self.timer.start()
        self.addCleanup(self.timer.stop)
        self.sample = {'title': 'Video sample 01', 'reason': 'Automatically spaced sample.'}
        self.clean = {'title': 'The Gate Is Closing', 'description': 'The team races to reach the closing gate.'}

    def error(self, status, delay=''):
        exc = RuntimeError('PRIVATE')
        exc.code = status
        exc.response = types.SimpleNamespace(headers={'retry-after': delay})
        return exc

    def test_gemini_cooldown_does_not_block_groq_retries(self):
        row = {}
        with patch.object(m, 'gemini', side_effect=self.error(429, '3600')) as gemini, patch.object(m, 'groq', side_effect=[self.error(503), self.error(503), self.clean]) as groq:
            self.assertEqual(m.choose_metadata(self.sample, '', [b'frame'], row), self.clean)
        self.assertEqual(gemini.call_count, 1)
        self.assertEqual(groq.call_count, 3)
        self.assertEqual(self.clock.now, 15)

    def test_groq_cooldown_does_not_block_gemini_retries(self):
        with patch.object(m, 'gemini', side_effect=[self.error(503), self.error(503), self.clean]) as gemini, patch.object(m, 'groq', side_effect=self.error(429, '3600')) as groq:
            self.assertEqual(m.choose_metadata(self.sample, '', [b'frame']), self.clean)
        self.assertEqual(gemini.call_count, 3)
        self.assertEqual(groq.call_count, 1)

    def test_ordinary_errors_still_alternate_six_times(self):
        row = {}
        with patch.object(m, 'gemini', side_effect=self.error(503)), patch.object(m, 'groq', side_effect=self.error(503)):
            with self.assertRaises(ValueError):
                m.choose_metadata(self.sample, '', [b'frame'], row)
        self.assertEqual([item['provider'] for item in row['metadata_attempts']], ['gemini', 'groq'] * 3)
        self.assertEqual(self.clock.now, 15)

    def test_retry_after_is_respected_for_its_provider(self):
        with patch.object(m, 'gemini', side_effect=[self.error(429, '12'), self.clean]), patch.object(m, 'groq', side_effect=self.error(401)):
            self.assertEqual(m.choose_metadata(self.sample, '', [b'frame']), self.clean)
        self.assertEqual(self.clock.now, 12)

    def test_no_call_scheduled_past_budget(self):
        row = {}
        with patch.object(m, 'gemini', side_effect=self.error(429, '3600')), patch.object(m, 'groq', side_effect=self.error(429, '3600')):
            with self.assertRaises(ValueError):
                m.choose_metadata(self.sample, '', [b'frame'], row)
        self.assertEqual(len(row['metadata_attempts']), 2)
        self.assertEqual(self.clock.now, 0)

    def test_missing_headers_do_not_break_recovery(self):
        error = self.error(503)
        error.response.headers = None
        self.assertEqual(m.retry_delay(error), 0)

    def test_existing_descriptive_metadata_is_preserved(self):
        moment = {'title': self.clean['title'], 'reason': self.clean['description']}
        with patch.object(m, 'gemini', side_effect=self.error(401)), patch.object(m, 'groq', side_effect=self.error(401)):
            self.assertEqual(m.choose_metadata(moment, '', [b'frame']), self.clean)

    def test_workflow_passes_vision_model_variable(self):
        path = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'manual.yml'
        self.assertIn("GROQ_VISION_MODEL: ${{ vars.GROQ_VISION_MODEL || 'qwen/qwen3.8-27b' }}", path.read_text())
