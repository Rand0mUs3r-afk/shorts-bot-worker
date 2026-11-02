# Source videos from other channels

The source uploader no longer has to match the authenticated destination channel. Put valid YouTube video links from any channel in the existing private queue. Only process content you own, are licensed to use, or have permission to edit and republish; accepting a URL is not a determination of reuse rights.

The batch-wide source videos.list/ownership rejection is removed. The downloader checks actual access to each source. A private, deleted, login-restricted, age-restricted, region-restricted, or otherwise inaccessible video is not made accessible by this change. It does not bypass access controls or guarantee all YouTube downloads. Existing source-failure continuation remains in effect for recoverable download errors.

OAuth still identifies your destination channel, and the existing upload ledger must match that destination. External source links do not change which channel receives the Shorts. Uploads remain private. Credentials/cookies, queue storage, private diagnostics, five-default/ten-maximum new clip plans, framing and resume protection are unchanged. Never add another person's session cookies or credentials without authorization.

Start a NEW manual run on main after the commit. No extra secret or source-channel login is required merely to pass the removed ownership check; cookies may still be required by YouTube for actual access. The commit does not start a run, edit the private queue/ledger, replace existing uploads, or change repository visibility.

Regression tests use mocked Google authorization and processing; they do not certify live access or the rights to any source video.
