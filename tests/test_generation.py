import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import generation as g


class GenerationTests(unittest.TestCase):
    def setUp(self):
        g.STATS.clear()
        self.old_worker, self.old_plan = g.W, g.BASE_PLAN
        g.W = types.SimpleNamespace(Failure=RuntimeError,
            source_id=lambda text: text.rsplit('/', 1)[-1],
            moments=lambda rows, total: rows,
            key_for=lambda source, moment: hashlib.sha256(f"{source}|{moment['start_time']}|{moment['end_time']}".encode()).hexdigest()[:24],
            private_body=lambda moment, key: {'status': {'privacyStatus': 'private'}},
            saved_plan=lambda saved, total: saved['moments'])
        self.store = types.SimpleNamespace(stage='', save=Mock())

    def tearDown(self):
        g.W, g.BASE_PLAN = self.old_worker, self.old_plan

    def state(self):
        return {'version': 1, 'sources': {}, 'uploads': {}}

    def moment(self):
        return {'start_time': 0.0, 'end_time': 20.0, 'title': 'Clip', 'reason': ''}

    def test_fallback_counts_and_bounds(self):
        for total in (15, 16, 29.999, 30, 59, 150, 2700, 3600.1234):
            rows = g.sample_plan(total)
            self.assertTrue(1 <= len(rows) <= 10)
            for row in rows:
                self.assertTrue(0 <= row['start_time'] < row['end_time'] <= total)
                self.assertTrue(14.999 <= row['end_time'] - row['start_time'] <= 45.001)
            self.assertTrue(all(a['end_time'] <= b['start_time'] for a, b in zip(rows, rows[1:])))

    def test_too_short_rejected(self):
        for value in (0, 14.99, math.nan, math.inf):
            with self.assertRaises(ValueError):
                g.sample_plan(value)

    def test_samples_not_mislabeled_as_ai_highlights(self):
        row = g.sample_plan(2700)[0]
        self.assertIn('sample', row['title'])
        self.assertIn('not an AI-ranked', row['reason'])

    def test_reopens_empty_completion(self):
        state = self.state()
        state['sources']['abcdefghij0'] = 'complete'
        self.assertEqual(g.select_sources('https://youtu.be/abcdefghij0', state), ['abcdefghij0'])
        self.assertNotIn('abcdefghij0', state['sources'])

    def test_preserves_real_upload_history(self):
        state = self.state()
        state['sources']['abcdefghij0'] = 'complete'
        state['uploads']['old'] = {'source': 'abcdefghij0', 'status': 'uploaded', 'youtube_id': 'existing-id'}
        before = copy.deepcopy(state)
        self.assertEqual(g.select_sources('https://youtu.be/abcdefghij0', state), [])
        self.assertEqual(state, before)

    def test_queue_limit_and_deduplication(self):
        text = '\n'.join('https://youtu.be/abcdefghij' + str(index) for index in range(8))
        self.assertEqual(len(g.select_sources(text + '\n' + text, self.state())), 5)

    def test_vad_retry(self):
        model = Mock()
        item = types.SimpleNamespace(start=0.0, end=20.0, text='Speech')
        model.transcribe.side_effect = [(iter([]), None), (iter([item]), None)]
        g.stats('source')['has_audio'] = True
        self.assertEqual(g.transcribe(Path('source.mp4'), [model], 'source'), [item])
        self.assertEqual([call.kwargs['vad_filter'] for call in model.transcribe.call_args_list], [True, False])

    def test_no_audio_uses_samples(self):
        g.stats('source')['has_audio'] = False
        self.assertEqual(g.transcribe(Path('source.mp4'), [], 'source'), [])
        self.assertEqual(g.stats('source')['transcription_mode'], 'no_audio')

    def test_empty_transcript_produces_plan(self):
        state = self.state()
        rows = g.build_plan(Mock(), [], 2700, state, 'source', self.store)
        self.assertEqual(len(rows), 10)
        self.assertEqual(state['sources']['source']['selection_method'], 'spaced_samples')
        self.store.save.assert_called_once()

    def test_ai_empty_produces_plan(self):
        state = self.state()
        g.BASE_PLAN = Mock(return_value=[])
        rows = g.build_plan(Mock(), [object()], 120, state, 'source', self.store)
        self.assertEqual(len(rows), 8)

    def test_ai_outage_still_produces_samples(self):
        g.BASE_PLAN = Mock(side_effect=RuntimeError('ai_failed'))
        rows = g.build_plan(Mock(), [object()], 120, self.state(), 'source', self.store)
        self.assertEqual(len(rows), 8)
        self.assertEqual(g.stats('source')['fallback_reason'], 'ai_selection_error')

    def test_ai_plan_preserved(self):
        g.BASE_PLAN = Mock(return_value=[self.moment()])
        rows = g.build_plan(Mock(), [object()], 120, self.state(), 'source', self.store)
        self.assertEqual(rows, [self.moment()])
        self.assertEqual(g.stats('source')['selection_method'], 'ai')

    def test_unknown_checkpoint_failure_not_hidden(self):
        g.BASE_PLAN = Mock(side_effect=RuntimeError('checkpoint-conflict'))
        with patch.dict(sys.modules, {'providers': types.SimpleNamespace(Providers=object)}):
            with self.assertRaises(RuntimeError):
                g.build_plan(Mock(), [object()], 120, self.state(), 'source', self.store)

    def configure_media(self):
        g.W.command = Mock(return_value=json.dumps({'streams': [{'codec_type': 'video'}, {'codec_type': 'audio'}]}))
        g.W.duration = Mock(return_value=120)
        g.W.plan = Mock(return_value=[self.moment()])

    def test_success_requires_recorded_upload(self):
        self.configure_media()
        state = self.state()
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'transcribe', return_value=[]), patch.object(g, 'render_clip'), patch.object(g, 'upload_clip', return_value='abcdefghij1') as upload:
            g.process('abcdefghij0', Mock(), Mock(), [], state, self.store, Path(folder))
        self.assertEqual(state['sources']['abcdefghij0'], 'complete')
        self.assertEqual(next(iter(state['uploads'].values()))['status'], 'uploaded')
        self.assertEqual(g.stats('abcdefghij0')['uploaded_this_run'], 1)
        upload.assert_called_once()

    def test_reservation_saved_before_upload(self):
        self.configure_media()
        state = self.state()
        def verify(*args):
            self.assertGreater(self.store.save.call_count, 0)
            self.assertEqual(next(iter(state['uploads'].values()))['status'], 'uploading')
            return 'abcdefghij1'
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'transcribe', return_value=[]), patch.object(g, 'render_clip'), patch.object(g, 'upload_clip', side_effect=verify):
            g.process('abcdefghij0', Mock(), Mock(), [], state, self.store, Path(folder))

    def test_zero_plan_cannot_be_complete(self):
        self.configure_media()
        g.W.plan.return_value = []
        state = self.state()
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'transcribe', return_value=[]):
            with self.assertRaises(RuntimeError):
                g.process('abcdefghij0', Mock(), Mock(), [], state, self.store, Path(folder))
        self.assertNotEqual(state['sources'].get('abcdefghij0'), 'complete')

    def test_upload_failure_keeps_uncertain_reservation(self):
        self.configure_media()
        state = self.state()
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'transcribe', return_value=[]), patch.object(g, 'render_clip'), patch.object(g, 'upload_clip', side_effect=RuntimeError('upload failed')):
            with self.assertRaises(RuntimeError):
                g.process('abcdefghij0', Mock(), Mock(), [], state, self.store, Path(folder))
        self.assertEqual(next(iter(state['uploads'].values()))['status'], 'uploading')
        self.assertNotEqual(state['sources'].get('abcdefghij0'), 'complete')

    def test_existing_upload_not_duplicated(self):
        self.configure_media()
        state = self.state()
        moment = self.moment()
        state['sources']['abcdefghij0'] = {'moments': [moment]}
        state['uploads'][g.W.key_for('abcdefghij0', moment)] = {'source': 'abcdefghij0', 'status': 'uploaded'}
        with tempfile.TemporaryDirectory() as folder, patch.object(g, 'upload_clip') as upload:
            g.process('abcdefghij0', Mock(), Mock(), [], state, self.store, Path(folder))
        upload.assert_not_called()
        self.assertEqual(g.stats('abcdefghij0')['already_uploaded'], 1)


if __name__ == '__main__':
    unittest.main()
