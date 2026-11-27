"""UNIT (pure): ICY metadata framing is byte-exact, counts the bytes actually sent (the MP3 bytes, not the PCM they came from),
survives any chunking of the audio and escapes/limits titles (DECISIONS #147; the WAV behaviour of DECISIONS #18 is unchanged)."""
import random

from btbridge.app import display_title
from btbridge.stream.icy import IcyMuxer, MAX_BLOCK, clean_title, metadata_block


def demux(stream: bytes, metaint: int) -> tuple[bytes, list[str]]:
    """Reference ICY reader: returns (audio bytes, the non-empty metadata texts in order)."""
    audio, titles, i = bytearray(), [], 0
    while i < len(stream):
        take = stream[i:i + metaint]
        audio += take
        i += len(take)
        if len(take) < metaint:
            break
        n = stream[i] * 16
        i += 1
        if n:
            titles.append(stream[i:i + n].rstrip(b"\0").decode("utf-8"))
            i += n
    return bytes(audio), titles


def audio_bytes(n: int) -> bytes:
    rnd = random.Random(7)
    return bytes(rnd.randrange(256) for _ in range(n))


def test_block_layout_is_length_byte_text_and_nul_padding_to_16():
    b = metadata_block("Miles Davis - Blue in Green")
    text = b"StreamTitle='Miles Davis - Blue in Green';"
    assert b[0] == (len(text) + 15) // 16 and b[1:1 + len(text)] == text and len(b) == 1 + b[0] * 16 and set(b[1 + len(text):]) <= {0}
    assert metadata_block("") == b"\0" and metadata_block("   \n\t ") == b"\0"


def test_metaint_counts_body_bytes_and_the_audio_is_returned_unchanged():
    src = audio_bytes(8192 * 5 + 123)
    m = IcyMuxer(lambda: "Artist - Title", 8192, on_change=False)
    out = m.feed(src)
    audio, titles = demux(out, 8192)
    assert audio == src and len(audio) == 8192 * 5 + 123 and titles == ["StreamTitle='Artist - Title';"] * 5
    assert len(out) == 8192 * 5 + 5 * (1 + 32) + 123             # 29-byte text -> 32 padded bytes + the length byte per block


def test_any_chunking_of_the_input_gives_the_identical_stream():
    src = audio_bytes(50_000)
    whole = IcyMuxer(lambda: "T", 8192).feed(src)
    rnd = random.Random(3)
    for _ in range(5):
        m, out, i = IcyMuxer(lambda: "T", 8192), bytearray(), 0
        while i < len(src):
            n = rnd.choice([1, 7, 1044, 1045, 4096, 17640])
            out += m.feed(src[i:i + n])
            i += n
        assert bytes(out) == whole


def test_on_change_sends_text_once_then_empty_blocks_and_repeats_only_as_a_safety_net():
    cur = {"t": "First - Song"}
    m = IcyMuxer(lambda: cur["t"], 1000, on_change=True, refresh_blocks=4)
    out = m.feed(bytes(1000 * 3))
    cur["t"] = "Second - Song"
    out += m.feed(bytes(1000 * 6))
    cur["t"] = ""
    out += m.feed(bytes(1000 * 2))
    audio, titles = demux(out, 1000)
    assert len(audio) == 11000 and titles[0].startswith("StreamTitle='First - Song'")
    assert [t for t in titles if "Second" in t][0] == "StreamTitle='Second - Song';"
    assert len(titles) < 11                                                       # not at every boundary (legacy repeat mode would be 11)
    assert m.blocks == 11 and m.titled_blocks == len(titles)


def test_a_cleared_title_is_announced_as_empty_and_the_next_one_is_sent_again():
    cur = {"t": "A - B"}
    m = IcyMuxer(lambda: cur["t"], 100, on_change=True, refresh_blocks=1000)
    out = m.feed(bytes(100)); cur["t"] = ""; out += m.feed(bytes(100)); cur["t"] = "A - B"; out += m.feed(bytes(100))
    assert demux(out, 100)[1] == ["StreamTitle='A - B';", "StreamTitle='';", "StreamTitle='A - B';"]   # L = 0 would mean "unchanged": clearing is explicit


def test_legacy_wav_mode_repeats_the_current_title_at_every_boundary():
    m = IcyMuxer(lambda: "X - Y", 100, on_change=False)
    assert demux(m.feed(bytes(500)), 100)[1] == ["StreamTitle='X - Y';"] * 5


def test_quotes_control_characters_and_utf8_are_handled_without_breaking_the_field():
    assert clean_title("It's a \"test\"; ok") == "It’s a \"test\"; ok"
    assert clean_title("a\x00b\r\nc\x7fd\te") == "a b c d e"
    b = metadata_block("Beyoncé – Déjà Vu ☃ 日本語")
    assert b[1:].rstrip(b"\0").decode("utf-8") == "StreamTitle='Beyoncé – Déjà Vu ☃ 日本語';"
    body = metadata_block("it's a 'quoted'; title")[1:].rstrip(b"\0")
    assert body.count(b"';") == 1 and body.endswith(b"';")      # the only quote+semicolon is the field terminator


def test_an_over_long_title_is_cut_on_a_character_boundary_and_fits_one_block():
    b = metadata_block("日" * 3000)                                               # 9000 bytes of 3-byte characters
    assert len(b) - 1 <= MAX_BLOCK and b[0] == len(b[1:]) // 16
    text = b[1:].rstrip(b"\0").decode("utf-8")                                    # decodes: no half character at the cut
    assert text.startswith("StreamTitle='日") and text.endswith("';")


def test_display_title_prefers_artist_dash_title_and_never_invents_or_keeps_values():
    assert display_title("Blue in Green", "Miles Davis", "Kind of Blue") == "Miles Davis - Blue in Green"
    assert display_title("Only Title", "", "x") == "Only Title" and display_title("", "Only Artist", "") == "Only Artist"
    assert display_title("", "", "Some Album") == "" and display_title(None, None) == ""        # metadata unavailable -> generic display
    assert display_title("  A\n B ", "C\x00", "") == "C - A B"
