# V4.4.6 — Installation compatibility and terminal recovery

Accept both verified Hermes attachment-delivery signatures and detect the installed split-ledger hook at runtime. Compatibility CI covers Hermes 0.21.3 source commit `2179a279ae04bfadf8efbc49a01ca0abfb738000`.

Recover exhausted terminal PATCH delivery with one UUID-bound replacement card in the original conversation/topic, retaining the complete answer and suppressing duplicate native text. Failed recovery remains observable in health diagnostics. Interpret explicit iteration/budget exits across legacy and native completion; retain visible partial answers on failure. Preserve the original question, numbered choices and result after interaction completion/expiry, and clarify rejected or uncertain callbacks. Provide a shell-quoted integrity diagnostic command. Update both CodeQL actions together to 4.38.0.

Candidate full CI passed 3605 tests on Python 3.11/3.12 and macOS, and 3594 on Python 3.9/3.10, alongside Windows, PowerShell, Docker, SDK and source-only installer checks. Release requires exact-merge tests, asset checksums and public installer verification. Upgrade through the official installer; after an accepted Hermes upgrade, restart Gateway to load its hooks. Do not clear integrity markers by hand.

CardKit streaming_mode (#293) and durable pause/resume (#295) remain unimplemented. Real mobile first-click behavior and unresolved topic/Docker reports remain open. No real mobile acceptance or production upgrade was performed for this release. See the [issue review](issue-triage-2026-09-15.md) for scope.

Thanks to tidytorch and Jentlezhi for original implementation/test commits, sp960817, Cyber-Yichen, shichenshuo-star and ywarmy for installation evidence, 7360403-coder for terminal-delivery analysis, and mouyong for issue reports. Historical README credits are preserved.
