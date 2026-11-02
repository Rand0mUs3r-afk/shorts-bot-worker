# Six clip-count options

The manual max_shorts dropdown now offers 5, 10, 15, 20, 25 and 30 per NEW source, with 5 still the default. Input validation, AI prompts, candidate selection, fallback sample counts and resumed-plan validation support the same range. The active AI selector no longer stops at ten candidates. Cached highlight chunks include the chosen maximum in their identity, so a partial five-clip request is not mistaken for a thirty-clip request.

This is a maximum, not a promise of that many suitable highlights. Fewer nonoverlapping clips may be available, and short sources cannot provide thirty 15-second samples. Existing nonempty saved plans resume unchanged (up to thirty clips) even if a different option is selected. Completed sources and previous uploads are not regenerated or reset. The new selection applies when creating a new source plan.

Larger plans require more processing and upload quota. The existing 330-minute job timeout and provider/channel limits still apply; choosing thirty does not guarantee a five-source batch will finish in one run. Durable per-clip upload checkpoints remain active for later resume.

Framing, quality, private upload visibility, cross-channel sources, private queue, diagnostics and batch continuation remain unchanged. Start a new manual run on main after this commit. The commit itself starts no run and changes no secrets or upload history.
