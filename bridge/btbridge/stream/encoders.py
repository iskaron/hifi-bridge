"""Stream encoders: WAV (raw PCM, default baseline) and FLAC (via the `flac` binary)."""
from __future__ import annotations

import struct
import subprocess
import threading


def wav_header(rate: int = 44100, channels: int = 2, bits: int = 16) -> bytes:
    """Endless WAV header (size fields 0xFFFFFFFF - 'unknown length')."""
    size = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits) +
            b"data" + struct.pack("<I", size))


WAV_HEADER_LEN = 44
CONTENT_TYPES = {"wav": "audio/x-wav", "flac": "audio/x-flac"}


class WavEncoder:
    fmt = "wav"

    def __init__(self, rate: int = 44100, with_header: bool = True):
        self._hdr = wav_header(rate) if with_header else b""

    def header(self) -> bytes:
        return self._hdr

    def encode(self, pcm: bytes) -> bytes:
        return pcm

    def close(self) -> None:
        pass


class FlacEncoder:
    """Live FLAC through the ``flac`` CLI. Lossless; ~50% smaller; adds encoder latency + CPU."""
    fmt = "flac"

    def __init__(self, rate: int = 44100, level: int = 0):
        self.proc = subprocess.Popen(
            ["flac", "-s", "-c", "--force-raw-format", "--endian=little", "--sign=signed", "--channels=2",
             "--bps=16", f"--sample-rate={rate}", f"-{level}", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._out = []
        self._lock = threading.Lock()
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self):
        try:
            while True:
                d = self.proc.stdout.read1(8192)
                if not d:
                    break
                with self._lock:
                    self._out.append(d)
        except Exception:
            pass

    def header(self) -> bytes:
        return b""

    def encode(self, pcm: bytes) -> bytes:
        try:
            self.proc.stdin.write(pcm)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            return b""
        with self._lock:
            out, self._out = b"".join(self._out), []
        return out

    def close(self) -> None:
        try:
            self.proc.kill()
        except Exception:
            pass
