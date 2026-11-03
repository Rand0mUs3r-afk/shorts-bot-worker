import hashlib
import json
import os
from pathlib import Path
import types
import sys
import unittest
from unittest.mock import Mock, patch
import clip_metadata as m


class RecoveryTests(unittest.TestCase):
    def sample(self):
        return {'start_time': 0.0, 'end_time': 45.0, 'title': 'Video sample 01', 'reason': 'Automatically spaced sample; not an AI-ranked highlight.'}

    def speech(self):
        return 'Run now, they are right behind us! The gate will not stay open much longer.'

    def context(self):
        moment = self.sample()
        row = {'has_audio': True}
        generation = types.SimpleNamespace(W=types.SimpleNamespace(Failure=RuntimeError,
            key_for=lambda source, item: hashlib.sha256(f"{source}|{item['start_time']}|{item['end_time']}".encode()).hexdigest()[:24]), stats=lambda source: row)
        state = {'sources': {'source': {'moments': [moment]}}, 'uploads': {}}
        context = {'generation': generation, 'source': 'source', 'state': state, 'store': types.SimpleNamespace(save=Mock()),
                   'segments': [types.SimpleNamespace(start=0.0, end=40.0, text=self.speech())]}
        return moment, context, row

    def test_local_dialogue_replaces_sample_title(self):
        result = m.dialogue_metadata(self.speech())
        self.assertIn(result['title'], self.speech())
        self.assertIn(result['description'], self.speech())
        self.assertNotIn('sample', result['title'].lower())

    def test_provider_outages_do_not_block_real_dialogue(self):
        row = {}
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only', 'GROQ_API_KEY': 'test-only'}), patch.object(m, 'gemini', side_effect=RuntimeError('PRIVATE_KEY')), patch.object(m, 'groq', side_effect=RuntimeError('PRIVATE_KEY')):
            result = m.choose_metadata(self.sample(), self.speech(), [b'frame'], row)
        self.assertEqual(row['metadata_method'], 'clip_dialogue')
        self.assertIn(result['description'], self.speech())
        self.assertNotIn('PRIVATE_KEY', json.dumps(row))
        self.assertEqual(len(row['metadata_failures']), 2)

    def test_sanitized_http_error(self):
        error = RuntimeError('SECRET_TOKEN')
        error.response = types.SimpleNamespace(status_code=429)
        row = {}
        m.note(row, 'groq', error)
        self.assertEqual(row['metadata_failures'][0]['http_status'], 429)
        self.assertNotIn('SECRET_TOKEN', json.dumps(row))

    def test_output_lengths_normalized(self):
        data = {'title': 'A dangerous escape ' * 8, 'description': 'The team moves through the corridor under pressure. ' * 8}
        result = m.decode(json.dumps(data))
        self.assertLessEqual(len(result['title']), 60)
        self.assertLessEqual(len(result['description']), 240)

    def test_one_item_array_and_caption_alias(self):
        result = m.decode(json.dumps([{'title': 'The Gate Is Closing', 'caption': 'The team races to reach the closing gate.'}]))
        self.assertEqual(result['description'], 'The team races to reach the closing gate.')

    def test_promotional_or_secret_metadata_rejected(self):
        for text in ('Bot reference: hidden', 'Visit https://example.com now', 'An AI-generated scene.', 'Watch now and subscribe for more.'):
            with self.assertRaises(ValueError):
                m.validate({'title': 'A Last-Second Escape', 'description': text})

    def test_silent_placeholder_still_not_invented(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': ''}):
            with self.assertRaises(ValueError):
                m.choose_metadata(self.sample(), '', [], {})

    def test_resume_gets_speech_from_rendered_clip(self):
        moment, context, row = self.context()
        context['segments'] = []
        model = Mock()
        model.transcribe.return_value = (iter([types.SimpleNamespace(text=self.speech())]), None)
        with patch.object(m, '_WHISPER', model):
            speech = m.speech_for_clip(moment, Path('clip.mp4'), context, row)
        self.assertEqual(speech, self.speech())
        self.assertEqual(row['metadata_speech_origin'], 'clip_transcription')
        model.transcribe.assert_called_once()

    def test_no_extra_transcription_with_existing_clip_speech(self):
        moment, context, row = self.context()
        model = Mock()
        with patch.object(m, '_WHISPER', model):
            self.assertEqual(m.speech_for_clip(moment, Path('clip.mp4'), context, row), self.speech())
        model.transcribe.assert_not_called()

    def test_complete_recovery_persists_before_upload(self):
        moment, context, row = self.context()
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': ''}):
            m.enrich(moment, Path('clip.mp4'), context)
        self.assertEqual(row['metadata_status'], 'ready')
        self.assertEqual(row['metadata_method'], 'clip_dialogue')
        self.assertEqual(context['state']['uploads'], {})
        context['store'].save.assert_called_once()
        self.assertNotIn('sample', moment['title'].lower())

    def test_cached_metadata_is_reused(self):
        moment, context, row = self.context()
        with patch.dict(os.environ, {'GEMINI_API_KEY': '', 'GROQ_API_KEY': ''}):
            m.enrich(moment, Path('clip.mp4'), context)
        with patch.object(m, 'choose_metadata') as choose:
            m.enrich(moment, Path('clip.mp4'), context)
        choose.assert_not_called()
        self.assertEqual(row['metadata_method'], 'cached')

    def test_groq_reasoning_budget(self):
        response = Mock()
        response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'title': 'The Gate Is Closing', 'description': 'The team races to reach the closing gate.'})}}]}
        post = Mock(return_value=response)
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-only', 'GROQ_MODEL': 'openai/gpt-oss-20b'}), patch.dict(sys.modules, {'httpx': types.SimpleNamespace(post=post)}):
            m.groq('prompt')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['max_completion_tokens'], 4096)
        self.assertEqual(payload['reasoning_effort'], 'low')
        self.assertFalse(post.call_args.kwargs['follow_redirects'])

    def test_truncated_response_not_accepted_as_metadata(self):
        response = Mock()
        response.json.return_value = {'choices': [{'finish_reason': 'length'}]}
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-only'}), patch.dict(sys.modules, {'httpx': types.SimpleNamespace(post=Mock(return_value=response))}):
            with self.assertRaisesRegex(ValueError, 'metadata_incomplete'):
                m.groq('prompt')

    def test_final_body_has_no_reference_or_link(self):
        selected = m.dialogue_metadata(self.speech())
        body = m.private_body({'title': selected['title'], 'reason': selected['description']}, 'INTERNAL_REFERENCE')
        self.assertEqual(body['snippet'], selected)
        self.assertNotIn('INTERNAL_REFERENCE', json.dumps(body))
        self.assertEqual(body['status']['privacyStatus'], 'private')


if __name__ == '__main__':
    unittest.main()
