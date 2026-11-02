import ast
import os
from pathlib import Path
import re
import types
import unittest
from unittest.mock import Mock, patch
import quality as q


class CountTests(unittest.TestCase):
    def setUp(self):
        self.previous = q.G
        q.G = types.SimpleNamespace(W=types.SimpleNamespace(Failure=RuntimeError, moments=lambda rows, *args: rows))

    def tearDown(self):
        q.G = self.previous

    def rows(self, count=30):
        return [{'start_time': float(i * 30), 'end_time': float(i * 30 + 20), 'title': str(i), 'reason': ''} for i in range(count)]

    def test_all_six_limits(self):
        for count in (5, 10, 15, 20, 25, 30):
            with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': str(count)}):
                self.assertEqual(q.clip_limit(), count)
                self.assertEqual(len(q.sample_plan(2700)), count)

    def test_default_stays_five(self):
        with patch.dict(os.environ):
            os.environ.pop('MAX_SHORTS_PER_VIDEO', None)
            self.assertEqual(q.clip_limit(), 5)

    def test_invalid_values_rejected(self):
        for value in ('0', '6', '11', '31', '100', ''):
            with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': value}):
                with self.assertRaises(ValueError):
                    q.clip_limit()

    def test_ai_selection_reaches_thirty(self):
        client = Mock(generate=Mock(return_value=self.rows()))
        store = types.SimpleNamespace(save=Mock())
        state = {'sources': {}, 'uploads': {}}
        segments = [types.SimpleNamespace(start=0.0, end=1000.0, text='speech')]
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '30'}):
            selected = q.select_plan(client, segments, 1000, state, 'source', store)
        self.assertEqual(len(selected), 30)
        self.assertEqual(state['sources']['source']['maximum'], 30)
        self.assertIn('Choose up to 30', client.generate.call_args.args[0])

    def test_ai_selection_obeys_every_limit(self):
        for count in (5, 10, 15, 20, 25, 30):
            with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': str(count)}):
                selected = q.select_plan(Mock(generate=Mock(return_value=self.rows())),
                    [types.SimpleNamespace(start=0.0, end=1000.0, text='speech')],
                    1000, {'sources': {}, 'uploads': {}}, 'source', types.SimpleNamespace(save=Mock()))
                self.assertEqual(len(selected), count)

    def test_count_changes_cache_identity(self):
        client = Mock(generate=Mock(return_value=self.rows()))
        state = {'sources': {}, 'uploads': {}}
        store = types.SimpleNamespace(save=Mock())
        segments = [types.SimpleNamespace(start=0.0, end=1000.0, text='speech')]
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '5'}):
            q.select_plan(client, segments, 1000, state, 'source', store)
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '30'}):
            selected = q.select_plan(client, segments, 1000, state, 'source', store)
        self.assertEqual(client.generate.call_count, 2)
        self.assertEqual(len(selected), 30)

    def test_saved_thirty_clip_plan_resumes_under_five_selection(self):
        rows = self.rows()
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '5'}):
            self.assertEqual(q.saved_plan({'moments': rows, 'maximum': 30}, 1000), rows)

    def test_saved_plan_over_thirty_rejected(self):
        with self.assertRaises(RuntimeError):
            q.saved_plan({'moments': self.rows(31)}, 1000)

    def test_empty_saved_plan_rejected(self):
        with self.assertRaises(RuntimeError):
            q.saved_plan({'moments': []}, 1000)

    def test_short_source_limits_sample_count(self):
        with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': '30'}):
            self.assertEqual(len(q.sample_plan(60)), 4)

    def test_sample_bounds_for_all_options(self):
        for count in (5, 10, 15, 20, 25, 30):
            with patch.dict(os.environ, {'MAX_SHORTS_PER_VIDEO': str(count)}):
                rows = q.sample_plan(2700)
                self.assertTrue(all(0 <= row['start_time'] < row['end_time'] <= 2700 for row in rows))
                self.assertTrue(all(15 <= row['end_time'] - row['start_time'] <= 45 for row in rows))
                self.assertTrue(all(a['end_time'] <= b['start_time'] for a, b in zip(rows, rows[1:])))

    def test_workflow_dropdown_matches_runtime(self):
        text = (Path(__file__).resolve().parents[1] / '.github/workflows/manual.yml').read_text()
        options = ast.literal_eval(re.search(r'options: (\[.*\])', text).group(1))
        self.assertEqual(options, ['5', '10', '15', '20', '25', '30'])
        self.assertIn("default: '5'", text)


if __name__ == '__main__':
    unittest.main()
