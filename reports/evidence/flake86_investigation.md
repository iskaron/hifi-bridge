# OPEN_QUESTIONS #86 - first bit-exact test after a lab recreate (P4, 2026-11-21)

Labels: INTEGRATION = real Lyrion Media Server 9.1.2 + Squeezelite 1.9.9-1414 in the Docker lab (`btbridge-lab`), simulated Bluetooth.
Evidence levels: **observed** (in logs), **reproduced** (failure re-created on purpose), **confirmed by experiment** (cause switched on
and off with everything else equal), **hypothesis**. All runs inside the lab's internal network; nothing outside the lab was contacted.
Raw per-run evidence (pre-status, Lyrion log since its start, Squeezelite stream starts, pytest output) is in `.lab/p4-flake86/runs/`
(git-ignored lab state), the lab's log history before P4 in `.lab/p4-flake86/history/`.

## Result

**Cause (reproduced and confirmed by experiment):** Lyrion resumed a stale queue item with a 1 s fade-in when a lab player connected to
the recreated Lyrion; that item could not be streamed, Lyrion kept the fade-in pending, and the first test's stream started with it
(`strm s autostart: 2 transition period: 1 transition type: 2`, Squeezelite "fade IN: 44100 frames"). The bit-exact analysis aligns on
the first 256 non-silent frames, which are scaled by the ramp: "could not align to reference (samples altered?)".

Chain:
1. The player-outage scenario (`scripts/test_outage.sh player1`) stops player1 while it plays the bridge stream; Lyrion records
   `playingAtPowerOff: 1` (final run: `_ts_playingAtPowerOff` = 2026-11-20 09:51:27 UTC, the outage step).
2. The bridge releases with `playlist deleteitem <url>`; Lyrion empties the live queue but does not rewrite the persisted queue
   `prefs/clientplaylist_<mac>.m3u` (`deleteitem` is not in Lyrion's queue-saving command list, `Slim/Player/Playlist.pm`
   `%validSubCommands`). The file keeps the last bridge URL (observed after a fresh lab and only test_01).
3. A recreate of Lyrion (compose recreates `lms` before the players, so Lyrion never sees their disconnect) starts Lyrion with that
   state. When player1 connects as a new client, `Slim::Player::Player::resumeOnPower(1)` runs `playlist jump <i> 1 0` - fade-in 1 s.
4. The stale URL fails before streaming (`Scanner::Remote ... Can't connect to remote server to retrieve playlist for
   http://appliance:8090/...: Couldn't resolve IP address`). `StreamingController->{fadeIn}` is cleared only in `_Start`/`_Resume`
   (a stream that really starts); `stop` and `playlist clear` (what `Lab.reset()` sends) leave it.
5. The first test's `playlist play <bridge url>` streams with the pending fade-in.

