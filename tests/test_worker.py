import contextlib
import copy
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import worker


class SafetyTests(unittest.TestCase):
    def state(self):
        return {'version': 1, 'sources': {}, 'uploads': {}}

    def test_queue_limit(self):
        text = '\n'.join('https://youtu.be/abcdefghij' + str(i) for i in range(8))
        self.assertEqual(len(worker.select_sources(text, self.state())), 5)

    def test_deduplicate_and_skip_completed(self):
        state = self.state()
        state['sources']['abcdefghij0'] = 'complete'
        text = '# ignored\nhttps://youtu.be/abcdefghij0,https://youtu.be/abcdefghij1\nhttps://youtu.be/abcdefghij1'
        self.assertEqual(worker.select_sources(text, state), ['abcdefghij1'])

    def test_foreign_url_rejected(self):
        with self.assertRaises(worker.Failure):
            worker.source_id('https://example.com/abcdefghij0')

    def test_plain_http_rejected(self):
        with self.assertRaises(worker.Failure):
            worker.source_id('http://youtu.be/abcdefghij0')

    def test_uncertain_upload_blocks(self):
        state = self.state()
        state['uploads']['key'] = {'status': 'uploading'}
        with self.assertRaises(worker.Failure):
            worker.validate_state(state)

    def test_bad_ledger_rejected(self):
        for state in ({}, None, {'version': 1, 'sources': [], 'uploads': {}}):
            with self.assertRaises(worker.Failure):
                worker.validate_state(state)

    def test_no_default_ledger_reset(self):
        state = self.state()
        state['uploads']['key'] = 'bad'
        with self.assertRaises(worker.Failure):
            worker.validate_state(state)

    def moment(self):
        return {'start_time': 0.0, 'end_time': 20.0, 'title': 'Example', 'reason': ''}

    def test_valid_plan(self):
        row = self.moment()
        self.assertEqual(worker.moments([row], 100), [row])

    def test_bad_timestamps_rejected(self):
        for start, end in ((True, 20), (0, math.inf), (-1, 20), (0, 200), (0, 14)):
            row = dict(self.moment(), start_time=start, end_time=end)
            with self.assertRaises(worker.Failure):
                worker.moments([row], 100)

    def test_legacy_plan_not_truncated(self):
        with self.assertRaises(worker.Failure):
            worker.saved_plan({'moments': [self.moment()] * 11}, 100)

    def test_private_upload_only(self):
        status = worker.private_body(self.moment(), 'key')['status']
        self.assertEqual(status['privacyStatus'], 'private')
        self.assertNotIn('publishAt', status)

    def test_clip_key_compatible(self):
        import hashlib
        row = self.moment()
        expected = hashlib.sha256(b'abcdefghij0|0.0|20.0').hexdigest()[:24]
        self.assertEqual(worker.key_for('abcdefghij0', row), expected)

    def test_checkpoint_cas(self):
        store = object.__new__(worker.Store)
        store.branch, store.sha = 'main', 'existing-sha'
        store.api = Mock(side_effect=[{'private': True}, {'content': {'sha': 'new-sha'}}])
        store.save(self.state())
        payload = store.api.call_args.args[1]
        self.assertEqual(payload['sha'], 'existing-sha')
        self.assertEqual(store.sha, 'new-sha')

    def test_checkpoint_conflict_stops(self):
        store = object.__new__(worker.Store)
        store.branch, store.sha = 'main', 'existing-sha'
        store.api = Mock(side_effect=RuntimeError('conflict'))
        with self.assertRaises(RuntimeError):
            store.save(self.state())
        self.assertEqual(store.sha, 'existing-sha')

    def test_state_must_be_private(self):
        with patch.dict(os.environ, {'PRIVATE_STATE_REPO': 'owner/state', 'PRIVATE_STATE_TOKEN': 'test-only'}):
            with patch.object(worker.Store, 'api', return_value={'private': False}):
                with self.assertRaises(worker.Failure):
                    worker.Store()

    def test_legacy_workflows_must_be_disabled(self):
        store = object.__new__(worker.Store)
        store.api = Mock(return_value={'state': 'active'})
        with self.assertRaises(worker.Failure):
            store.preflight()

    def test_redirects_rejected(self):
        self.assertIsNone(worker.NoRedirect().redirect_request(None))

    def test_silent_wrapper_success_and_failure(self):
        root = Path(__file__).resolve().parents[1]
        for fail in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                (path / 'quiet.py').write_text((root / 'quiet.py').read_text())
                (path / 'worker.py').write_text("import os\ndef main():\n    print('PRIVATE_DETAIL')\n    os.write(2,b'PRIVATE_NATIVE')\n" + ("    raise RuntimeError('PRIVATE_EXCEPTION')\n" if fail else ''))
                result = subprocess.run([sys.executable, 'quiet.py'], cwd=path, capture_output=True, text=True)
                self.assertEqual(result.stdout, 'Failed\n' if fail else 'Success\n')
                self.assertEqual(result.stderr, '')
                self.assertEqual(result.returncode, 1 if fail else 0)


if __name__ == '__main__':
    unittest.main()
