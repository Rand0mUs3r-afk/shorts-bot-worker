import json
import types
import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import diagnostics as d


class Failure(RuntimeError):
    pass


class BatchTests(unittest.TestCase):
    def report(self):
        result = object.__new__(d.Reporter)
        result.doc = {'events': [], 'status': 'running', 'stage': 'processing', 'source_stats': {}}
        result.write = Mock()
        return result

    def module(self, failed=(), fatal=None, uncertain=False):
        report = self.report()
        state = {'sources': {}, 'uploads': {}}
        called, status_codes = [], []
        class Store:
            def status(self, code):
                status_codes.append(code)
        store = Store()
        store.verified = True
        store.stage = 'download'
        ids = ['abcdefghij0', 'abcdefghij1', 'abcdefghij2']
        def process(source, yt, client, models, current, checkpoint, work):
            called.append(source)
            checkpoint.stage = 'download'
            if source in failed:
                if uncertain:
                    current['uploads']['uncertain'] = {'status': 'uploading'}
                if fatal is not None:
                    raise fatal
                raise d.MediaError('download_auth_required', 1)
            current['uploads'][source] = {'status': 'uploaded', 'source': source, 'youtube_id': 'recorded'}
            current['sources'][source] = 'complete'
            checkpoint.stage = 'source_checkpoint'
        module = types.SimpleNamespace(Store=Store, Failure=Failure, process=process, plan=lambda *args: [], command=lambda *args: '')
        def main():
            for source in ids:
                module.process(source, None, None, [], state, store, None)
            store.stage = 'complete'
            store.status('success')
        module.main = main
        return report, module, state, called, status_codes, ids

    def test_first_failure_does_not_block_next_sources(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'])
        d.run_worker(report, module)
        self.assertEqual(called, ids)
        self.assertEqual(report.doc['status'], 'partial_success')
        self.assertEqual(codes, ['partial_success'])
        self.assertNotIn(ids[0], state['sources'])
        self.assertEqual(state['sources'][ids[2]], 'complete')

    def test_middle_failure_preserves_first_and_processes_last(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij1'])
        d.run_worker(report, module)
        self.assertEqual(called, ids)
        self.assertEqual(report.doc['summary']['sources_completed'], 2)
        self.assertEqual(report.doc['summary']['sources_failed'], 1)
        self.assertEqual(state['uploads'][ids[0]]['status'], 'uploaded')
        self.assertEqual(state['uploads'][ids[2]]['status'], 'uploaded')

    def test_last_failure_keeps_completed_sources(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij2'])
        d.run_worker(report, module)
        self.assertEqual(report.doc['status'], 'partial_success')
        self.assertEqual(state['sources'][ids[0]], 'complete')
        self.assertNotIn(ids[2], state['sources'])

    def test_all_sources_fail_but_all_are_attempted(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0', 'abcdefghij1', 'abcdefghij2'])
        d.run_worker(report, module)
        self.assertEqual(called, ids)
        self.assertEqual(report.doc['status'], 'failed')
        self.assertEqual(report.doc['summary']['sources_failed'], 3)
        self.assertEqual(codes, ['failed'])

    def test_all_success(self):
        report, module, state, called, codes, ids = self.module()
        d.run_worker(report, module)
        self.assertEqual(report.doc['status'], 'success')
        self.assertEqual(report.doc['summary']['sources_completed'], 3)

    def test_unknown_failure_stops_batch(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'], RuntimeError('unexpected'))
        with self.assertRaises(RuntimeError):
            d.run_worker(report, module)
        self.assertEqual(called, [ids[0]])

    def test_checkpoint_failure_stops_batch(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'], Failure('state_encoding'))
        with self.assertRaises(Failure):
            d.run_worker(report, module)
        self.assertEqual(called, [ids[0]])

    def test_uncertain_upload_stops_batch(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'], uncertain=True)
        with self.assertRaises(d.MediaError):
            d.run_worker(report, module)
        self.assertEqual(called, [ids[0]])

    def test_disk_full_stops_batch(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'], d.MediaError('disk_full', 1))
        with self.assertRaises(d.MediaError):
            d.run_worker(report, module)
        self.assertEqual(called, [ids[0]])

    def test_reporting_failure_not_silently_skipped(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij0'])
        original = report.source_result
        def fail(*args, **kwargs):
            raise d.DiagnosticError('private_report_unavailable')
        report.source_result = fail
        with self.assertRaises(d.DiagnosticError):
            d.run_worker(report, module)
        self.assertEqual(called, [ids[0]])

    def test_failure_details_stay_sanitized(self):
        report = self.report()
        report.stage('download')
        report.source_result('abcdefghij0', 'failed', 'download', d.MediaError('download_auth_required', 1))
        result = report.doc['source_results']['abcdefghij0']
        self.assertEqual(result['error']['category'], 'download_auth_required')
        self.assertEqual(report.doc['events'][-1]['status'], 'failed')

    def test_safe_source_validation_failure_can_continue(self):
        state = {'uploads': {}}
        module = types.SimpleNamespace(Failure=Failure)
        self.assertTrue(d.recoverable_source_error(Failure('source_too_short'), 'download', state, module))
        self.assertFalse(d.recoverable_source_error(Failure('upload_uncertain'), 'upload', state, module))

    def test_partial_success_survives_workflow_finalization(self):
        report, module, state, called, codes, ids = self.module(['abcdefghij1'])
        d.run_worker(report, module)
        report.finish_workflow({'process': {'outcome': 'success'}, 'checks': {'outcome': 'success'}})
        self.assertEqual(report.doc['status'], 'partial_success')
        self.assertEqual(report.doc['summary']['sources_failed'], 1)

    def test_existing_numeric_summary_preserved(self):
        report = self.report()
        report.doc['summary'] = {'uploaded_this_run': 13, 'clips_selected': 13}
        report.doc['source_results'] = {'a': {'status': 'complete'}, 'b': {'status': 'failed'}}
        report.success()
        self.assertEqual(report.doc['summary']['uploaded_this_run'], 13)
        self.assertEqual(report.doc['status'], 'partial_success')

    def test_cli_partial_success_and_total_failure_exit_codes(self):
        root = Path(__file__).resolve().parents[1]
        for status, command, expected in [('partial_success', 'run', 0), ('partial_success', 'finish', 0), ('failed', 'run', 1)]:
            script = ("import diagnostics as d\n"
                      "class Report:\n"
                      "    def __init__(self): self.doc = {'status': " + repr(status) + "}\n"
                      "    def finish_workflow(self, steps): pass\n"
                      "d.Reporter = Report\n"
                      "d.run_worker = lambda report: None\n"
                      "raise SystemExit(d.cli([" + repr(command) + "]))\n")
            result = subprocess.run([sys.executable, '-c', script], cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            self.assertEqual(result.stdout, 'Success\n' if expected == 0 else 'Failed\n')
            self.assertEqual(result.stderr, '')

    def test_status_method_restored(self):
        report, module, state, called, codes, ids = self.module()
        original = module.Store.status
        d.run_worker(report, module)
        self.assertIs(module.Store.status, original)


if __name__ == '__main__':
    unittest.main()
