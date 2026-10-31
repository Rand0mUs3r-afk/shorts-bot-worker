import copy
import math
import os
from pathlib import Path
import types
import unittest
from unittest.mock import Mock, patch
import quality as q


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '5'})
        self.env.start()
        self.previous = q.G, q.BASE_BUILD, q.BASE_PROCESS
        self.rows = {}
        q.G = types.SimpleNamespace(W=types.SimpleNamespace(Failure=RuntimeError), stats=lambda source: self.rows.setdefault(source, {}))

    def tearDown(self):
        q.G, q.BASE_BUILD, q.BASE_PROCESS = self.previous
        self.env.stop()

    def moments(self, count):
        return [{'start_time': float(index * 20), 'end_time': float(index * 20 + 20), 'title': 'Clip', 'reason': ''} for index in range(count)]

    def test_default_is_five(self):
        with patch.dict(os.environ):
            os.environ.pop('MAX_SHORTS_PER_VIDEO', None)
            self.assertEqual(q.clip_limit(), 5)

    def test_ten_supported(self):
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '10'}):
            self.assertEqual(q.clip_limit(), 10)
            self.assertEqual(len(q.sample_plan(2700)), 10)

    def test_invalid_counts_rejected(self):
        for count in ('0', '6', '11', '100', 'bad'):
            with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': count}):
                with self.assertRaises(ValueError):
                    q.clip_limit()

    def test_five_samples_spread_across_source(self):
        rows = q.sample_plan(2700)
        self.assertEqual(len(rows), 5)
        self.assertGreater(rows[-1]['start_time'], 2000)
        self.assertTrue(all(a['end_time'] <= b['start_time'] for a, b in zip(rows, rows[1:])))

    def test_sample_duration_bounds(self):
        for total in (15, 16, 29.999, 30, 75, 120, 2700):
            rows = q.sample_plan(total)
            self.assertTrue(1 <= len(rows) <= 5)
            for row in rows:
                self.assertTrue(0 <= row['start_time'] < row['end_time'] <= total)
                self.assertTrue(14.999 <= row['end_time'] - row['start_time'] <= 45.001)

    def test_too_short_rejected(self):
        for total in (0, 14.9, math.nan, math.inf):
            with self.assertRaises(ValueError):
                q.sample_plan(total)

    def test_first_saved_plan_already_capped(self):
        state = {'sources': {}, 'uploads': {}}
        captured = []
        store = types.SimpleNamespace(save=lambda current: captured.append(copy.deepcopy(current)))
        def original(client, segments, total, current, source, checkpoint):
            selected = self.moments(10)
            current['sources'][source] = {'moments': selected, 'maximum': 10}
            checkpoint.save(current)
            return selected
        q.BASE_BUILD = original
        result = q.build_plan(Mock(), [], 300, state, 'source', store)
        self.assertEqual(len(result), 5)
        self.assertTrue(all(len(row['sources']['source']['moments']) == 5 for row in captured))
        self.assertEqual(state['sources']['source']['maximum'], 5)

    def test_ai_request_uses_preference(self):
        client = Mock()
        def original(configured, *args):
            configured.generate('Choose up to 10 highlights')
            return self.moments(2)
        q.BASE_BUILD = original
        q.build_plan(client, [], 100, {'sources': {}, 'uploads': {}}, 'source', types.SimpleNamespace(save=Mock()))
        client.generate.assert_called_once_with('Choose up to 5 highlights')

    def test_empty_plan_not_success(self):
        q.BASE_BUILD = Mock(return_value=[])
        with self.assertRaises(RuntimeError):
            q.build_plan(Mock(), [], 100, {'sources': {}, 'uploads': {}}, 'source', types.SimpleNamespace(save=Mock()))

    def test_render_settings(self):
        q.G.W.command = Mock()
        q.G.W.duration = Mock(return_value=20)
        q.render_clip(Path('source.mp4'), Path('clip.mp4'), self.moments(1)[0])
        args = q.G.W.command.call_args.args[0]
        self.assertEqual(args[args.index('-crf') + 1], '16')
        self.assertEqual(args[args.index('-b:a') + 1], '192k')
        self.assertIn('scale=1620:2880', q.video_filter())
        self.assertIn('crop=1080:1920', q.video_filter())
        self.assertEqual(q.video_filter().count('flags=lanczos'), 2)

    def test_duration_mismatch_rejected(self):
        q.G.W.command = Mock()
        q.G.W.duration = Mock(return_value=30)
        with self.assertRaises(RuntimeError):
            q.render_clip(Path('source.mp4'), Path('clip.mp4'), self.moments(1)[0])

    def test_source_quality_and_command_restored(self):
        original = Mock(return_value='ok')
        q.G.W.command = original
        def process(*args):
            return q.G.W.command(['yt-dlp', '-f', 'bv*[height<=1080]+ba/b[height<=1080]', 'source'])
        q.BASE_PROCESS = process
        self.assertEqual(q.process('source', None, None, [], {}, None, Path('.')), 'ok')
        self.assertEqual(original.call_args.args[0][2], 'bv*[height<=1440]+ba/b[height<=1440]')
        self.assertIs(q.G.W.command, original)

    def test_commands_restored_after_failure(self):
        original = Mock()
        q.G.W.command = original
        q.BASE_PROCESS = Mock(side_effect=RuntimeError('failure'))
        with self.assertRaises(RuntimeError):
            q.process('source', None, None, [], {}, None, Path('.'))
        self.assertIs(q.G.W.command, original)


if __name__ == '__main__':
    unittest.main()
