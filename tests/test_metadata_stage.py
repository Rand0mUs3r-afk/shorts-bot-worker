import types
import unittest
from unittest.mock import Mock
import diagnostics as d


class MetadataStageTests(unittest.TestCase):
    def report(self):
        report = object.__new__(d.Reporter)
        report.doc = {'events': [], 'status': 'running', 'stage': 'processing'}
        report.write = Mock()
        return report

    def test_metadata_stage_and_source_result_are_accepted(self):
        report = self.report()
        report.stage('render')
        report.stage('metadata')
        report.source_result('flg3V4VLa5g', 'failed', 'metadata', RuntimeError('metadata_unavailable'))
        self.assertEqual(report.doc['events'][0]['status'], 'completed')
        self.assertEqual(report.doc['source_results']['flg3V4VLa5g']['error']['category'], 'metadata_unavailable')

    def test_tracked_store_accepts_metadata_transition(self):
        class Store:
            pass
        def main():
            store = Store()
            store.verified = True
            store.stage = 'render'
            store.stage = 'metadata'
        module = types.SimpleNamespace(Store=Store, main=main, process=lambda *a: None,
                                       plan=lambda *a: None, command=lambda *a: '')
        original = Store.__setattr__
        report = self.report()
        d.run_worker(report, module)
        self.assertIn('metadata', [event['stage'] for event in report.doc['events']])
        self.assertIs(Store.__setattr__, original)

    def test_known_metadata_failure_remains_recoverable(self):
        module = types.SimpleNamespace(Failure=RuntimeError)
        self.assertTrue(d.recoverable_source_error(RuntimeError('metadata_unavailable'), 'metadata', {'uploads': {}}, module))
        self.assertFalse(d.recoverable_source_error(RuntimeError('upload_uncertain'), 'metadata', {'uploads': {}}, module))
        self.assertFalse(d.recoverable_source_error(RuntimeError('metadata_unavailable'), 'metadata', {'uploads': {'clip': {'status': 'uploading'}}}, module))

    def test_real_tracking_records_failure_without_changing_uploads(self):
        class Store:
            pass
        class Failure(RuntimeError):
            pass
        source = 'flg3V4VLa5g'
        state = {'sources': {source: {}}, 'uploads': {'done': {'status': 'uploaded', 'youtube_id': 'existing'}}}
        def process(source, yt, client, models, state, store, work):
            store.stage = 'metadata'
            raise Failure('metadata_unavailable')
        def main():
            store = Store()
            store.verified = True
            module.process(source, None, None, [], state, store, None)
        module = types.SimpleNamespace(Store=Store, Failure=Failure, main=main, process=process,
                                       plan=lambda *a: None, command=lambda *a: '')
        report = self.report()
        d.run_worker(report, module)
        self.assertEqual(report.doc['source_results'][source]['stage'], 'metadata')
        self.assertEqual(report.doc['status'], 'failed')
        self.assertEqual(state['uploads']['done']['youtube_id'], 'existing')
        self.assertIs(module.process, process)
