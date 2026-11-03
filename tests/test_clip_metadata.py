import copy
import hashlib
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch
import clip_metadata as m
import quality as q


class MetadataTests(unittest.TestCase):
    def clean(self):
        return {'title': 'A Last-Second Escape', 'description': 'The squad fights through a sudden ambush and makes a narrow escape.'}

    def context(self, placeholder=True):
        moment = {'start_time': 0.0, 'end_time': 20.0,
                  'title': 'Video sample 01' if placeholder else self.clean()['title'],
                  'reason': 'Automatically spaced sample; not an AI-ranked highlight.' if placeholder else self.clean()['description']}
        state = {'sources': {'source': {'moments': [moment]}}, 'uploads': {}}
        row = {}
        generation = types.SimpleNamespace(W=types.SimpleNamespace(Failure=RuntimeError,
            key_for=lambda source, item: hashlib.sha256(f"{source}|{item['start_time']}|{item['end_time']}".encode()).hexdigest()[:24]),
            stats=lambda source: row)
        store = types.SimpleNamespace(save=Mock())
        return moment, {'generation': generation, 'source': 'source', 'state': state, 'store': store, 'segments': []}, row

    def test_clean_fields_accepted(self):
        self.assertEqual(m.validate(self.clean()), self.clean())

    def test_sample_titles_rejected(self):
        for title in ('Video sample 01', 'sample video 02', 'Clip 03'):
            with self.assertRaises(ValueError):
                m.validate(dict(self.clean(), title=title))

    def test_unwanted_description_text_rejected(self):
        for description in ('Bot reference: 12345', 'An AI-generated action scene.', 'Watch https://example.com/video',
                            'Source: original upload', 'Visit www.example.com now', 'A close escape! #shorts'):
            with self.assertRaises(ValueError):
                m.validate(dict(self.clean(), description=description))

    def test_meta_commentary_is_rejected(self):
        with self.assertRaises(ValueError):
            m.validate(dict(self.clean(), description='This clip is a great highlight for viewers.'))

    def test_upload_body_has_only_story_metadata(self):
        body = m.private_body({'title': self.clean()['title'], 'reason': self.clean()['description']}, 'private-reference')
        self.assertEqual(body['snippet'], self.clean())
        self.assertEqual(body['status']['privacyStatus'], 'private')
        self.assertNotIn('private-reference', str(body))
        self.assertNotIn('publishAt', body['status'])

    def test_placeholder_is_replaced_before_reservation(self):
        moment, context, row = self.context()
        original_key = context['generation'].W.key_for('source', moment)
        def save(state):
            self.assertEqual(state['uploads'], {})
        context['store'].save.side_effect = save
        with patch.object(m, 'choose_metadata', return_value=self.clean()), patch.object(m, 'frames', return_value=[]):
            m.enrich(moment, Path('clip.mp4'), context)
        self.assertEqual(moment['title'], self.clean()['title'])
        self.assertEqual(moment['reason'], self.clean()['description'])
        self.assertEqual(original_key, context['generation'].W.key_for('source', moment))
        context['store'].save.assert_called_once()
        self.assertEqual(row['metadata_status'], 'ready')

    def test_metadata_cached_for_retry(self):
        moment, context, row = self.context()
        with patch.object(m, 'choose_metadata', return_value=self.clean()) as choose, patch.object(m, 'frames', return_value=[]):
            m.enrich(moment, Path('clip.mp4'), context)
            m.enrich(moment, Path('clip.mp4'), context)
        choose.assert_called_once()

    def test_no_generic_upload_when_metadata_unavailable(self):
        moment, context, row = self.context()
        with patch.object(m, 'choose_metadata', side_effect=ValueError('unavailable')), patch.object(m, 'frames', return_value=[]):
            with self.assertRaises(RuntimeError):
                m.enrich(moment, Path('clip.mp4'), context)
        self.assertEqual(context['state']['uploads'], {})
        self.assertEqual(row['metadata_status'], 'unavailable')
        context['store'].save.assert_not_called()

    def test_provider_failure_can_keep_existing_descriptive_metadata(self):
        moment, context, row = self.context(False)
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-only', 'GROQ_API_KEY': 'test-only'}),              patch.object(m, 'gemini', side_effect=RuntimeError('private')), patch.object(m, 'groq', side_effect=RuntimeError('private')):
            self.assertEqual(m.choose_metadata(moment, 'spoken evidence', [b'image']), self.clean())

    def test_text_only_request_does_not_claim_visual_access(self):
        prompt = m.prompt_for({'title': 'Video sample 01', 'reason': 'untrusted'}, 'speech', False)
        self.assertIn('No images are available', prompt)
        self.assertNotIn('untrusted"', prompt)

    def test_groq_not_used_for_a_silent_placeholder(self):
        moment, context, row = self.context()
        with patch.dict(os.environ, {'GROQ_API_KEY': 'test-only', 'GEMINI_API_KEY': ''}), patch.object(m, 'groq') as groq:
            with self.assertRaises(ValueError):
                m.choose_metadata(moment, '', [])
        groq.assert_not_called()

    def test_quality_render_calls_metadata_only_with_active_context(self):
        moment, context, row = self.context()
        fake = types.SimpleNamespace(W=types.SimpleNamespace(command=Mock(), duration=Mock(return_value=20), Failure=RuntimeError))
        with patch.object(q, 'G', fake), patch.object(q, 'METADATA_CONTEXT', context), patch.object(m, 'enrich') as enrich:
            q.render_clip(Path('source.mp4'), Path('clip.mp4'), moment)
        enrich.assert_called_once_with(moment, Path('clip.mp4'), context)

    def test_transcript_hook_is_restored(self):
        rows = [types.SimpleNamespace(start=0, end=20, text='clip speech')]
        fake = types.SimpleNamespace(W=types.SimpleNamespace(command=Mock()), transcribe=Mock(return_value=rows), stats=lambda source: {})
        original = fake.transcribe
        def run(*args):
            fake.transcribe(None, [], 'source')
            self.assertEqual(q.METADATA_CONTEXT['segments'], rows)
            raise RuntimeError('stop')
        with patch.object(q, 'G', fake), patch.object(q, 'BASE_PROCESS', run), patch.object(q, 'METADATA_CONTEXT', None):
            with self.assertRaises(RuntimeError):
                q.process('source', None, None, [], {}, None, Path('.'))
            self.assertIs(fake.transcribe, original)
            self.assertIsNone(q.METADATA_CONTEXT)


if __name__ == '__main__':
    unittest.main()
