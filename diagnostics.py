"""Private per-run reporting; no raw errors, credentials or media in reports."""
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPRedirectHandler

STAGES = {'setup_python', 'offline_checks', 'setup_node', 'dependencies', 'state_preflight',
          'legacy_cutover', 'ledger_read', 'credentials', 'ownership', 'download', 'ai_preflight',
          'transcription', 'highlights', 'render', 'upload_reservation', 'upload',
          'upload_checkpoint', 'source_checkpoint', 'complete', 'processing'}
REASONS = {'quotaExceeded', 'dailyLimitExceeded', 'uploadLimitExceeded', 'insufficientPermissions',
           'accessNotConfigured', 'serviceDisabled', 'youtubeSignupRequired', 'forbidden',
           'invalid_grant', 'invalid_client', 'access_denied', 'unauthorized_client'}
CATEGORIES = {'ledger_invalid', 'upload_uncertain', 'queue_invalid', 'state_configuration', 'state_encoding',
              'state_must_be_private', 'disable_legacy_workflows', 'legacy_run_active', 'plan_invalid',
              'legacy_plan_requires_review', 'cached_plan_invalid', 'media_invalid', 'render_invalid',
              'source_incomplete', 'credentials_missing', 'channel_ambiguous', 'channel_mismatch',
              'source_not_owned', 'ai_configuration', 'ai_failed', 'private_reporting_configuration',
              'private_repository_required', 'run_identity', 'invalid_existing_report', 'report_limit'}
STEP_NAMES = {'checkout', 'init', 'python', 'checks', 'node_marker', 'node', 'deps_marker', 'deps', 'process'}


def now():
    return datetime.now(timezone.utc).isoformat()


class DiagnosticError(RuntimeError):
    pass


