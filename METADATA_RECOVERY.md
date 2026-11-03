# Metadata recovery without placeholder uploads

The previous metadata path discarded provider errors, rejected slightly overlong output, and could reject every numbered fallback clip after rendering. This update keeps the requested story-only title/description policy while adding a non-network fallback from actual clip speech.

Order: Gemini with available frames/speech; Groq with speech; a short verbatim dialogue hook from the clip; existing descriptive metadata if valid. The local fallback does not invent visual action, names or outcomes. It may use dialogue as the title and description rather than a third-person scene summary. If there is no usable speech and providers cannot describe the images, the source remains pending rather than inventing a story or uploading Sample 01.

When source transcript segments do not fit inside the selected clip, the rendered clip can be transcribed locally with the already-installed Whisper library. The extra model instance is reused during the run. This may add processing time; no new secret is required.

Provider JSON can be a single object or one-item array; common description aliases are accepted, and overlong fields are shortened at word boundaries before strict validation. Groq metadata requests use a 4096-token ceiling and low reasoning effort for GPT-OSS models. This is not a diagnosis that token truncation caused the previous failure.

Private source_stats now includes metadata_method, metadata_stage, speech-character count, frame count and up to eight sanitized metadata_failures with provider, exception type and HTTP status when available. It never stores raw exception text, response bodies, keys or cookies. These fields distinguish provider trouble from text validation. The outer recorded stage is still render because metadata is prepared within the render function; metadata_status describes the substep.

The upload body still has no bot reference, AI-production wording, source URL, numbered sample title, hashtag or promotional filler. Metadata is saved BEFORE the upload reservation. Existing uploads, timestamps, identifiers, clip counts and visibility remain unchanged. No history reset, live run, or existing YouTube edit is included in this code commit. Start a new manual run on main to apply it.
