# Playback states and ownership contract

Maintain *separate* state dimensions: `bluetooth_connection`, `bt_media_activity`, `audio_permission`, `lyrion_reachability`, `target_ownership`, and `stream_transport`. UI must not conflate "Bluetooth connected" with "audible output exists".

| Event | Intended outcome |
| --- | --- |
| Owner enables pairing | Discoverable/pairable for ~5 min, then automatically close pairing window |
| Known phone connects | Connected/ready, do not steal LMS playback merely from connection |
| Phone starts Play; target idle | Initiate stream promptly; do not force 2 s wait |
| Phone notification while radio plays | Avoid taking radio for a short beep; evaluate AVRCP + ~2 s activity filter |
| Sustained new phone media while radio plays, override on | Start bridge-owned playback, with brief compatible buffered lead-in; don't auto-restore radio later |
| New phone media while radio plays, override off | Do not take over; clearly show permission/target status |
| Phone pauses | Media becomes silent promptly where possible; logical ownership grace up to 30 s; never stop unrelated source |
| Phone resumes within grace | Restore bridge audio using tested LMS protocol; no minute-long delay caused by stale buffer |
| Grace expires | Release only bridge-owned LMS stream |
| Phone disconnects | Release only bridge-owned LMS stream immediately |
| Owner disables bridge audio | Release only bridge-owned LMS stream; preserve BT pairing/connection as configured |
| Someone switches LMS to radio | Bridge loses ownership. Late timers/disconnect MUST NOT issue generic stop against radio |
| Fresh phone Play after manual source change | May retake if override on; do not constantly fight manual source |
| Lyrion unreachable | BT stays connected, mark degraded, retry selected LMS; drop old audio; live source on return |
| Selected player unreachable | Do not move to another room or silently change target |
| Owner changes selected player/group | Stop any currently owned stream at old target; continue connected BT on new target; do not reconfigure sync group |
| Diagnostic test while target plays anything | Refuse without interruption |

**Ownership implementation requirement:** Define a bridge-specific session identifier and state-invalidation mechanism. Read back/subscribe to current LMS playlist/URL/stream state, guard delayed operations and ownership changes; investigate races between checking and sending LMS commands. Prefer controllable, minimal APIs and note any unavoidable races explicitly. Test two bridges targeting the same LMS player.

**Timing probes:** `t_play_event`, `t_first_bt_pcm`, `t_lms_command`, `t_stream_client_connected`, `t_first_lms_output`, `t_pause`, `t_last_audible_output`, `t_stop`. All capture methods and clock synchronization must be documented; absence of an audio capture rig means *not measured*, not zero.
