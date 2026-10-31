# Continue after source-specific failures

A failed download (including download_auth_required), media timeout, or recoverable source/render validation error is recorded privately and does not block later selected sources. Failed sources stay in the private queue and are not marked complete. Existing plans and confirmed uploads are preserved. There is no immediate repeat attempt for a failed source within the same run; it may be retried by a later manual run.

Per-run reports now include source_results with complete/failed, the stage, time and sanitized error information. Failed stage events remain failed when processing moves on. The summary includes sources_completed and sources_failed, alongside existing clip/upload counters.

Run outcomes:
- All attempted sources completed: success.
- Some completed and some failed: partial_success; the workflow completes without a processing-step failure.
- Every attempted source failed: failed, after trying all eligible selected sources.
- No pending sources: successful no-op as before.

The finalizer preserves partial_success. The legacy worker_status.json also receives the correct batch outcome. Public processing output stays generic Success/Failed, while details remain in the private repository.

Uncertain uploads, upload/checkpoint errors, loss of private reporting, exhausted disk space and unexpected programming errors still stop the batch. Global authorization/preflight failures also still stop processing. These are not treated as harmless per-video download failures.

The run still selects up to five pending source videos, not an unlimited queue drain. Only remaining sources in that selected batch continue. This change does not fix rejected YouTube cookies, bypass authentication, reset history, alter clip counts/framing, publish videos, or start a run. Start a new manual run on main after committing.
