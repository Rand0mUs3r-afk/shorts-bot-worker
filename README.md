# Private processing worker

Staging only: keep this repository private until setup and a private end-to-end test pass.
No credentials or processing data belong in this repository. No scheduled, push, or pull-request workflow is defined.

## Behavior
- Manually selects up to five pending sources from the private queue, with up to ten Shorts per new source.
- Requires sources owned by the authenticated YouTube channel.
- Uploads private review copies only. No automatic publication or publication scheduling.
- Keeps the original queue and upload ledger in the existing private repository. Does not delete queue entries.
- Uses conditional SHA updates for checkpoints. Conflicts stop processing rather than overwrite history.
- Preserves uploaded entries and clip keys. An uncertain uploading entry blocks the next run; reconcile it in YouTube Studio before retrying. Never reset the ledger to bypass this check.
- Saved plans larger than ten clips stop for private review instead of being silently truncated.
- Renders temporary media, uploads it, then removes it. No artifacts, summaries, diagnostic media, or workflow caches are published.
- Bot output is only Success or Failed. GitHub still exposes platform logs, timings, step names and status.
- Private worker_status.json records a bounded stage/status, not raw exceptions. A hard timeout may prevent that update.
- Gemini/Groq receive transcript chunks. GitHub, Google, AI providers and trusted dependencies remain part of the trust boundary.

## One-time cutover
1. Keep the original shorts-bot repository private. In its Actions UI, disable Generate Shorts and Process Shorts Queue (private). Wait for every old processing run to finish or cancel it safely. Check for uncertain uploads.
2. In this worker's Actions secrets, configure PRIVATE_STATE_REPO with the original owner/repository name, and PRIVATE_STATE_TOKEN with a fine-grained GitHub token restricted to that repository: Contents read/write and Actions read. The built-in GITHUB_TOKEN cannot access another private repository.
3. Configure YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN, optionally YOUTUBE_COOKIES, and GEMINI_API_KEY and/or GROQ_API_KEY directly in GitHub Secrets. Do not paste them in chat or commit them. Existing repository secrets are not automatically copied.
4. The private repository must already contain its valid shorts_uploads.json and youtube_queue.txt. Missing files fail closed. Add source links only to that private queue.
5. Run the manual worker privately with one source first. Confirm the private upload and private ledger; inspect worker_status.json if it fails. Then test a five-source batch.
6. Only after review, decide whether to change this clean worker repository to public. Leave the state repository private. No previous code history, logs, artifacts or data are migrated here.

Actions are pinned to resolved commit SHAs; Python dependencies are not yet fully version/hash locked. This is not a completed supply-chain audit or a guarantee against compromised dependencies. Do not enable debug logging, run untrusted code with secrets, cache working data, or add artifact uploads. Public code reveals the general processing architecture, not private source links or credentials.

Run offline tests with: python -m unittest discover -s tests

The connector cannot copy existing secret values or enable this deployment automatically. This code commit does not disable old workflows or change repository visibility. Complete the cutover steps before attempting processing. Never use your Google password as a repository secret.
