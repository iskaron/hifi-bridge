#!/bin/bash
# Entrypoint of the demo's Lyrion container: seeds the preferences on first start, then hands over to the image's own start script.
# Loudness (docs/DEMO.md "Loudness"): Lyrion starts new players at volume 50, which is -25 dB and made the demo hardly audible (measured
# -42 dBFS RMS at the room's virtual DAC). The three rooms start at 75 (-12.5 dB, headroom to both sides); data of an older demo that
# still has the default 50 is changed ONCE (marker file), later changes by the owner are never touched.
set -e
CONFIG=${LMS_CONFIG_DIR:-/config}; SEED=${LMS_SEED_DIR:-/seed}       # overridable for the unit test (tests/entry)
mkdir -p "$CONFIG/prefs"
for f in server.prefs favorites.opml; do [ -f "$CONFIG/prefs/$f" ] || cp "$SEED/$f" "$CONFIG/prefs/$f"; done
marker=$CONFIG/prefs/.hifi-demo-room-volume-v1
if [ ! -f "$marker" ]; then
  awk '/^_client:02:00:00:00:01:0[123]:$/ { room = 1; print; next }
       /^[^ ]/ { room = 0 }
       room && /^  volume: 50$/ { print "  volume: 75"; next }
       { print }' "$CONFIG/prefs/server.prefs" > "$CONFIG/prefs/server.prefs.new"
  cat "$CONFIG/prefs/server.prefs.new" > "$CONFIG/prefs/server.prefs" && rm -f "$CONFIG/prefs/server.prefs.new"
  touch "$marker"
fi
exec start-container
