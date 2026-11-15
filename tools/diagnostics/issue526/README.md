# Issue 526: isolated native entry control

This investigation checks a pinned Hermes v0.23.1 native inbound/admission path
with an ordinarily installed HFC wheel. Feishu input is synthetic; the model and
sidecar are loopback HTTP stubs. It does not establish real Feishu delivery,
client visuals, the PM launcher, or the state of an existing Gateway process.
It is a control experiment, not a reproduction or fix of the reporter incident.
It exercises managed producer hooks and Feishu plugin registration; it does not
validate HFC plugin activation, observer records or startup callback registration.

Run with Python 3.14 and the Hermes SDK dependencies in an isolated environment.
`probe.py --help` lists the paths. `--upstream-root` must be a clean Git checkout
of `3b421578588243f920886e6fe79a6faa87ea026e`. The output must be new or empty.
The probe builds its own managed copy and private HOME, removes inherited keys
and proxies, and only permits loopback connections during execution. It never
patches the original upstream checkout. Do not point it at a production runtime.

Installed-package mode requires `--expected-package-root` and separately verified
wheel provenance. `--hfc-source` is explicitly a development-source control.
Do not combine these two evidence levels.

Expected evidence: two unique DM IDs, real SessionAuthority claims, `handoff`,
completed durable admission rows, and one contiguous event stream starting at 0
with `started`, answer deltas and `completed` for each canonical turn. Supported
`system.notice` events consume their normal sequence positions; both the combined
delta text and final answer must equal the fake model reply. The second input
waits for the first native admission and adapter task to finish, so this does not
exercise busy-input coalescing. The ordinary wheel workflow records package version,
import origin, resource hashes and archive provenance before each run.

Keep failure logs before rerunning. Retain only the acceptance JSON, per-run
result JSON and log when exporting evidence; the private child state contains
temporary transport secrets and must not be uploaded. Remove task-owned copies
and state after the investigation when no longer needed.
