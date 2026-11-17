"""User audio files for the virtual phones: decoded locally to 44.1 kHz stereo s16le; anything else is refused with a reason."""
import io
import subprocess
import wave

import numpy as np
import pytest

from hifidemo import decode as D


def wav_bytes(rate=44100, channels=2, seconds=0.5, sampwidth=2):
    t = np.arange(int(rate * seconds)) / rate
    x = (0.4 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    if channels == 2:
        x = np.stack([x, x], axis=1).reshape(-1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(sampwidth); w.setframerate(rate)
        w.writeframes(x.tobytes())
    return buf.getvalue()


def frames(pcm):
    return len(pcm) // 4


def test_wav_44k1_stereo_is_taken_as_is():
    pcm = D.decode(wav_bytes(), "song.wav")
    assert frames(pcm) == 22050


def test_wav_mono_48k_is_converted():
    pcm = D.decode(wav_bytes(rate=48000, channels=1), "mono.wav")
    assert abs(frames(pcm) - 22050) <= 2
    x = np.frombuffer(pcm, dtype="<i2").reshape(-1, 2)
    assert np.array_equal(x[:, 0], x[:, 1]) and np.abs(x).max() > 10000


def test_flac_is_decoded_with_the_image_tool(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(wav_bytes(seconds=1.0))
    subprocess.run(["flac", "-s", "-f", "-o", str(tmp_path / "a.flac"), str(src)], check=True)
    assert abs(frames(D.decode((tmp_path / "a.flac").read_bytes(), "a.flac")) - 44100) <= 2


def test_broken_mp3_and_ogg_are_refused_with_a_reason():
    # No MP3/Vorbis encoder is in the image, so only the error path is tested here; real files are exercised manually (docs/DEMO.md).
    for name in ("x.mp3", "x.ogg"):
        with pytest.raises(D.DecodeError, match="could not decode"):
            D.decode(b"\0" * 4096, name)


def test_unknown_or_broken_files_are_refused():
    with pytest.raises(D.DecodeError, match="unsupported"):
        D.decode(b"hello", "notes.txt")
    with pytest.raises(D.DecodeError):
        D.decode(b"RIFF....garbage", "broken.wav")


def test_size_limits():
    with pytest.raises(D.DecodeError, match="too large"):
        D.check_size(D.MAX_UPLOAD_BYTES + 1)
    D.check_size(1000)


def test_title_from_file_name():
    assert D.title_from_name("01 - Mein_Lied.mp3") == "01 - Mein Lied"
    assert D.title_from_name("../../etc/passwd") == "passwd"
