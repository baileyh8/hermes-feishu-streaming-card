# V5.0.5: newer Hermes admission and configuration-writer compatibility

This release addresses the source compatibility boundaries reported in [#451](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/451) and includes [PR #448](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/448), [PR #449](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/449) and [PR #452](https://github.com/baileyh8/hermes-feishu-streaming-card/pull/452). Its interval starts after v5.0.4. Earlier commits, tags and contributor credits remain intact.

## Changes

- **Native admission completion:** verify complete `session_ingress.py` / `session_authority.py` source cohorts, loaded code and the Base extraction seam before accepting a legitimately reconstructed source for the same Hermes admission. Full route, owner, generation, epoch and original execution handle must still match. Completion handoff is single use; duplicate, late, cross-identity and terminal-failure paths retain their boundaries.
- **Compatibility before card admission:** when a current admission first chooses card policy and the same identity has no pinned decision, the existing policy boundary checks every `SUPPORTED_RUNTIME_EVENTS` entry, including notice/interaction first events. Unknown admission sources remain native. Detection reports admission completion separately. Ordinary contexts pinned before admission, independent official-plugin owners and delivery/terminal-failure behavior after ownership are outside this change.
- **Official configuration writers:** add source families reviewed through their call chains and indirect serializers, retaining earlier families. Plan exact config bytes in a private HOME before official enable. Reject unknown sources and drift. PM A-to-B rebuilds check ordinary-wheel provenance, while recovery verifies exact original config and managed source bytes.
- **Complete CI isolation and balancing:** include #448/#449 conservative scope selection, duration-balanced shards and exact coverage verification. Upstream workers create independent ordinary-wheel prefixes, avoiding sensor/`.pth` contention and an oversized 33-case shared bundle. Timing hints record actual JUnit results and explicitly identified estimates. Retain Linux Python 3.9–3.14, macOS, Windows, SDK, Docker, CodeQL, all required checks and the 25-minute job timeout. Missing wheels, failed environment preparation or missing reports still block. The asynchronous recheck test waits for diagnosis and publication before closing its client, retaining fingerprint and single-delivery assertions.

## Pinned sources and upgrading

Support is bound to these real sources. A version string does not prove compatibility with every later main revision.

| Source | Pinned commit |
| --- | --- |
| cf4 reported in #451 | `cf4f3a7396814ffcaef69d9af09f7cfdade3ddbe` |
| rc.2-v0.22.3 | `f83b4df3ce1ab2ec62f1e0e90f22e0a1db21cb68` |
| v0.22.4 | `4d04303818b61bf3aa09278af48356315515965b` |
| main observed 2026-10-22 | `4e58c721c8cf9b51c46636ea84ea6a5cb540880a` |

Retain the earlier `5fc490e` admission source and independent d923 v0.22.2 / 0ab main install, writer and rate baselines. Mixed source hashes, unknown loaded topology, missing seams or identity mismatches do not qualify for completion ownership. Accepted paths retain their delivery owner and terminal-failure rules. This does not establish that every unknown version or plugin creates zero cards.

After `v5.0.5` is published, upgrade through the official installer with the existing config, env, Hermes HOME, profile and service owners. Restart the corresponding processes as instructed. See the [upgrade guide](upgrade-v4-to-v5.en.md). Display defaults, card layout and Hermes execution/authorization ownership retain their behavior.

## Verification and remaining limits

The repair candidate has separate evidence for pinned-source install, repeat install, doctor, byte restoration, execution of original patched ledger methods, configuration-writer comparison and real PM rebuilds. A concurrent ordinary-wheel probe verifies two distinct prefixes/sites, sensor/`.pth` ownership, non-editable provenance, archive SHA and 141 resource bytes, while checking that the borrowed environment remains unchanged. Each result establishes its own boundary, not a real business turn.

Record exact v5.0.5 commit CI / CodeQL, real Feishu API, ordinary packages, public tagged installation and asset/checksum results separately in [release readiness](release-readiness.en.md). Actual results belong to the exact-version [v5.0.5 GitHub Release acceptance record](https://github.com/baileyh8/hermes-feishu-streaming-card/releases/tag/v5.0.5). Adjacent commits or checks from before version preparation do not replace the final release gate.

Actual Gateway/model execution, the reporter's tool-heavy environment and desktop/Android visual/click acceptance remain unverified. iOS and new-user trials are outside this scope. Passing synthetic multi-tool cases does not establish that the original #451 environment was retested. Earlier #344/#361 uncertainty remains open.

## Credits

Repairs and CI code in this release interval are maintained by [baileyh8](https://github.com/baileyh8), matching the actual PR #448/#449/#452 and Git authors. Thanks to [coytus](https://github.com/coytus) for #451 field evidence and [Lite-G](https://github.com/Lite-G) for source analysis. These are evidence and analysis credits, separate from this repair's code authorship. Earlier credits in both READMEs and historical release notes remain intact.
