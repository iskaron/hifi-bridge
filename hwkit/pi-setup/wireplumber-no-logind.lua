-- WirePlumber 0.4.x: /etc/wireplumber/bluetooth.lua.d/80-no-logind.lua
-- Without systemd-logind, 0.4 quits ("failed to start systemd logind monitor") - docs/PIPEWIRE_CAPTURE.md, finding 9.
bluez_monitor.properties["with-logind"] = false
