import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import diagnostics as d


class ReportTests(unittest.TestCase):
    def report(self):
        report = object.__new__(d.Reporter)
        report.doc = {'events': [], 'status': 'running', 'stage': 'processing'}
        report.write = Mock()
        return report

    def test_transitions(self):
        report = self.report()
        report.stage('download')
        report.stage('transcription')
        self.assertEqual([row['status'] for row in report.doc['events']], ['completed', 'running'])
        self.assertEqual(report.doc['stage'], 'transcription')

    def test_failure_keeps_stage(self):
        report = self.report()
        report.stage('render')
        report.failure(ValueError('secret-value'))
        self.assertEqual(report.doc['status'], 'failed')
        self.assertEqual(report.doc['stage'], 'render')
        self.assertNotIn('secret-value', json.dumps(report.doc))

    def test_no_raw_exception_details(self):
        exc = RuntimeError('token=private-cookie https://private-video')
        self.assertEqual(d.safe_error(exc), {'type': 'RuntimeError'})

    def test_allowlisted_api_reason(self):
        exc = RuntimeError('private-value')
        exc.content = json.dumps({'error': {'errors': [{'reason': 'quotaExceeded'}, {'reason': 'SECRET'}]}})
        self.assertEqual(d.safe_error(exc)['reasons'], ['quotaExceeded'])

    def test_oauth_error(self):
        exc = RuntimeError('secret', {'error': 'invalid_grant', 'error_description': 'PRIVATE'})
        self.assertEqual(d.safe_error(exc)['reasons'], ['invalid_grant'])
        self.assertNotIn('PRIVATE', json.dumps(d.safe_error(exc)))

    def test_safe_category(self):
        self.assertEqual(d.safe_error(RuntimeError('disable_legacy_workflows'))['category'], 'disable_legacy_workflows')

    def test_http_code(self):
        exc = RuntimeError('private')
        exc.resp = types.SimpleNamespace(status=403)
        self.assertEqual(d.safe_error(exc)['http_status'], 403)

    def test_step_failure(self):
        report = self.report()
        report.stage('dependencies')
        report.finish_workflow({'deps': {'outcome': 'failure'}, 'process': {'outcome': 'skipped'}})
        self.assertEqual(report.doc['error']['step'], 'deps')
        self.assertEqual(report.doc['status'], 'failed')

    def test_finalizer_preserves_processing_failure(self):
        report = self.report()
        report.stage('upload')
        report.failure(ValueError('hidden'))
        report.finish_workflow({'process': {'outcome': 'failure'}})
        self.assertEqual(report.doc['error']['type'], 'ValueError')
        self.assertEqual(report.doc['stage'], 'upload')

    def test_write_failure_blocks_stage(self):
        report = self.report()
        report.write.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            report.stage('upload')

    def test_public_destination_rejected(self):
        env = {'PRIVATE_STATE_REPO': 'owner/state', 'PRIVATE_STATE_TOKEN': 'test-only', 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        with patch.dict(os.environ, env), patch.object(d.Reporter, 'api', return_value={'private': False}):
            with self.assertRaises(d.DiagnosticError):
                d.Reporter()

    def test_invalid_stage_rejected(self):
        with self.assertRaises(d.DiagnosticError):
            self.report().stage('secret-string')

    def test_media_details_discarded(self):
        result = types.SimpleNamespace(returncode=1, stderr='Sign in required SECRET_COOKIE source-link', stdout='')
        with patch.object(d.subprocess, 'run', return_value=result):
            with self.assertRaises(d.MediaError) as caught:
                d.media_command(['yt-dlp', 'private-url'])
        error = d.safe_error(caught.exception)
        self.assertEqual(error['category'], 'download_auth_required')
        self.assertNotIn('SECRET_COOKIE', json.dumps(error))

    def test_write_uses_cas_and_private_check(self):
        report = object.__new__(d.Reporter)
        report.doc, report.branch, report.path, report.sha = {'events': []}, 'main', '/contents/report.json', 'old'
        report.api = Mock(side_effect=[{'private': True}, {'content': {'sha': 'new'}}])
        report.write()
        self.assertEqual(report.api.call_args.args[1]['sha'], 'old')
        self.assertEqual(report.sha, 'new')

    def test_silent_cli_on_configuration_failure(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env.pop('PRIVATE_STATE_TOKEN', None)
        result = subprocess.run([sys.executable, 'diagnostics.py', 'start'], cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (1, 'Failed\n', ''))

    def test_instrumentation_restored_after_failure(self):
        class Store:
            pass
        def fail():
            instance = Store()
            instance.verified = True
            instance.stage = 'credentials'
            raise ValueError('PRIVATE')
        module = types.SimpleNamespace(Store=Store, main=fail, process=lambda *a: None, plan=lambda *a: None, command=lambda *a: '')
        original = Store.__setattr__
        report = self.report()
        with self.assertRaises(ValueError):
            d.run_worker(report, module)
        self.assertIs(Store.__setattr__, original)
        self.assertEqual(report.doc['stage'], 'credentials')
        self.assertNotIn('PRIVATE', json.dumps(report.doc))


if __name__ == '__main__':
    unittest.main()
