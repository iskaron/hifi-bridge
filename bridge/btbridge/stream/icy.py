"""In-band ICY (Shoutcast) metadata: the ``StreamTitle`` block and the byte-exact interleaving into an audio stream.

Protocol: the response carries ``icy-metaint: N``; after every N bytes of *body* a metadata block follows: one length byte L
(block size = L * 16 bytes) and then L * 16 bytes of ``StreamTitle='...';`` padded with NUL. L = 0 means "unchanged". The count N
is over the bytes actually sent as body (for MP3: the encoded MP3 bytes, never the PCM they came from).
"""
from __future__ import annotations

MAX_BLOCK = 255 * 16                                # a block holds at most 4080 bytes
_PREFIX, _SUFFIX = b"StreamTitle='", b"';"


def clean_title(title: str) -> str:
    """Printable single-line text: control characters (incl. NUL, CR, LF) become spaces, runs of blanks collapse, and the quote that
    would end the field early is replaced by a typographic apostrophe (ICY has no escaping)."""
    t = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in str(title))
    return " ".join(t.replace("'", "’").split())


def metadata_block(title: str, allow_empty: bool = False) -> bytes:
    """The complete block (length byte + padded text) announcing ``title``; b"\\0" for no title. UTF-8; a title that does not fit is
    shortened on a character boundary (never in the middle of a multi-byte character)."""
    t = clean_title(title)
    if not t and not allow_empty:
        return b"\0"
    room = MAX_BLOCK - len(_PREFIX) - len(_SUFFIX)
    raw = t.encode("utf-8")
    if len(raw) > room:
        raw = raw[:room].decode("utf-8", "ignore").encode("utf-8")
    body = _PREFIX + raw + _SUFFIX
    body += b"\0" * (-len(body) % 16)
    return bytes([len(body) // 16]) + body


class IcyMuxer:
    """Per connection: inserts metadata blocks into the body.

    ``on_change``: a block with text is sent only when the title differs from the last one sent on this connection (and at the first
    boundary), otherwise the 1-byte empty block; ``refresh_blocks`` repeats an unchanged title every so many boundaries as a safety
    net (a receiver that cleared its title is corrected, at the cost of one short block). With ``on_change=False`` the current title
    is sent at every boundary (the long-standing WAV behaviour).
    """

    def __init__(self, get_title, metaint: int, on_change: bool = True, refresh_blocks: int = 16, sent_before: int = 0,
                 clear_text: str = ""):
        self.get_title, self.metaint, self.on_change, self.refresh_blocks = get_title, metaint, on_change, refresh_blocks
        self.clear_text = clear_text                # what a cleared title is announced as ("" = an empty StreamTitle; see DECISIONS #147)
        self.sent = sent_before                     # body bytes so far, ICY blocks excluded
        self._last: str | None = None
        self._quiet = 0
        self.blocks = self.titled_blocks = 0

    def _block(self) -> bytes:
        title = clean_title(self.get_title() or "")
        self.blocks += 1
        if self.on_change:
            if title == self._last:
                self._quiet += 1
                if not (self._quiet >= self.refresh_blocks and title):
                    return b"\0"
            cleared = not title and bool(self._last)          # a title was shown and is now gone: say so explicitly (L = 0 means "unchanged")
            self._last, self._quiet = title, 0
            if cleared:
                self.titled_blocks += 1
                return metadata_block(self.clear_text, allow_empty=True)
        if not title:
            return b"\0"
        self.titled_blocks += 1
        return metadata_block(title)

    def feed(self, audio: bytes) -> bytes:
        out = bytearray()
        buf = memoryview(audio)
        while len(buf):
            room = self.metaint - (self.sent % self.metaint)
            part, buf = buf[:room], buf[room:]
            out += part
            self.sent += len(part)
            if self.sent % self.metaint == 0:
                out += self._block()
        return bytes(out)
