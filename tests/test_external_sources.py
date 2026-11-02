import copy
import os
import sys
import types
import unittest
from unittest.mock import Mock, patch
import worker as w


class ExternalSourceTests(unittest.TestCase):
    def setUp(self):
        self.state = {'version': 1, 'sources': {}, 'uploads': {}, 'channel_id': 'destination-channel'}
        self.sources = ['abcdefghij0']
        self.store = Mock()
        self.store.preflight.side_effect = lambda: (self.state, self.sources)
        self.yt = Mock()
        self.yt.channels.return_value.list.return_value.execute.return_value = {'items': [{'id': 'destination-channel'}]}
        self.yt.videos.side_effect = AssertionError('Source-channel metadata must not gate processing')
        self.process = Mock()
        modules = {}
        for name in ('google', 'google.oauth2', 'google.oauth2.credentials', 'google.auth', 'google.auth.transport', 'google.auth.transport.requests', 'googleapiclient', 'googleapiclient.discovery', 'providers'):
            module = types.ModuleType(name)
            module.__path__ = []
            modules[name] = module
        self.credentials = Mock()
        modules['google.oauth2.credentials'].Credentials = Mock(return_value=self.credentials)
        modules['google.auth.transport.requests'].Request = Mock()
        modules['googleapiclient.discovery'].build = Mock(return_value=self.yt)
        modules['providers'].Providers = Mock()
        for context in (patch.dict(sys.modules, modules), patch.object(w, 'Store', return_value=self.store),
                        patch.object(w, 'process', self.process), patch.dict(os.environ, {
                            'YT_CLIENT_ID': 'test-only', 'YT_CLIENT_SECRET': 'test-only', 'YT_REFRESH_TOKEN': 'test-only'}, clear=True)):
            context.start()
            self.addCleanup(context.stop)

    def test_external_source_reaches_processing(self):
        w.main()
        self.assertEqual(self.process.call_args.args[0], self.sources[0])
        self.yt.videos.assert_not_called()
        self.credentials.refresh.assert_called_once()

    def test_multiple_channels_no_batch_wide_ownership_gate(self):
        self.sources[:] = ['abcdefghij0', 'abcdefghij1', 'abcdefghij2']
        w.main()
        self.assertEqual([call.args[0] for call in self.process.call_args_list], self.sources)
        self.yt.videos.assert_not_called()

    def test_destination_channel_mismatch_still_blocks(self):
        self.state['channel_id'] = 'different-channel'
        with self.assertRaisesRegex(w.Failure, 'channel_mismatch'):
            w.main()
        self.process.assert_not_called()

    def test_old_upload_history_preserved(self):
        self.state['uploads']['existing'] = {'source': 'old-source', 'status': 'uploaded', 'youtube_id': 'old-upload'}
        before = copy.deepcopy(self.state['uploads'])
        w.main()
        self.assertEqual(self.state['uploads'], before)
        self.assertEqual(self.state['channel_id'], 'destination-channel')

    def test_uploads_without_destination_binding_blocked(self):
        self.state.pop('channel_id')
        self.state['uploads']['existing'] = {'status': 'uploaded'}
        with self.assertRaisesRegex(w.Failure, 'channel_mismatch'):
            w.main()
        self.process.assert_not_called()

    def test_upload_visibility_stays_private(self):
        body = w.private_body({'title': 'Example', 'reason': ''}, 'reference')
        self.assertEqual(body['status']['privacyStatus'], 'private')
        self.assertNotIn('publishAt', body['status'])

    def test_non_youtube_source_still_rejected(self):
        with self.assertRaises(w.Failure):
            w.source_id('https://example.com/abcdefghij0')

    def test_empty_queue_does_not_start_processing(self):
        self.sources.clear()
        w.main()
        self.process.assert_not_called()
        self.credentials.refresh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
