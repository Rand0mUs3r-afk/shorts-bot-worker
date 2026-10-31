"""Closer framing, higher-quality encoding, and five-or-ten new clip plans."""
import math
import os
import sys

G = None
BASE_BUILD = None
BASE_PROCESS = None


def clip_limit():
    value = os.getenv('MAX_SHORTS_PER_VIDEO', '5')
    if value not in ('5', '10'):
        raise ValueError('clip_count_must_be_5_or_10')
    return int(value)


def sample_plan(total):
    if not math.isfinite(total) or total < 15:
        raise ValueError('source_too_short')
    usable = math.floor(total * 1000) / 1000
    count = min(clip_limit(), int(usable // 15))
    width, length = usable / count, min(45, usable / count)
    return [{'start_time': round(index * width + (width - length) / 2, 3),
             'end_time': round(index * width + (width + length) / 2, 3),
             'title': f'Video sample {index + 1:02d}',
             'reason': 'Automatically spaced sample; not an AI-ranked highlight.'} for index in range(count)]


def build_plan(client, segments, total, state, source, store):
    limit = clip_limit()
    class ConfiguredClient:
        def generate(self, prompt):
            return client.generate(prompt.replace('Choose up to 10 ', f'Choose up to {limit} ', 1))
    class PlanStore:
        def __getattr__(self, name):
            return getattr(store, name)
        def save(self, current):
            saved = current['sources'].get(source)
            if isinstance(saved, dict) and isinstance(saved.get('moments'), list):
                saved['moments'] = saved['moments'][:limit]
                saved['maximum'] = limit
            return store.save(current)
    selected = BASE_BUILD(ConfiguredClient(), segments, total, state, source, PlanStore())[:limit]
    if not selected:
        raise G.W.Failure('no_clips_generated')
    saved = state['sources'].setdefault(source, {})
    saved.update(moments=selected, maximum=limit)
    G.stats(source).update(selected_count=len(selected), requested_maximum=limit)
    store.save(state)
    return selected


def video_filter():
    return "[0:v:0]split=2[b][f];[b]scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2:flags=lanczos,crop=1080:1920,gblur=sigma=30[bg];[f]scale=1620:2880:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos,crop=w='min(iw,1080)':h='min(ih,1920)'[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1[v]"


def render_clip(media, target, moment):
    expected = moment['end_time'] - moment['start_time']
    G.W.command(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-filter_complex_threads', '1',
        '-ss', str(moment['start_time']), '-i', str(media), '-t', str(expected),
        '-filter_complex', video_filter(), '-map', '[v]', '-map', '0:a:0?',
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '16', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)])
    actual = G.W.duration(target)
    if not 14.9 <= actual <= 180.1 or abs(actual - expected) > 1:
        raise G.W.Failure('render_invalid')


def process(source, yt, client, models, state, store, work):
    original_command = G.W.command
    def command(args):
        args = list(args)
        if args and args[0] == 'yt-dlp' and '-f' in args:
            index = args.index('-f') + 1
            args[index] = args[index].replace('height<=1080', 'height<=1440')
        return original_command(args)
    G.stats(source).update(requested_maximum=clip_limit(), output_width=1080, output_height=1920,
                          foreground_zoom=1.5, crf=16, source_height_limit=1440)
    G.W.command = command
    try:
        return BASE_PROCESS(source, yt, client, models, state, store, work)
    finally:
        G.W.command = original_command


def activate():
    global G, BASE_BUILD, BASE_PROCESS
    clip_limit()
    import generation
    G = generation
    BASE_BUILD, BASE_PROCESS = G.build_plan, G.process
    G.sample_plan, G.build_plan, G.render_clip, G.process = sample_plan, build_plan, render_clip, process
    G.activate()


if __name__ == '__main__':
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