**Not the cause:** player gain (65536 throughout), the bridge (it sent its usual `power`/`play`; the stale request never reached any
bridge in the observed cases - DNS failure or connect timeout, and the failing test's stream server saw only its own two requests), a
race with the stale fetch (in the final run it failed 7 minutes before the test), timing. The `audg 3840` session analysed for `c76c541`
(2026-11-18 22:28) was a different, real cause with the same symptom (its streams had `transition type: 0`).

**Fix (harness, DECISIONS #141):** `Lab.settle_players()`, once per process from `Lab.reset()`: each lab player starts the lab's own
synthetic stream until Lyrion reports it playing, then `playlist clear` + `stop`; Squeezelite's log must show that the next stream start
has no transition (a second settle start if the first carried one; error otherwise). No sleep, no rerun, no threshold change. Regression
test `tests/integration/test_00_lab_settle.py` (fails without the fix: `AttributeError`; passes with it).

## Observed in the lab history (before P4)

Lyrion starts in `server.log` (2026-11-15 .. 11-20): 44. Followed within 8 s by a stale resume error: 5.

| Lyrion start (UTC) | stale resume (first error) | first test stream on player1 | outcome |
|---|---|---|---|
| 2026-11-18 17:55:55 | +8.2 s `http://172.17.0.7:8080/radio.wav` (connect timeout) | 18:01:21 `transition type: 2` | full run pkg 2: bit_exact[wav] failed |
| 2026-11-18 22:40:42 | +8.2 s old bridge `.../ZK-4bIfA.flac` (connect timeout) | 22:45:17 `transition type: 2` | full run pkg 3: bit_exact[wav] failed |
| 2026-11-18 23:26:01 | +7.1 s old bridge `.../rxJ2VW2U.wav` (connect timeout) | 23:26:11 `transition type: 2` | recreate during the `c76c541` work (result not logged) |
| 2026-11-20 09:25:32 | +5.7 s `http://appliance:8090/.../1QbI5jaY.wav` (DNS) | 09:32:52 `transition type: 2` | final run: bit_exact[wav] failed, `first_stored_frame` 250 |
| 39 other starts | none | `transition type: 0` | no failure (2 faded streams on 11-19 09:56/18:55 are the Lyrion-outage scenario: resume of the live URL succeeded) |

Player1 gain in all four faded cases: `audg gainL: 65536` from connect to the test stream.

## Reproduced: cold labs, pre-fix harness

Procedure per run: `scripts/lab_down.sh` (lab containers, network and volumes incl. captures removed) -> `.lab/lms-config` prepared ->
`scripts/lab_up.sh` -> status of all four players -> `scripts/run_integration.sh -k test_end_to_end_bit_exact -s` (wav first, as in the
suite). Variants: **stale** = copy of the state left by the earlier runs (`.lab/lms-config.stale-20261120T235200Z`, taken after stopping
Lyrion first: player1 `playingAtPowerOff: 1`, queue `http://appliance:8090/s/.../tOpBt475.wav`); **fresh** = empty directory (Lyrion
seeds `docker/lms-config/server.prefs`); **stale-disarmed** = the stale copy with player1's `playingAtPowerOff` set to `''`.

| variant | runs | failed | stale resume in Lyrion log | first stream on player1 |
|---|---|---|---|---|
| stale | 6 | **6** | 6/6 | `transition type: 2` 6/6 |
| fresh | 5 | 0 | 0/5 | `transition type: 0` 5/5 |
| stale-disarmed | 3 | 0 | 0/3 | `transition type: 0` 3/3 |

Failure every time: `{'blocks': 0, 'error': 'could not align to reference (samples altered?)', 'first_stored_frame': N}` with N = 49 .. 414
(depends on where the ramp first exceeds 1 LSB of the signal). The flac test after it always passed (the fade-in was consumed).

Player status after the players connected (stale run pre3, 23:58:31 UTC; the other players: stop, power 1, volume 100, empty queue,
unsynced, `playingAtPowerOff` ''; `syncgroups` empty):

```
02:00:00:00:00:01 {'mode': 'stop', 'power': 1, 'mixer volume': 100, 'playlist_tracks': 1, 'remote': 1, 'current_title': 'Bluetooth'}
                  urls: ['http://appliance:8090/s/OLXkcdF9v-.../tOpBt475.wav']  sync: '-'  playingAtPowerOff: '0' (consumed by the resume)
[26-11-20 23:58:31.4657] Slim::Utils::Scanner::Remote::__ANON__ (228) Error: Can't connect to remote server to retrieve playlist for,
                         http://appliance:8090/s/OLXkcdF9v-.../tOpBt475.wav: Couldn't resolve IP address for: appliance.
[23:58:36.240240] process_strm:349 strm s autostart: 2 transition period: 1 transition type: 2 codec: p      (the test's bridge URL)
[23:58:37.754655] _checkfade:304 fade IN: 44100 frames
```

Fresh run pre3: player1 empty queue, `playingAtPowerOff` None, no Lyrion error, `strm s autostart: 3 transition period: 10 transition
type: 0` for both test streams. (Its volume 50 is the seeded default; the reset sets and confirms 100.)

Capture analysis of the failing run pre1 (`.lab/p4-flake86/tools/fadecheck.py`): stored/reference gain over the first 44100 frames
0.05 -> 0.28 -> 0.51 -> 0.79 -> 1.00 (a linear 1 s ramp); with that second skipped the output is the generator signal, bit-exact
(74 of 75 blocks matched, the 75th cut by the analysis window, no jump).

## Confirmed by experiment: warm lab, no recreate, fresh state

Before the wav test, Lyrion gets a play request for an unreachable URL on its own loopback (`http://127.0.0.1:1/p4/x.wav`, fails with
"Connect timed out"), with or without the fade-in parameter; the test's reset then sends its usual `playlist clear` + `stop`.

| request | runs | failed | test stream |
|---|---|---|---|
| `playlist play <unreachable> Bluetooth 1` (fade-in 1 s) | 3 | **3** | `transition type: 2` |
| `playlist play <unreachable> Bluetooth` | 3 | 0 | `transition type: 0` |

Old session URL of a **running** bridge, requested with fade-in (`.lab/p4-flake86/tools/stale_live.py`, 3 runs): the stream server
answered 410 each time (`stale_rejected` +1, `connections` +0, `bad_token` +0), and the bridge's next stream started with
`transition type: 2` 3/3. The product rejects the stale URL correctly; the fade-in is Lyrion's (OPEN_QUESTIONS #99, FINDINGS 13/14).

Start latency is not affected: `t_first_lms_output` of the wav test: 1.56-1.62 s faded (stale, n=6), 1.51-1.62 s unfaded (fresh, n=5).

## After the fix: cold labs

Same procedure as above, fixed harness.

| variant | runs | failed | stale resume in Lyrion log | settle: first start carried a transition | bridge streams on player1 |
|---|---|---|---|---|---|
| stale | 6 | 0 | 6/6 | 6/6 (`{'player1': [2]}`, consumed; second settle start type 0) | type 0 (12/12) |
| fresh | 6 | 0 | 0/6 | 0/6 | type 0 (12/12) |

(Runs post1-5 and two more that were meant as full runs but ran only these two tests because of a script bug - `notfull1/2` in
`.lab/p4-flake86/runs/`.)

Settle cost per session: ~2 s (nothing pending), ~4.5 s (pending fade-in consumed; pytest 35.0-35.4 s vs 37.8-38.5 s for the two tests).

## After the fix: full integration suites from cold labs

`scripts/run_integration.sh -s` (all 56 tests incl. the new `test_00_lab_settle.py`), each from a cold lab:

| run (UTC start) | how the lab was made cold | stale resume at Lyrion start | result |
|---|---|---|---|
| 2026-11-21 00:30 (full3) | **natural chain of the failing final run:** `scripts/test_outage.sh player1` (left player1 `playingAtPowerOff: 1` and `http://172.17.0.7:8090/s/.../WIJDok7Z.wav` in the persisted queue), then `docker compose up -d --force-recreate lms player1 player2 player3 legacy1 mosquitto` | yes (old bridge URL, connect timeout), consumed by the settle | **56 passed** in 992 s |
| 2026-11-21 00:49 (fullA) | `lab_down.sh` + stale reference state + `lab_up.sh` | yes (`http://appliance:8090/...`), consumed by the settle | **56 passed** in 990 s |
| 2026-11-21 01:06 (fullB) | `lab_down.sh` + empty state + `lab_up.sh` | no | **56 passed** in 991 s |

In all three runs every one of the 57-61 bridge streams on player1 started with `transition type: 0`; `transition type: 2` appeared
only on settle streams (the pending fade-in being consumed; `test_00` creates one on purpose). Each run also shows one Lyrion resume of an
old bridge URL about 5 minutes into the suite (a player re-attaching after an earlier test's bridge had stopped, e.g. 01:12:02 in fullB);
it carried no fade-in (the reconnect path uses `play` without one) and no later stream was faded - observed, not investigated further.

Other suites (this change touches only the lab harness and docs; no product code): `scripts/run_static.sh` "static exit: 0";
`scripts/run_secrets.sh` "gitleaks ... over 289 commits: rc=0". `scripts/run_unit.sh` not run (no product change).

## Open

- OPEN_QUESTIONS #99: Lyrion's persisted queue keeps the released bridge URL; on a real installation an old "Bluetooth" item can come
  back after a Lyrion restart and, if the player was playing at power-off, its failed resume makes the guest's next stream fade in over
  1 s. Lyrion behaviour; options listed there; real Lyrion installations and players: hardware gate.
- Not tested: other Lyrion versions (the `fadeIn` handling was read in 9.1.2 only), real Squeezebox hardware.
