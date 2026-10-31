"""Generate nonempty clip plans, preserve uploads, and report actual output."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

W = None
BASE_PLAN = None
STATS = {}


def stats(source):
    return STATS.setdefault(source, {'selected_count': 0, 'uploaded_this_run': 0, 'already_uploaded': 0})


def select_sources(text, state):
    result, seen = [], set()
    for line in text.lstrip('\ufeff').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        for token in line.split(','):
            if not token.strip():
                continue
            source = W.source_id(token)
            if source in seen:
                continue
            seen.add(source)
            if state['sources'].get(source) == 'complete':
                if any(row.get('source') == source for row in state['uploads'].values()):
                    continue
                state['sources'].pop(source)
                stats(source)['reopened_empty_completion'] = True
            result.append(source)
            stats(source)['queued'] = True
            if len(result) == 5:
                return result
    return result


def sample_plan(total):
    if not math.isfinite(total) or total < 15:
        raise ValueError('source_too_short')
    usable = math.floor(total * 1000) / 1000
    count = min(10, int(usable // 15))
    width = usable / count
    length = min(45, width)
    return [{'start_time': round(index * width + (width - length) / 2, 3),
             'end_time': round(index * width + (width + length) / 2, 3),
             'title': f'Video sample {index + 1:02d}',
             'reason': 'Automatically spaced sample; not an AI-ranked highlight.'} for index in range(count)]


def transcribe(media, models, source):
    row = stats(source)
    if not row['has_audio']:
        row.update(transcript_segments=0, transcription_mode='no_audio')
        return []
    try:
        if not models:
            from faster_whisper import WhisperModel
            models.append(WhisperModel('small', device='cpu', compute_type='int8'))
        for vad in (True, False):
            segments, _ = models[0].transcribe(str(media), vad_filter=vad, beam_size=5)
            segments = [item for item in segments if item.text.strip() and math.isfinite(item.start) and math.isfinite(item.end) and item.end > item.start]
            row.update(transcript_segments=len(segments), transcription_mode='vad' if vad else 'retry_without_vad')
            if segments:
                row['speech_seconds'] = round(sum(item.end - item.start for item in segments), 3)
                return segments
    except Exception as exc:
        kind = type(exc).__name__
        row['transcription_error_type'] = kind if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', kind) else 'UnknownError'
    row.update(transcript_segments=0, speech_seconds=0, transcription_mode='sample_fallback')
    return []


def build_plan(client, segments, total, state, source, store):
    row = stats(source)
    row['ai_response_items'] = 0
    class CountedClient:
        def generate(self, prompt):
            response = client.generate(prompt)
            if isinstance(response, list):
                row['ai_response_items'] += len(response)
            return response
    selected = []
    if segments:
        try:
            selected = BASE_PLAN(CountedClient(), segments, total, state, source, store)
        except Exception as exc:
            # Never hide checkpoint failures or unexpected programming errors.
            if not ((isinstance(exc, W.Failure) and str(exc) == 'plan_invalid') or (type(exc) is RuntimeError and str(exc) == 'ai_failed')):
                raise
            kind = type(exc).__name__
            row['selection_error_type'] = kind if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', kind) else 'UnknownError'
            row['fallback_reason'] = 'ai_selection_error'
    if selected:
        row.update(selection_method='ai', selected_count=len(selected))
        return selected
    row.setdefault('fallback_reason', 'empty_transcript' if not segments else 'no_ai_highlights')
    selected = W.moments(sample_plan(total), total)
    if not selected:
        raise W.Failure('no_clips_generated')
    state['sources'][source] = {'moments': selected, 'maximum': 10, 'selection_method': 'spaced_samples'}
    row.update(selection_method='spaced_samples', selected_count=len(selected))
    store.save(state)
    return selected


def render_clip(media, target, moment):
    vf = "[0:v:0]split=2[b][f];[b]scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2,crop=1080:1920,gblur=sigma=30[bg];[f]scale=1350:2400:force_original_aspect_ratio=decrease:force_divisible_by=2,crop=w='min(iw,1080)':h='min(ih,1920)'[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1[v]"
    expected = moment['end_time'] - moment['start_time']
    W.command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1',
               '-ss', str(moment['start_time']), '-i', str(media), '-t', str(expected), '-filter_complex', vf,
               '-map', '[v]', '-map', '0:a:0?', '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
               '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(target)])
    actual = W.duration(target)
    if not 14.9 <= actual <= 180.1 or abs(actual - expected) > 1:
        raise W.Failure('render_invalid')


def upload_clip(yt, target, body):
    from googleapiclient.http import MediaFileUpload
    request = yt.videos().insert(part='snippet,status', body=body,
        media_body=MediaFileUpload(str(target), mimetype='video/mp4', chunksize=8 * 1024 * 1024, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk(num_retries=5)
    identifier = response.get('id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', identifier):
        raise W.Failure('upload_uncertain')
    return identifier


def process(source, yt, client, models, state, store, work):
    row = stats(source)
    media, target = work / 'source.mp4', work / 'clip.mp4'
    try:
        store.stage = 'download'
        args = ['yt-dlp', '--ignore-config', '--no-cache-dir', '--no-playlist', '--no-progress',
                '--js-runtimes', 'node', '-f', 'bv*[height<=1080]+ba/b[height<=1080]',
                '--merge-output-format', 'mp4', '--remux-video', 'mp4', '-o', str(media)]
        if os.getenv('YOUTUBE_COOKIES'):
            args += ['--cookies', str(work / 'cookies.txt')]
        W.command(args + ['https://www.youtube.com/watch?v=' + source])
        total = W.duration(media)
        row['source_duration_seconds'] = round(total, 3)
        if total < 15:
            row['outcome'] = 'source_too_short'
            raise W.Failure('source_too_short')
        probe = json.loads(W.command(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'json', str(media)]))
        kinds = {item.get('codec_type') for item in probe.get('streams', [])}
        row['has_audio'] = 'audio' in kinds
        if 'video' not in kinds:
            raise W.Failure('video_stream_missing')
        saved = state['sources'].get(source)
        if isinstance(saved, dict) and saved.get('moments'):
            selected = W.saved_plan(saved, total)
            row.update(selected_count=len(selected), selection_method=saved.get('selection_method', 'saved_plan'))
        else:
            store.stage = 'transcription'
            segments = transcribe(media, models, source)
            # Empty legacy plans must not bypass generation on a retry.
            if isinstance(saved, dict) and 'moments' in saved:
                state['sources'][source] = {}
            store.stage = 'highlights'
            selected = W.plan(client, segments, total, state, source, store)
        if not selected:
            row['outcome'] = 'no_clips_generated'
            raise W.Failure('no_clips_generated')
        for moment in selected:
            key = W.key_for(source, moment)
            if state['uploads'].get(key, {}).get('status') == 'uploaded':
                row['already_uploaded'] += 1
                continue
            store.stage = 'render'
            render_clip(media, target, moment)
            store.stage = 'upload_reservation'
            state['uploads'][key] = {'source': source, 'status': 'uploading', 'upload_mode': 'private', 'title': moment['title']}
            store.save(state)
            store.stage = 'upload'
            identifier = upload_clip(yt, target, W.private_body(moment, key))
            state['uploads'][key].update(status='uploaded', youtube_id=identifier)
            store.stage = 'upload_checkpoint'
            store.save(state)
            row['uploaded_this_run'] += 1
            target.unlink(missing_ok=True)
        if any(state['uploads'].get(W.key_for(source, moment), {}).get('status') != 'uploaded' for moment in selected):
            raise W.Failure('source_incomplete')
        state['sources'][source] = 'complete'
        row['outcome'] = 'uploaded' if row['uploaded_this_run'] else 'already_uploaded'
        store.stage = 'source_checkpoint'
        store.save(state)
    finally:
        media.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def activate():
    global W, BASE_PLAN
    import worker
    import diagnostics
    W, BASE_PLAN = worker, worker.plan
    STATS.clear()
    worker.select_sources, worker.plan, worker.process = select_sources, build_plan, process
    original_stage = diagnostics.Reporter.stage
    original_success = diagnostics.Reporter.success
    def with_counts(report, name):
        report.doc['source_stats'] = STATS
        return original_stage(report, name)
    def finish(report):
        report.doc['source_stats'] = STATS
        report.doc['summary'] = {'sources_selected': len(STATS),
            'clips_selected': sum(row.get('selected_count', 0) for row in STATS.values()),
            'uploaded_this_run': sum(row.get('uploaded_this_run', 0) for row in STATS.values()),
            'already_uploaded': sum(row.get('already_uploaded', 0) for row in STATS.values()),
            'outcome': 'processed' if STATS else 'no_pending_sources'}
        return original_success(report)
    diagnostics.Reporter.stage, diagnostics.Reporter.success = with_counts, finish


if __name__ == '__main__':
    # Suppress import-time messages before the existing diagnostics entry point.
    output = os.dup(1)
    code = 1
    try:
        with open(os.devnull, 'w') as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            activate()
            import diagnostics
            code = diagnostics.cli(['run'])
    except BaseException:
        pass
    finally:
        os.write(output, b'Success\n' if code == 0 else b'Failed\n')
        os.close(output)
    sys.exit(code)
