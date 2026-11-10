"""Allowlisted download diagnostics; never publish raw downloader output."""
import importlib.metadata
import os
import re
import subprocess
import sys

REASONS = (
    ("confirm you're not a bot", 'bot_confirmation_required'),
    ('confirm you’re not a bot', 'bot_confirmation_required'),
    ('sign in to confirm your age', 'age_confirmation_required'),
    ('private video', 'private_video'),
    ('members-only', 'members_only'),
    ('not available in your country', 'region_restricted'),
    ('po token', 'po_token_mentioned'),
    ('javascript runtime', 'javascript_runtime_mentioned'),
    ('sign in', 'sign_in_required'),
    ('login required', 'sign_in_required'),
    ('video unavailable', 'video_unavailable'),
    ('403', 'http_403_mentioned'),
)


def details(stderr):
    text = stderr.lower() if isinstance(stderr, str) else ''
    result = {'reason_code': next((code for phrase, code in REASONS if phrase in text), 'unclassified_download_error')}
    try:
        version = importlib.metadata.version('yt-dlp')
    except Exception:
        version = ''
    if re.fullmatch(r'[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]+)?', version):
        result['yt_dlp_version'] = version
    return result


def activate():
    import diagnostics as D
    original_command, original_safe_error = D.media_command, D.safe_error

    def command(args):
        if not args or args[0] != 'yt-dlp':
            return original_command(args)
        try:
            result = subprocess.run(args, text=True, capture_output=True, timeout=900)
        except subprocess.TimeoutExpired:
            exc = D.MediaError('media_timeout')
            exc.download_diagnostics = details('')
            exc.download_diagnostics['reason_code'] = 'download_timeout'
            raise exc from None
        if result.returncode:
            text = result.stderr.lower()
            category = 'media_failed'
            for phrase, value in [('no space left', 'disk_full'), ('sign in', 'download_auth_required'),
                                  ('login required', 'download_auth_required'), ('video unavailable', 'download_unavailable'),
                                  ('403', 'media_forbidden'), ('invalid argument', 'render_invalid_arguments')]:
                if phrase in text:
                    category = value
                    break
            exc = D.MediaError(category, result.returncode)
            exc.download_diagnostics = details(result.stderr)
            raise exc from None
        return result.stdout

    def safe_error(exc):
        result = original_safe_error(exc)
        extra = getattr(exc, 'download_diagnostics', None)
        if isinstance(exc, D.MediaError) and isinstance(extra, dict):
            code = extra.get('reason_code')
            if code in {value for _, value in REASONS} | {'unclassified_download_error', 'download_timeout'}:
                result['download_reason'] = code
            version = extra.get('yt_dlp_version')
            if isinstance(version, str) and re.fullmatch(r'[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]+)?', version):
                result['yt_dlp_version'] = version
        return result

    D.media_command, D.safe_error = command, safe_error


def main():
    output = os.dup(1)
    code = 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            import quality
            quality.activate()
            activate()
            import diagnostics
            code = diagnostics.cli(['run'])
    except BaseException:
        pass
    finally:
        os.write(output, b'Success\n' if code == 0 else b'Failed\n')
        os.close(output)
    return code


if __name__ == '__main__':
    sys.exit(main())
