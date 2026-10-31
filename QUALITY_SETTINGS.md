# Framing, quality and clip count

New manual runs enter through quality.py. The Run workflow form offers max_shorts values 5 (default) and 10 (hard ceiling) for NEW source plans. Both AI requests and fallback sampling use the chosen cap; AI may return fewer usable highlights. Counts are limited before checkpointing the new plan, including the first save. Existing nonempty saved plans retain their clip identities and size, up to the existing ten-clip limit, to preserve upload/resume history. No uploaded clips or completed sources are regenerated solely for this visual change.

Output stays 1080x1920, vertical 9:16 MP4. Foreground scaling changes from 1350x2400 to 1620x2880: approximately 20 percent larger than the previous foreground. Framing stays centered, not subject-tracked; more of the edges may be cropped. The blurred background still fills the frame.

Both scaling paths use Lanczos. H.264 changes from CRF 18 / medium to CRF 16 / fast; AAC audio targets 192 kbit/s when audio exists. Source downloads prefer available video up to 1440p rather than 1080p. Existing source frame rate is retained. Encoding cannot recover detail absent from the source. Larger downloads and higher-bitrate outputs can use more bandwidth, disk space and processing; a visual improvement is not guaranteed for already-blurry footage.

AI highlights remain 15-180 seconds; labelled fallback samples remain 15-45 seconds. Uploads stay private. Source links, credentials and detailed reporting remain outside public workflow inputs/logs. Only the harmless clip-count choice is added to the Run workflow form.

Start a NEW run on main to apply this change. It does not start processing, change visibility, clear history, or replace existing YouTube uploads. No additional secrets are needed.
