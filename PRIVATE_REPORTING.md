# Private run reporting

Open the original PRIVATE state repository, then worker-runs. Each run attempt has its own <run-id>-<attempt>.json file. New runs create new records; previous reports remain in Git history.

Reports contain step transitions, timestamps, current source ID in the private repository, sanitized exception class, HTTP status when available, and an allowlisted API reason or media failure category. No raw exception messages, request/response bodies, credentials, cookies, transcripts or video files are written by this reporter.

Setup reporting starts immediately after checkout, using standard-library Python before installing dependencies. A final always-step records setup/test/install/process outcomes. During processing, progress is saved BEFORE each major stage. A report-write failure stops processing instead of continuing without diagnostics. Existing private upload checkpoints and uncertain-upload protections remain unchanged.

The worker uses the existing PRIVATE_STATE_REPO and PRIVATE_STATE_TOKEN secrets; no additional secrets are required. The private token needs Contents read/write and Actions read, scoped to the original private repository. The state repository is verified private before report writes.

Public processing output remains Success or Failed, plus GitHub's platform logs and ordinary step metadata. Reports do not consume a separate private Actions run; they are file updates from the worker.

Limitations: missing/expired/unauthorized private credentials cannot write their own failure report. Checkout failure cannot use the reporter. A hard timeout, force cancellation, runner loss or GitHub API failure can prevent finalization; the last recorded stage is then the best available clue, not proof of the precise failure time. Offline checks do not validate a live OAuth connection or YouTube upload. Old failed runs do not gain reports retroactively. For a test after this commit, start a NEW run on main rather than rerunning an old commit.

The original worker_status.json may still be updated by the pipeline; the authoritative detailed history for this enhancement is worker-runs/<run-id>-<attempt>.json. Keep the original repository private. No automatic publication, media artifacts, visibility change or bot execution is introduced by this commit.
