# V4.0.1

V4.0.1 fixes duplicate native answer text after completed cards with image or file output while preserving Hermes native media delivery.

## Fixes

- Fixes [issue #106](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/106): after a card succeeds, explicit `MEDIA:` and local output paths are passed to Hermes as media-only native delivery without the answer text already shown in the card.
- Completed cards no longer expose internal paths such as `MEDIA:/opt/data/...`; attachment summaries and native image/file delivery remain available.
- Normal completion and queued follow-up completion use the same media/text split.

## Compatibility and fallback

- A failed card delivery preserves the complete Hermes native response so neither the answer nor media is lost.
- Non-Feishu platforms are not rewritten.
- Structured media without an explicit delivery path keeps its existing native-delivery behavior.
- The installer recognizes and upgrades V4.0.0 completion hooks instead of reporting the previous valid block as corrupt markers.

## Credits

- Thanks to @ShakuOvO for reporting #106.
- Thanks to @blakejia for independently confirming it on Hermes `0.18.2`.

## Verification

- Hot-path matrix: `509 passed`.
- Full suite: `1257 passed, 3 skipped`; `git diff --check` passed.
- Local package smoke: the sdist and wheel built successfully, and a clean venv imported version `4.0.1`.
- Hermes `extract_media()` data-flow check preserved the media path with an empty native-visible text body.

## Release assets

- `hermes-feishu-card-v4.0.1-macos.tar.gz`
- `hermes-feishu-card-v4.0.1-linux.tar.gz`
- `hermes-feishu-card-v4.0.1-windows.zip`
- `hermes-feishu-card-v4.0.1-checksums.txt`
