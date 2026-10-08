# V4.7.3: bounded heartbeat scans and responsive health snapshots

- **#378 / PR #379:** after a complete inspection verifies an installed source tree while readiness only waits for heartbeats, defer the next full inspection for 300 seconds after completion. Readiness is still checked each cycle; other states invalidate this observation. Cached observations never authorize repair.
- **Health/status:** publish a coherent last-completed diagnostic snapshot under a separate short lock. Requests no longer wait for source inspection or repair. In-flight diagnostic counts may lag; readiness is read independently.
- **#372:** document that explicit rebinding also updates `plan_fingerprint`, that an intermediate `gateway_restart_required` needs a Gateway restart, and that service KeepAlive must not relaunch sidecar during acknowledgement.
- **Tests:** expire a confirmation only after its callback enters, using an explicit test clock. Runtime authorization and expiry behavior are unchanged.

## Upgrade

Upgrade HFC through the original installation entry point, rerun official setup/install, then restart sidecar and Gateway through their existing service owners. This patch does not require upgrading Hermes itself. Preserve configuration, state and installation manifests.

## Verification boundaries

Regressions cover repeated scans, periodic reinspection, readiness transitions, failure/refusal paths and real loopback HTTP while detection remains blocked. Exact-merge CI and package/public-install evidence are recorded in the final Release.

Single-scan CPU cost is unchanged. Source changes are found at the next full inspection, after the five-minute interval plus polling and scan time. Reporter WSL CPU/latency figures have not been reproduced locally. No card layout changes or new mobile visual acceptance are claimed.

## Credits

Thanks to [lanx214](https://github.com/lanx214) for #378 performance evidence and [ywarmy](https://github.com/ywarmy) for #372 field verification and recovery-documentation feedback. Earlier credits remain intact.
