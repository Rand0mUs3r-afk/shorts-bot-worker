import json
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch
import clip_metadata as m


class VisualFallbackTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'GEMINI_API_KEY': 'test', 'GROQ_API_KEY': 'test'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sleep = patch.object(m.time, 'sleep')
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.sample = {'title': 'Video sample 01', 'reason': 'Automatically spaced sample.'}
        self.clean = {'title': 'The Gate Is Closing', 'description': 'The team races to reach the closing gate.'}

    def test_gemini_429_uses_visual_groq_without_speech(self):
        error = RuntimeError('SECRET')
        error.code = 429
        row = {}
        with patch.object(m, 'gemini', side_effect=error), patch.object(m, 'groq', return_value=self.clean) as groq:
            self.assertEqual(m.choose_metadata(self.sample, '', [b'frame'], row), self.clean)
        self.assertEqual(groq.call_args.args[1], [b'frame'])
        self.assertEqual(row['metadata_method'], 'groq')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_six_attempts_then_no_fabricated_metadata(self):
        error = RuntimeError('SECRET')
        error.code = 503
        row = {}
        with patch.object(m, 'gemini', side_effect=error), patch.object(m, 'groq', side_effect=error):
            with self.assertRaises(ValueError):
                m.choose_metadata(self.sample, '', [b'frame'], row)
        self.assertEqual([item['provider'] for item in row['metadata_attempts']], ['gemini', 'groq'] * 3)

    def test_permanent_error_not_retried_and_dialogue_preserved(self):
        error = RuntimeError('SECRET')
        error.code = 401
        row = {}
        speech = 'Run now, they are right behind us!'
        with patch.object(m, 'gemini', side_effect=error) as gemini, patch.object(m, 'groq', side_effect=error) as groq:
            result = m.choose_metadata(self.sample, speech, [b'frame'], row)
        self.assertEqual(gemini.call_count, 1)
        self.assertEqual(groq.call_count, 1)
        self.assertEqual(row['metadata_method'], 'clip_dialogue')
        self.assertIn(result['description'], speech)

    def test_early_success_stops(self):
        with patch.object(m, 'gemini', return_value=self.clean), patch.object(m, 'groq') as groq:
            self.assertEqual(m.choose_metadata(self.sample, '', [b'frame']), self.clean)
        groq.assert_not_called()

    def test_groq_payload_includes_images_and_json_mode(self):
        response = Mock()
        response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.clean)}}]}
        post = Mock(return_value=response)
        with patch.dict(sys.modules, {'httpx': types.SimpleNamespace(post=post)}):
            self.assertEqual(m.groq('prompt', [b'frame']), self.clean)
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'qwen/qwen3.8-27b')
        self.assertTrue(payload['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(payload['response_format'], {'type': 'json_object'})

    def test_retry_after_and_permanent_classification(self):
        error = RuntimeError('SECRET')
        error.response = types.SimpleNamespace(status_code=429, headers={'retry-after': '12'})
        self.assertEqual(m.retry_delay(error), 12)
        self.assertTrue(m.transient(error))
        self.assertFalse(m.transient(TypeError('programming error')))

    def test_old_method_does_not_survive_failure(self):
        row = {'metadata_method': 'groq'}
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                m.choose_metadata(self.sample, '', [], row)
        self.assertNotIn('metadata_method', row)