class MediaError(RuntimeError):
    def __init__(self, category, code=None):
        super().__init__(category)
        self.category, self.exit_code = category, code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def safe_error(exc):
    kind = type(exc).__name__
    result = {'type': kind if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', kind) else 'UnknownError'}
    code = getattr(exc, 'code', None) or getattr(getattr(exc, 'resp', None), 'status', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
    if re.fullmatch(r'[1-5][0-9]{2}', str(code)):
        result['http_status'] = int(code)
    if str(exc) in CATEGORIES:
        result['category'] = str(exc)
    if isinstance(exc, MediaError):
        if exc.category in {'media_failed', 'download_auth_required', 'download_unavailable', 'disk_full', 'media_timeout', 'media_forbidden', 'render_invalid_arguments'}:
            result['category'] = exc.category
        if type(exc.exit_code) is int and -65535 <= exc.exit_code <= 65535:
            result['exit_code'] = exc.exit_code
    try:
        payload = json.loads(exc.content)
    except Exception:
        payload = next((row for row in exc.args if isinstance(row, dict)), {})
    if isinstance(payload, dict):
        error = payload.get('error', {})
        reasons = [error] if isinstance(error, str) else []
        if isinstance(error, dict) and isinstance(error.get('errors'), list):
            reasons.extend(row.get('reason') for row in error['errors'] if isinstance(row, dict))
        accepted = sorted({reason for reason in reasons if isinstance(reason, str) and reason in REASONS})
        if accepted:
            result['reasons'] = accepted
    return result


class Reporter:
    def __init__(self):
        repo, token = os.getenv('PRIVATE_STATE_REPO', ''), os.getenv('PRIVATE_STATE_TOKEN', '')
        run, attempt = os.getenv('GITHUB_RUN_ID', ''), os.getenv('GITHUB_RUN_ATTEMPT', '')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo) or not token or repo == os.getenv('GITHUB_REPOSITORY'):
            raise DiagnosticError('private_reporting_configuration')
        if not re.fullmatch(r'[0-9]{1,24}', run) or not re.fullmatch(r'[0-9]{1,8}', attempt):
            raise DiagnosticError('run_identity')
        self.root, self.token = 'https://api.github.com/repos/' + repo, token
        self.opener = build_opener(NoRedirect())
        metadata = self.api('')
        if metadata.get('private') is not True:
            raise DiagnosticError('private_repository_required')
        self.branch = metadata['default_branch']
        self.path = '/contents/worker-runs/' + run + '-' + attempt + '.json'
        self.sha = None
        self.doc = {'version': 1, 'run_id': run, 'attempt': attempt, 'status': 'running', 'stage': 'processing', 'events': []}
        try:
            row = self.api(self.path + '?ref=' + quote(self.branch, safe=''))
            loaded = json.loads(base64.b64decode(row['content']).decode())
            if loaded.get('version') != 1 or loaded.get('run_id') != run or loaded.get('attempt') != attempt or not isinstance(loaded.get('events'), list):
                raise DiagnosticError('invalid_existing_report')
            self.doc, self.sha = loaded, row['sha']
        except HTTPError as exc:
            if exc.code != 404:
                raise

    def api(self, path, payload=None):
        request = Request(self.root + path, method='GET' if payload is None else 'PUT',
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28'})
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)

    def write(self):
        if self.api('').get('private') is not True:
            raise DiagnosticError('private_repository_required')
        self.doc['updated_at'] = now()
        payload = {'message': 'Update private run diagnostics', 'branch': self.branch,
                   'content': base64.b64encode(json.dumps(self.doc, indent=2, allow_nan=False).encode()).decode()}
        if self.sha:
            payload['sha'] = self.sha
        self.sha = self.api(self.path, payload)['content']['sha']

    def close_event(self, status):
        if self.doc['events'] and self.doc['events'][-1]['status'] == 'running':
            self.doc['events'][-1].update(status=status, ended_at=now())

    def stage(self, name):
        if name not in STAGES:
            raise DiagnosticError('invalid_stage')
        if len(self.doc['events']) >= 1000:
            raise DiagnosticError('report_limit')
        self.close_event('completed')
        event = {'stage': name, 'status': 'running', 'started_at': now()}
        if self.doc.get('source'):
            event['source'] = self.doc['source']
        self.doc['events'].append(event)
        self.doc.update(stage=name, status='running')
        self.write()

    def failure(self, exc):
        self.close_event('failed')
        self.doc.update(status='failed', error=safe_error(exc))
        self.write()

    def success(self):
        self.close_event('completed')
        self.doc.update(status='success', stage='complete')
        self.write()

    def finish_workflow(self, steps):
        allowed = {name: value.get('outcome') for name, value in steps.items()
                   if name in STEP_NAMES and isinstance(value, dict) and value.get('outcome') in {'success', 'failure', 'cancelled', 'skipped'}}
        self.doc['workflow_steps'] = allowed
        failed = next((name for name, outcome in allowed.items() if outcome in {'failure', 'cancelled'}), None)
        if failed:
            if self.doc.get('status') != 'failed':
                self.close_event('failed')
                self.doc.update(status='failed', error={'type': 'WorkflowStepFailed', 'step': failed})
            self.write()
        elif allowed.get('process') == 'success':
            self.success()
        else:
            self.close_event('failed')
            self.doc.update(status='failed', error={'type': 'ProcessingNotCompleted'})
            self.write()


def media_command(args):
    timeout = 900 if args[0] == 'yt-dlp' else 1200 if args[0] == 'ffmpeg' else 90
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaError('media_timeout') from None
    if result.returncode:
        text = result.stderr.lower()
        category = 'media_failed'
        for signature, value in [('no space left', 'disk_full'), ('sign in', 'download_auth_required'),
                                 ('login required', 'download_auth_required'), ('video unavailable', 'download_unavailable'),
                                 ('403', 'media_forbidden'), ('invalid argument', 'render_invalid_arguments')]:
            if signature in text:
                category = value
                break
        raise MediaError(category, result.returncode)
    return result.stdout


def run_worker(report, module=None):
    if module is None:
        import worker as module
    original_setattr, original_process, original_plan, original_command = module.Store.__setattr__, module.process, module.plan, module.command
    def tracked_setattr(store, name, value):
        old = store.__dict__.get('stage')
        original_setattr(store, name, value)
        if name == 'stage' and store.__dict__.get('verified') and not (value == 'highlights' and old == 'transcription'):
            report.stage(value)
    def tracked_process(source, *args, **kwargs):
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', source):
            raise DiagnosticError('invalid_source')
        report.doc['source'] = source
        result = original_process(source, *args, **kwargs)
        report.doc.pop('source', None)
        return result
    def tracked_plan(*args, **kwargs):
        report.stage('highlights')
        return original_plan(*args, **kwargs)
    try:
        module.Store.__setattr__ = tracked_setattr
        module.process, module.plan, module.command = tracked_process, tracked_plan, media_command
        report.stage('state_preflight')
        module.main()
        report.success()
    except Exception as exc:
        try:
            report.failure(exc)
        except Exception:
            pass
        raise
    finally:
        module.Store.__setattr__ = original_setattr
        module.process, module.plan, module.command = original_process, original_plan, original_command


def cli(args):
    output = os.dup(1)
    success = False
    try:
        os.umask(0o077)
        with open(os.devnull, 'w') as sink:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            for name in ('GITHUB_STEP_SUMMARY', 'GITHUB_OUTPUT', 'GITHUB_ENV', 'GITHUB_PATH'):
                os.environ[name] = os.devnull
            report = Reporter()
            if args == ['start']:
                report.stage('setup_python')
            elif len(args) == 2 and args[0] == 'stage':
                report.stage(args[1])
            elif args == ['run']:
                run_worker(report)
            elif args == ['finish']:
                report.finish_workflow(json.loads(os.getenv('WORKFLOW_STEPS', '{}')))
            else:
                raise DiagnosticError('invalid_command')
            success = args != ['finish'] or report.doc.get('status') == 'success'
    except BaseException:
        pass
    finally:
        os.write(output, b'Success\n' if success else b'Failed\n')
        os.close(output)
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(cli(sys.argv[1:]))
