# Nonempty clip generation

The manual workflow now enters through generation.py, which installs the generation functions before the existing private diagnostics wrapper starts the worker. Authentication, source-ownership checks, private-only uploads, and durable upload reservations remain in use.

A queued source marked complete with NO associated upload records is reopened in memory and persisted by the ordinary private checkpoint path after authorization. Sources with any associated upload record are not reset. The whole upload ledger is never cleared.

The source is downloaded and its duration and audio/video streams checked. Transcription retries without VAD if the first pass is empty. If audio is missing, transcription cannot run, the AI is unavailable, or AI selection is empty, the bot makes up to ten nonoverlapping, evenly spaced samples of up to 45 seconds (minimum 15 seconds). Sample titles and descriptions explicitly say these are samples, not AI-ranked highlights. This fallback produces usable review clips; it does not claim to understand visual action or guarantee engaging moments. Review private uploads before publishing.

Nonempty saved plans and uploaded clip IDs are reused. Empty legacy plans regenerate. A source cannot become complete unless it has a nonempty plan and every planned clip has a confirmed upload record. Invalid/too-short media or upload/API errors still fail rather than claim success. The redundant dummy AI connectivity request before download is removed.

Private per-run reports gain source_stats with duration, audio presence, transcript segment counts, transcription mode, selected count, selection method, new uploads and existing uploads. Successful runs also include a numeric summary. Existing setup reporting remains active.

Start a NEW manual run on main after this commit; rerunning an old run uses old code. Keep the test source in the private queue. No new secret, repository visibility change, data deletion, or processing run is performed by the code commit itself. Retry recovery happens only when you run the worker. Offline tests use simulated providers/uploads and do not prove live YouTube authorization or quota availability.
