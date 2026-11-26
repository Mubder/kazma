# Members' network across restarts: drill, 2026-11-25

Spaces S4-05b. On the drill VM (`KazmaVMs`, reverted to its clean checkpoint),
Kazma's own engine put in with `kazma setup engine -y` (Docker 29.10.1 in WSL,
bded5e2e6, then the fix). A recorder in the distro read Docker's socket and
`kazma-egress-rules ... check` about every 0.1 s, and a table of its own
(`nft`, hooks after Docker's and Kazma's, which neither touches) counted every
packet from `kz-egress` that got past the rules to a private address, to the
distro, or over IPv6. Containers with a member's limits sent UDP and TCP at
private addresses, the metadata address, Windows and the distro throughout:
one already running (`surv`), one Docker itself starts again
(`--restart=always`, stronger than a member's `--rm`), and, where marked *, a
loop of `docker run` past Kazma's check. "Product" is Kazma's own networked
run (`_run_docker_jail`) in a loop on Windows.

**Controls.** Rules out: the counters moved (7,834 forwarded, 6,835 to the
distro), Windows' SSH and the distro's listener were reached, the check said
not in effect. IPv6 with a privileged container: 0 with the rules, 20 to the
distro without.

## Before the fix (bded5e2e6)

| Case | Past the rules | What was measured |
|---|---|---|
| `systemctl restart docker` * | 0 | containers stopped at 10.2 s; check fails 11.5-12.0 s; Docker answers 12.6 s |
| `kill -9` dockerd * | 0 | rules in again 2.3 s; Docker kills `surv` before its own rules; check fails 13.2-13.8 s; Docker answers 14.4 s |
| dockerd and containerd; the shims too; three times over; killed while starting | 0 | |
| **`kill -9` dockerd, the containers' shims hung** | **468, then 390 (a second run)** | check fails 5.1-5.65 s; packets to private addresses get through in that half second; Docker answers 5.9 s |
| Rules flushed (`iptables -F`), setup asked | - | **`setup engine --check` says "ok" and `setup engine` puts nothing in** |

**Fault 1.** Docker, starting, takes its jump to `DOCKER-USER` out and puts it
back behind `DOCKER-FORWARD` for about half a second. Containers are normally
dead by then; one Docker could not kill (a hung shim) is not.
**Fault 2.** The engine's check read the stamp and the network, not the rules,
so the repair a refused run names did nothing.
**Smaller:** two applies at once failed one of them (`Chain already exists`);
a run refused because Docker did not answer said the network was not set up.

## After the fix

While Docker starts every packet from the bridge is dropped (raw `PREROUTING`,
which Docker never touches), until the guard finds the rules in effect.

| Case | Past the rules | Docker answers / rules in effect (s) | Kazma's check and runs |
|---|---|---|---|
| Hung shims, as above | 0 | - | held throughout the start |
| `systemctl restart docker` * | 0 | 12-14 | product: refused from the stop, then reached 1.1.1.1 only |
| `kill -9` dockerd * | 0 | 14.2 / 14.3 | held from 2.1 s; product: refused, then 1.1.1.1 only |
| dockerd and containerd; the shims too *; three times; killed while starting | 0 | - | in effect at the end |
| `wsl --terminate kazma-engine` * (the keep-alive starts it, 22 s) | 0 | 25.5 / 25.6 | WSL's kernel keeps the bridge and the rules; held during the start; product: refused 3.8-28 s |
| Windows restarted, nobody signed in * | 0 | 1.9 / 2.05 after the distro's first reading | bridge made 1.34 s, held since 0.65 s; product: refused 8 s, then 1.1.1.1 only |
| The rules cannot be put in (hold can) | 7,364, all before the hold (the rules were taken out by hand first) | - | `EGRESS_NETWORK_STILL_THERE` with a container on it: held, reached nothing; `setup engine` put all of it right |
| The rules' script broken, rules flushed | 0 | - | network removed; runs refused |
| Two applies at once | 0 | - | both answer, in effect |

A container already running when Docker restarts dies with it (`surv` exited
137; 255 where its shim was killed) and does not come back.

## Not covered

- The rules flushed while Docker runs: a container already running gets past
  them until Docker next starts (15,238 packets to the distro in the 10 s stop
  of such a restart); the next run is refused. As before.
- A hold that cannot be put in (the script itself gone) leaves a start as it
  was before the fix.
- A distro with its own IPv6 uplink, WSL mirrored networking, a WSL update.
- The drill did not turn a person's switch on through People; the run itself
  (`_run_docker_jail(network=True)`) was asked directly.
