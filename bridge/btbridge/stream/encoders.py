"""Stream encoders: WAV (raw PCM, default baseline), FLAC (via the `flac` binary) and live MP3 320 kbit/s CBR (via the `lame` binary)."""
from __future__ import annotations

import collections
import os
import shutil
import struct
import subprocess
import threading
import time


def wav_header(rate: int = 44100, channels: int = 2, bits: int = 16) -> bytes:
    """Endless WAV header (size fields 0xFFFFFFFF - 'unknown length')."""
    size = 0xFFFFFFFF
    return (b"RIFF" + struct.pack("<I", size) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * bits // 8, channels * bits // 8, bits) +
            b"data" + struct.pack("<I", size))


WAV_HEADER_LEN = 44
CONTENT_TYPES = {"wav": "audio/x-wav", "flac": "audio/x-flac", "mp3": "audio/mpeg"}
STREAM_FORMATS = ("wav", "flac", "mp3")


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
        """Stop the encoder child, wait for it (no zombie) and close its pipes (review L4)."""
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                if f is not None:
                    f.close()
            except Exception:
                pass
        self._t.join(timeout=1)


class EncoderUnavailable(Exception):
    """The external encoder binary is not installed (or cannot be started). WAV and FLAC are unaffected."""


MP3_BITRATE_KBPS = 320
MP3_QUALITY = 5                     # lame -q: algorithm quality 0 (best, slowest) .. 9; 5 is lame's own default (speed/quality balance)
MP3_QUEUE_CAP = 128 * 1024          # encoded bytes that may wait for the HTTP writer (about 3 s at 320 kbit/s); the reader then blocks
MP3_READ = 16384


def mp3_encoder_path() -> str | None:
    """Absolute path of the ``lame`` binary, or None. Looked up on PATH only (never from user input)."""
    return shutil.which("lame")


class Mp3Encoder:
    """Live MPEG-1 Layer III, 44.1 kHz stereo, **CBR 320 kbit/s**, encoded on the bridge with the ``lame`` CLI (LGPL-2.0+).

    One encoder process per HTTP connection, so every client starts at a frame boundary with an empty bit reservoir (a late joiner
    never sees frames that depend on bytes it did not receive). The process reads raw s16le stereo PCM from stdin and writes the
    stream to stdout; ``lame`` is run without the Xing/Info tag (``-t``), which is meaningless for a live stream.

    Bounded by construction: the child's pipes hold 64 KiB each, a reader thread moves its output into a queue of at most
    ``MP3_QUEUE_CAP`` bytes and then stops reading (the child blocks, nothing grows), and ``close()`` always kills and reaps the
    child, so no orphan is left behind. The queue is only drained by ``encode()``; nothing is written to disk.
    """
    fmt = "mp3"

    def __init__(self, rate: int = 44100, bitrate_kbps: int = MP3_BITRATE_KBPS, quality: int = MP3_QUALITY, binary: str | None = None):
        exe = binary or mp3_encoder_path()
        if not exe:
            raise EncoderUnavailable("the 'lame' MP3 encoder is not installed")
        self.rate, self.bitrate_kbps = rate, bitrate_kbps
        try:
            self.proc = subprocess.Popen(           # fixed argv, no shell; the binary comes from shutil.which
                [exe, "-r", "-s", f"{rate / 1000:g}", "--bitwidth", "16", "--signed", "--little-endian", "-m", "s",
                 "--cbr", "-b", str(bitrate_kbps), "-q", str(quality), "-t", "--silent", "-", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as e:
            raise EncoderUnavailable(f"cannot start the MP3 encoder: {e}") from e
        self._q: collections.deque[bytes] = collections.deque()
        self._queued = 0
        self._cond = threading.Condition()
        self._eof = False
        self._closed = False
        self.pcm_in = 0                 # bytes of PCM written to the encoder
        self.bytes_out = 0              # bytes of MP3 handed to the caller
        self.encode_s = 0.0             # wall time spent inside encode() (stdin write + queue handover), cumulative
        self._t = threading.Thread(target=self._reader, daemon=True, name="mp3-reader")
        self._t.start()

    def _reader(self) -> None:
        try:
            while True:
                with self._cond:
                    while self._queued >= MP3_QUEUE_CAP and not self._closed:
                        self._cond.wait(0.5)                 # backpressure: stop reading, the child blocks in write()
                    if self._closed:
                        return
                d = self.proc.stdout.read1(MP3_READ)
                if not d:
                    break
                with self._cond:
                    self._q.append(d)
                    self._queued += len(d)
        except (OSError, ValueError):
            pass
        finally:
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None and not self._eof

    def header(self) -> bytes:
        return b""

    def encode(self, pcm: bytes) -> bytes:
        """Feed PCM, return whatever MP3 is ready (possibly nothing yet). Raises BrokenPipeError when the encoder died."""
        t0 = time.monotonic()
        try:
            self.proc.stdin.write(pcm)
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise BrokenPipeError("MP3 encoder stopped") from e
        self.pcm_in += len(pcm)
        out = self._take()
        self.encode_s += time.monotonic() - t0
        return out

    def _take(self) -> bytes:
        with self._cond:
            if not self._q:
                return b""
            out = b"".join(self._q)
            self._q.clear()
            self._queued = 0
            self._cond.notify_all()
        self.bytes_out += len(out)
        return out

    def cpu_seconds(self) -> float | None:
        """User+system CPU time the encoder process has used (Linux /proc), None where that is not available."""
        try:
            with open(f"/proc/{self.proc.pid}/stat", "rb") as f:
                fields = f.read().rsplit(b")", 1)[1].split()
            return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None

    def close(self) -> None:
        """Stop the encoder, reap it (no zombie), close its pipes and end the reader thread. Safe to call twice."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        try:
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:                                       # nosec B110 - already gone
            pass
        for f in (self.proc.stdin, self.proc.stdout):
            try:
                if f is not None:
                    f.close()
            except Exception:                                   # nosec B110
                pass
        self._t.join(timeout=1)
