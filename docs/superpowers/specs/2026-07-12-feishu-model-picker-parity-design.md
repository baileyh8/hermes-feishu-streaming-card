# Feishu `/model` Picker Parity Design

## Goal

Make the Feishu `/model` picker mirror the Hermes CLI picker instead of
flattening every provider and model into one long dropdown.

The Hermes-provided `providers` value is the source of truth. The plugin must
not reconstruct availability from `config.yaml`, authentication environment
variables, or auxiliary-model settings.

## User Experience

The interaction has a Provider → Model hierarchy in one Feishu card:

1. The provider view lists the same provider rows Hermes supplied to the CLI
   picker and marks the current provider.
2. Selecting a provider updates the same card to show that provider's models.
3. Selecting a model invokes Hermes' existing `on_model_selected` callback.
4. The model view includes a Back action that restores the provider view.
5. Both views include a Cancel action that closes the interaction without
   changing the active model.

The provider and model order remains the order supplied by Hermes. The current
provider and model are visually identified. Model selection remains a native
Feishu dropdown so large providers remain compact.

## Data Contract

The adapter consumes the `providers` list already passed by Hermes
`list_picker_providers(...)`. Each valid provider row contributes:

- provider slug from `slug` or `provider`;
- display name from `name`, falling back to the slug;
- models from the row's `models` list.
- display count from `total_models`, never lower than the retained model count;
- current marker from `is_current`, with `current_provider` as compatibility fallback.

Invalid rows and blank model identifiers are skipped. Duplicate provider slugs
and duplicate model identifiers are removed while preserving first-seen order.
No auxiliary task configuration is read or merged.

This keeps Feishu synchronized with Hermes when upstream adds, removes, or
reorders providers and models.

## Card State

The native picker state stored on the adapter includes:

- `providers`: a sanitized copy of the provider tree;
- `current_provider` and `current_model`;
- `view`: `providers` or `models`;
- `selected_provider` when the model view is active;
- existing chat, session, message, and callback fields.

Provider selection and Back update the existing message in place. They do not
resolve or remove picker state. Model selection resolves the picker through the
existing Hermes callback and then removes the state. Cancel removes the state
and updates the card to a non-interactive cancelled result.

## Compatibility And Failure Handling

- If Hermes supplies exactly one valid provider, the card may open directly on
  that provider's model view while retaining Back only when there is another
  provider to return to.
- If the native WebSocket card path is unavailable, the existing sidecar/text
  fallback remains fail-open.
- If provider navigation cannot update the card, the action returns an error
  response and preserves picker state so the user can retry.
- If the provider tree has no valid choices, the existing `no model options`
  failure remains unchanged.
- Older Hermes versions remain supported because the plugin only relies on the
  provider rows already accepted by the existing flat picker.
- Feishu element limits are enforced per view. A provider or model list that
  exceeds the native dropdown limit is truncated in display only, with a note
  directing users to `/model <model>` for an exact switch. The underlying
  Hermes callback contract is unchanged.

## Security And Privacy

The card contains provider names, model identifiers, non-sensitive
`total_models` / `is_current` display metadata, and opaque picker IDs only. It
never serializes API keys, provider credentials, base URLs, local paths, or raw
Hermes configuration.

Existing private-chat and group-chat ownership checks continue to guard every
card action. Navigation actions use the same picker ID and authorization path
as final model selection.

## Test Matrix

Unit and integration coverage must prove:

- provider options match the supplied provider order and current marker;
- selecting a provider renders only that provider's models;
- Back restores the provider view without resolving the picker;
- Cancel resolves without calling `on_model_selected`;
- selecting a model calls `on_model_selected` with the original provider slug
  and model ID exactly once;
- duplicate and malformed rows are safely normalized;
- single-provider, truncation, update-error, and non-native fallback paths;
- existing WebSocket ownership and stale-handler behavior remain intact.

Real Feishu acceptance must run `/model`, compare provider/model counts with the
CLI picker, navigate into at least one provider, return once, select a model,
and verify the active model changed without duplicate gray text.
