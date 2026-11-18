# Docker requirements (Engine and Compose)

**Minimum: Docker Engine 24.0 and Docker Compose (v2 plugin, `docker compose`) 2.20.** Tested range: Engine 24.0.9 - 29.1.5, Compose 2.20.3 - 2.36.2
(`reports/evidence/docker_version_matrix.log`). Older versions are **not supported** (they may work, nothing was verified below these
numbers). Nothing else is needed on the host except `make` (optional: every script can be called directly) and, for `scripts/os_fetch_buildroot.sh`, `git`.

Deutsch: Benötigt werden Docker Engine ab 24.0 und Docker Compose (v2-Plugin, `docker compose`) ab 2.20. Die Prüfung vor dem Start nennt gefundene und
benötigte Version und die Abhilfe; `make doctor` führt nur diese Prüfung aus.

## Why this changed (owner's test, 2026-11-18)
`make demo` failed on Engine 25.0.2 + Compose 2.24.5 with `services.gateway.networks.edge Additional property gw_priority is not allowed` and worked
after upgrading to Engine 29.10.0 + Compose 5.7.2. The only cause was `gw_priority` in `demo/compose.yml`, which was used to make Docker pick the
non-internal network `edge` as the gateway's default route (the route Docker publishes ports on, DECISIONS #71).

**`gw_priority` is not needed and was removed.** The gateway sits on two networks: `demo` (internal, no host address) and `edge` (normal).
Docker prefers a non-internal network for the default route on every engine tested, so the published ports and the isolation are identical
with and without it. Evidence (`tests/entry/docker_matrix/topology_probe.sh`, in disposable Docker-in-Docker sandboxes, without `gw_priority`):
on Engine 24.0.9, 25.0.5, 26.1.4, 27.5.1, 28.0.4 and 29.1.5 the gateway's default route is on `edge`, the port published to 127.0.0.1 is
reachable, the host has no IPv4 address on the internal bridge, services on `demo` cannot reach a decoy "Lyrion" listening on the host (TCP
9000/3483), while a **positive control** on a normal bridge reaches the same decoy (so the probe is meaningful). The same topology *with*
`gw_priority` also ran on every engine here (older engines ignore it), but it needs a Compose that knows the key.

## How the minimums were determined
| Question | Method | Result |
|---|---|---|
| Which Compose releases accept the compose files? | the project's static Compose binaries (`docker-compose-linux-x86_64`, sha256-verified) run `config -q` on `demo/compose.yml` and `docker/compose.yml` with all profiles, in a container without network | `gw_priority` is rejected by 2.24.5, 2.27.1, 2.29.7, 2.31.0 and 2.32.4 and accepted from 2.33.1 (first accepting release: 2.33.x). Without it both files validate on 2.5.1 and later; 2.0.1 rejects the top-level `name:` |
| Which Compose CLI features do the scripts need? | `features_probe.sh` in a Docker-in-Docker sandbox: `up -d --remove-orphans`, `depends_on` with `service_healthy` and `service_completed_successfully`, profiles, `healthcheck`, `ps -q --status running`, `exec -T`, `cp`, `logs --tail`, `config --images`, `restart`, `down -v` | all pass on 2.20.3 and 2.36.2. `ps --format '{{.Health}}'` (used earlier by `demo.sh`) does **not** exist in 2.5.1-2.20.3 (exists in 2.24.5), so `demo.sh` now reads health with `docker inspect` |
| Which Engine versions work? | `topology_probe.sh` + `features_probe.sh` on `docker:<version>-dind` for 24.0.9, 25.0.5, 26.1.4, 27.5.1, 28.0.4, 29.1.5 (pinned by digest) | see the matrix below; Engine < 24 was not tested, so 24.0 is the stated minimum |
| Does the real demo start from nothing on the lower bound and a current version? | `scripts/test_entry_clean.sh`: fresh clone, no images, `make demo` + `scripts/hwkit_bundle.sh` in a sandbox | see the transcripts under `reports/evidence/entry_clean_*.log` |

Why Compose 2.20 and not lower: the lowest release the full probe suite was *run* on is 2.20.3; the compose files validate on 2.5.1 and later, but nothing below 2.20 was run end to end, and a stated minimum should be something that was run.

## Engine 24 quirk (harmless, handled)
On Engine 24.0.9 a container attached **only** to the internal `inhibit_ipv4` network gets a default route via the non-existent gateway
(172.29.251.1); Engine 25+ creates no default route. Both cannot reach anything: the routed address does not exist on the host. The older stack test
asserted "no default route" and would have failed on Engine 24 although the isolation held; `scripts/check_isolation.sh` therefore probes
reachability (TCP 9000/3483, UDP 3483 discovery, broadcast, host-name lookups) instead of inspecting routes.

## Matrix (Engine x Compose, 2026-11-18)
Result of `scripts/test_docker_versions.sh` - see `reports/evidence/docker_version_matrix.log` (raw output).

MATRIX_TABLE_PLACEHOLDER

## Reproduce
```bash
scripts/test_docker_versions.sh                                   # whole matrix (about 40 minutes; privileged sandboxes hifi-entry-dind-*, removed afterwards)
scripts/test_docker_versions.sh --engines "24.0.9" --compose "2.20.3"
scripts/test_entry_clean.sh --engine 25.0.5 --compose 2.24.5      # the owner's failing combination, from a fresh clone
```
The sandboxes are `--privileged` (Docker-in-Docker needs it) and **only** used for this test: no host socket, no host mount, own name prefix
`hifi-entry-`, removed with their volumes at the end. Nothing is installed on the host.
