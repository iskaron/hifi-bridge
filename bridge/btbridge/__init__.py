"""Bluetooth Audio Bridge - core library (Python stdlib only).

Hardware-independent: a Bluetooth/A2DP adapter (real BlueZ or simulated) feeds
PCM + media events in; the bridge publishes one live HTTP stream and controls
exactly one Lyrion player through the standard LMS JSON-RPC interface.
"""
__version__ = "0.1.0-spike"
