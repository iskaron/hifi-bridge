"""Hardware-independent audio-source adapter interface.

A *real* adapter (BlueZ A2DP sink + AVRCP via D-Bus, PCM via PipeWire/WirePlumber) and
the *simulated* adapter (testkit ``btkit.sim_a2dp``) implement the same interface and
drive the same pipeline.  See docs/BLUEZ_ADAPTER_DESIGN.md for the real mapping.

Callbacks the adapter invokes (all optional to implement on the consumer side):
  on_connect(device_id) -> bool   # return False => adapter must refuse/disconnect the new source
  on_disconnect(device_id)
  on_transport(state)             # 'idle' | 'pending' | 'active'   (A2DP transport)
  on_avrcp(status)                # 'playing' | 'paused' | 'stopped' | None (unsupported)
  on_volume(value)                # AVRCP absolute volume 0..127
  on_metadata(title, artist, album)
  on_pcm(bytes)                   # 44.1 kHz / s16le / stereo frames (decoded)
"""
from __future__ import annotations

from typing import Callable, Protocol


class SourceCallbacks:
    on_connect: Callable[[str], bool]
    on_disconnect: Callable[[str], None]
    on_transport: Callable[[str], None]
    on_avrcp: Callable[[str | None], None]
    on_volume: Callable[[int], None]
    on_metadata: Callable[[str, str, str], None]
    on_pcm: Callable[[bytes], None]


class AudioSourceAdapter(Protocol):
    def attach(self, cb: SourceCallbacks) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def set_pairable(self, enabled: bool) -> None: ...
    def disconnect_device(self, device_id: str) -> None: ...


class DeviceManagement(Protocol):
    """Owner device management (WebUI 'Pairing' page). Implemented by the BlueZ adapter and the simulated source.

    paired_devices() -> [{"address": "AA:..", "name": str, "connected": bool, "trusted": bool}]
    remove_device(address)   forget a paired phone (it must pair again inside a pairing window)
    set_alias(name)          the Bluetooth name guests see
    """
    def paired_devices(self) -> list[dict]: ...
    def remove_device(self, address: str) -> bool: ...
    def set_alias(self, name: str) -> None: ...
