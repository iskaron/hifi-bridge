"""Hardware validation kit (docs/HARDWARE_VALIDATION_PLAN.md).

Recording side (runs on the Raspberry Pi under test, stdlib + jeepney only): environment capture, BlueZ D-Bus traces with operator markers,
PCM capture of the phone's audio, structured results.  Analysis side (Docker on any machine, numpy): test-track generation and decoding,
trace replay through the bridge's translator, reports.

Nothing in this package is ever executed against hardware on the development host; its tests use fakes (fake BlueZ, PipeWire pipe source).
"""
__version__ = "0.1.0"
