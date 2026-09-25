# V4.7.0: CardKit restart recovery and 230011 failure-loop fix

- CardKit entity restart recovery: after a sidecar restart the in-memory entity map is lost, so `update` fell back to a plain-JSON PATCH that Feishu rejects for CardKit cards (400/230011 "not a plain JSON card"). The entity is now rebuilt from the card reference stored in the message body (`{"type":"card","data":{"card_id":...}}`), triggered only for `schema == "2.0"` CardKit cards — legacy plain-JSON cards pay no extra lookup.
- 230011 failure-loop termination: Feishu's 230011 "The message was withdrawn" means the target message was recalled, so retries can never succeed. The update loop now stops immediately and callers treat it as a terminal failure, cleaning up session state.
- Patcher tolerance for the extra `_release_turn_marker` if-block added by Hermes 0.21.x: the block is idempotent and does not disturb the ledger contract, so the AST check no longer rejects it on upgrade.
- Version markers (`pyproject.toml`, `config.yaml.example`, `docker-compose.example.yml`, CI workflow, docs) aligned to 4.7.0.

## Recovery bounds

Recovery rebuilds the in-memory entity (card_id + empty card) only; it does not restore streaming state or the execution stack. Messages whose lookup succeeds without a card reference are marked unrecoverable, so later updates skip the lookup. The 128-entity cap is unchanged.

## Validation

The four regression tests (`test_cardkit`, `test_feishu_client`, `test_package_metadata`, `test_docs`) all pass; the full unit suite shows no new failures (60 pre-existing environment failures match HEAD, plus one `test_persistent_service` pre-existing failure fixed in passing).

