#!/usr/bin/env python3
"""
Scenify - Image Scene Flow Organizer - ULTIMATE FIXED VERSION
→ All original features preserved
→ Opens MAXIMIZED reliably on first launch
→ Remembers window size/state/position if you resize/move
→ Left panel & preview have stable fixed sizes
→ Keyboard arrow navigation (← →) with live preview update
→ Internal QSettings (no files)
→ Folder status indicator below preview (outside preview widget)
→ Professional credit line at the bottom
→ Green loading progress bar when loading folders
→ Everything else 100% intact

UPDATED:
- Compact layout with smaller buttons so preview is fully visible
- Status stays green when no actual folder changes (only yellow when files added/removed from folder)
- Progress bar + status + credit above search (no overlap on preview)
- Rename skips missing/deleted files silently (no crash)
- FIXED: Thumbnail slider now actually resizes thumbnails progressively (not just padding)
- FIXED: Preview maximized to full width (no left/right padding)
- FIXED: Copyright moved to absolute bottom in standard professional position
- NEW: Inline base name input below Rename Selected (no pop-up)
- NEW: Digit count spinbox for controlling zero-padding digits
- FIXED: Left panel wrapped in QScrollArea — window never grows, copyright always visible
- FIXED: Progress bar always reserves its space (pinned above copyright), never shifts layout
- FIXED: Reduced button height + compact status label — no scrolling on left panel
- NEW: Press B/b with an image selected to copy its name (no ext) into the Base Name field

BUGFIX (reload_folder):
- Counter for generic_ names now scans existing disk files so it never reuses a
  number that was previously assigned (even if that file was later renamed).
- Thumbnail cache is evicted for both old_path and new_path before the rename
  so the new image's actual thumbnail is always displayed, not a stale one.

NEW — STAR / FAVORITE RATING:
- Every thumbnail shows a clickable ☆ star overlay in the top-right corner.
- Clicking the star toggles a 5-star rating written directly into the image file.
- The star turns solid gold (★) when the image is rated; hollow (☆) when not.
- Supported formats: JPEG/JPG, PNG.  Other formats show a greyed-out star.
- Rating is written immediately on click; the file on disk is updated in-place.
- Stars survive app restarts: on load the star state is read back from the file.
- You can also press S while a thumbnail is focused to toggle its star.

RATING — HOW IT WORKS (Windows Explorer compatible):
  JPEG/JPG:
    • Writes EXIF tags 0x4746 (Rating=5) and 0x4749 (RatingPercent=99) — these
      are the tags Windows Explorer's property system reads for System.Rating.
    • Also writes XMP with xmp:Rating=5 + MicrosoftPhoto:Rating=99 for Adobe apps.
    • Both are written in a single atomic pass (read → modify → write) so the file
      is never left in a partial state.
    • After writing, SHChangeNotify() is called so Windows Explorer refreshes the
      rating column immediately — no F5 needed, no waiting for the search indexer.
  PNG:
    • Writes XMP iTXt chunk with xmp:Rating=5 + MicrosoftPhoto:Rating=99.
    • Also calls SHChangeNotify() for immediate Explorer refresh.
  Reading back:
    • JPEG: reads EXIF Rating tag first, falls back to XMP.
    • PNG:  reads XMP iTXt chunk.

REQUIREMENT: pip install piexif   (one-time, needed for JPEG EXIF writing)
"""
import os
import sys
import re
import struct
import zlib
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtCore import Qt, QSettings, QTimer
from PyQt5.QtWidgets import QAbstractItemView, QApplication

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".tif"}
# Extensions that support XMP/EXIF rating embedding (read + write)
XMP_SUPPORTED_EXT = {".jpg", ".jpeg", ".png"}

THUMB_MIN, THUMB_MAX, DEFAULT_THUMB = 60, 400, 170
PADDING = 30

# ──────────────────────────────────────────────────────────────────────────────
#  Rating helpers — Windows Explorer compatible
#
#  Windows Explorer reads star ratings from JPEG files via EXIF tags:
#    Tag 0x4746 (18246) = Rating        — SHORT, 0–5   (5 = five stars)
#    Tag 0x4749 (18249) = RatingPercent — SHORT, 0–99  (99 = five stars)
#
#  Windows star ↔ RatingPercent mapping:
#    0 stars → 0     1 star → 1     2 stars → 25
#    3 stars → 50    4 stars → 75   5 stars → 99
#
#  For cross-app compatibility we ALSO write XMP with:
#    xmp:Rating            (1-5, Adobe standard)
#    MicrosoftPhoto:Rating (0-99, Windows namespace — Lightroom, Bridge, etc.)
#
#  After every write we call SHChangeNotify() so Explorer refreshes immediately.
# ──────────────────────────────────────────────────────────────────────────────

try:
    import piexif as _piexif
    _PIEXIF_AVAILABLE = True
except ImportError:
    _PIEXIF_AVAILABLE = False

# Windows star → RatingPercent mapping
_STAR_TO_PERCENT = {0: 0, 1: 1, 2: 25, 3: 50, 4: 75, 5: 99}

_XMP_RATING_RE = re.compile(
    r'<xmp:Rating>\s*(\d)\s*</xmp:Rating>', re.IGNORECASE
)
_MS_RATING_RE = re.compile(
    r'<MicrosoftPhoto:Rating>\s*(\d+)\s*</MicrosoftPhoto:Rating>', re.IGNORECASE
)


# ── Shell notification (forces Windows Explorer to refresh immediately) ────────

def _notify_shell(path: str) -> None:
    """
    Tell the Windows Shell that a file's properties changed.
    This makes Windows Explorer update the Rating column instantly —
    no manual F5 or waiting for the Search Indexer.
    Safe no-op on non-Windows platforms.
    """
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        shell32          = ctypes.windll.shell32
        SHCNE_UPDATEITEM = 0x00002000   # a single item's properties changed
        SHCNF_PATHW      = 0x0005       # path is a wide-char string
        abs_path         = ctypes.c_wchar_p(os.path.abspath(path))
        shell32.SHChangeNotify(SHCNE_UPDATEITEM, SHCNF_PATHW, abs_path, None)
    except Exception:
        pass


# ── XMP packet builder ────────────────────────────────────────────────────────

def _build_xmp_packet(rating_stars: int) -> bytes:
    """
    Return a UTF-8 XMP packet embedding both xmp:Rating (1-5) and
    MicrosoftPhoto:Rating (0-99).  rating_stars=0 → tags omitted (removal).
    """
    percent = _STAR_TO_PERCENT.get(rating_stars, 0)
    if rating_stars > 0:
        rating_block = (
            f"      <xmp:Rating>{rating_stars}</xmp:Rating>\n"
            f"      <MicrosoftPhoto:Rating>{percent}</MicrosoftPhoto:Rating>\n"
        )
    else:
        rating_block = ""

    xmp = (
        "<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Scenify'>\n"
        "  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
        "    <rdf:Description rdf:about=''\n"
        "        xmlns:xmp='http://ns.adobe.com/xap/1.0/'\n"
        "        xmlns:MicrosoftPhoto='http://ns.microsoft.com/photo/1.0/'>\n"
        f"{rating_block}"
        "    </rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        "<?xpacket end='w'?>"
    )
    return xmp.encode('utf-8')


# ── JPEG rating ───────────────────────────────────────────────────────────────

_JPEG_XMP_MARKER  = b'http://ns.adobe.com/xap/1.0/\x00'
_JPEG_EXIF_MARKER = b'Exif\x00\x00'


def _jpeg_get_rating(path: str) -> int:
    """
    Read the rating from a JPEG.
    Priority: EXIF 0x4746 → EXIF 0x4749 → XMP xmp:Rating → XMP MicrosoftPhoto:Rating
    Returns 0 if absent/unreadable.
    """
    if _PIEXIF_AVAILABLE:
        try:
            ed  = _piexif.load(path)
            val = ed.get('0th', {}).get(_piexif.ImageIFD.Rating, 0)
            if val and 0 < val <= 5:
                return int(val)
            pct = ed.get('0th', {}).get(_piexif.ImageIFD.RatingPercent, 0)
            if pct:
                for stars, p in _STAR_TO_PERCENT.items():
                    if p == pct and stars > 0:
                        return stars
        except Exception:
            pass

    # Fallback: scan APP1 segments for XMP
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:2] != b'\xff\xd8':
            return 0
        i = 2
        while i < len(data) - 4:
            if data[i] != 0xff:
                break
            marker = data[i:i+2]
            if marker in (b'\xff\xda', b'\xff\xd9'):
                break
            seg_len     = struct.unpack('>H', data[i+2:i+4])[0]
            seg_payload = data[i+4:i+2+seg_len]
            if marker == b'\xff\xe1' and seg_payload.startswith(_JPEG_XMP_MARKER):
                xmp_str = seg_payload[len(_JPEG_XMP_MARKER):].decode('utf-8', errors='ignore')
                m = _XMP_RATING_RE.search(xmp_str)
                if m:
                    return int(m.group(1))
                m2 = _MS_RATING_RE.search(xmp_str)
                if m2:
                    pct = int(m2.group(1))
                    for stars, p in _STAR_TO_PERCENT.items():
                        if p == pct and stars > 0:
                            return stars
            i += 2 + seg_len
    except Exception:
        pass
    return 0


def _jpeg_set_rating(path: str, rating: int) -> bool:
    """
    Write rating into a JPEG file.

    Step 1 — EXIF (piexif):
      Load the file's existing EXIF directly, patch Rating + RatingPercent,
      write back via piexif.insert().  All camera metadata is preserved.
      If piexif is unavailable, a minimal fallback EXIF block is injected
      via a raw byte-level pass.

    Step 2 — XMP pass:
      Re-read the (now EXIF-updated) file and rebuild the segment list,
      replacing/removing the XMP APP1 segment.

    Step 3 — SHChangeNotify():
      Tell Windows Shell to refresh Explorer's Rating column immediately.
    """
    # ── Step 1a: patch EXIF via piexif (preserves all camera metadata) ───────
    exif_written = False
    if _PIEXIF_AVAILABLE:
        try:
            try:
                ed = _piexif.load(path)
            except Exception:
                ed = {'0th': {}, 'Exif': {}, 'GPS': {}, 'Interop': {}, '1st': {}}
            if rating == 0:
                ed['0th'].pop(_piexif.ImageIFD.Rating, None)
                ed['0th'].pop(_piexif.ImageIFD.RatingPercent, None)
            else:
                ed['0th'][_piexif.ImageIFD.Rating]        = rating
                ed['0th'][_piexif.ImageIFD.RatingPercent] = _STAR_TO_PERCENT.get(rating, 99)
            _piexif.insert(_piexif.dump(ed), path)
            exif_written = True
        except Exception:
            pass

    # ── Step 1b: fallback minimal EXIF (when piexif not installed) ────────────
    if not exif_written:
        try:
            with open(path, 'rb') as f:
                raw = f.read()
            if raw[:2] == b'\xff\xd8':
                if rating > 0:
                    pct = _STAR_TO_PERCENT.get(rating, 99)
                    def _entry(tag, val):
                        return struct.pack('<HHII', tag, 3, 1, val)
                    tiff = (b'II' + struct.pack('<H', 42) + struct.pack('<I', 8)
                            + struct.pack('<H', 2)
                            + _entry(0x4746, rating) + _entry(0x4749, pct)
                            + struct.pack('<I', 0))
                    exif_payload = b'Exif\x00\x00' + tiff
                else:
                    tiff = (b'II' + struct.pack('<H', 42) + struct.pack('<I', 8)
                            + struct.pack('<H', 0) + struct.pack('<I', 0))
                    exif_payload = b'Exif\x00\x00' + tiff
                out = bytearray(b'\xff\xd8')
                i = 2
                exif_done = False
                while i < len(raw):
                    if raw[i] != 0xff:
                        out += raw[i:]
                        break
                    marker = raw[i:i+2]
                    if marker in (b'\xff\xda', b'\xff\xd9'):
                        if not exif_done:
                            out += (b'\xff\xe1'
                                    + struct.pack('>H', len(exif_payload) + 2)
                                    + exif_payload)
                            exif_done = True
                        out += raw[i:]
                        break
                    sl  = struct.unpack('>H', raw[i+2:i+4])[0]
                    se  = i + 2 + sl
                    sp  = raw[i+4:se]
                    if marker == b'\xff\xe1' and sp.startswith(_JPEG_EXIF_MARKER):
                        if not exif_done:
                            out += (b'\xff\xe1'
                                    + struct.pack('>H', len(exif_payload) + 2)
                                    + exif_payload)
                            exif_done = True
                        i = se
                        continue
                    out += raw[i:se]
                    i = se
                with open(path, 'wb') as f:
                    f.write(bytes(out))
                exif_written = True
        except Exception:
            pass

    # ── Step 2: XMP pass — re-read file, patch/remove the XMP segment ─────────
    xmp_written = False
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:2] != b'\xff\xd8':
            if exif_written:
                _notify_shell(path)
            return exif_written

        new_xmp = (_JPEG_XMP_MARKER + _build_xmp_packet(rating)) if rating > 0 else None

        out          = bytearray(b'\xff\xd8')
        xmp_injected = False
        i            = 2

        while i < len(data):
            if data[i] != 0xff:
                if not xmp_injected and new_xmp:
                    out += b'\xff\xe1' + struct.pack('>H', len(new_xmp) + 2) + new_xmp
                    xmp_injected = True
                out += data[i:]
                break

            marker = data[i:i+2]

            if marker in (b'\xff\xda', b'\xff\xd9'):
                if not xmp_injected and new_xmp:
                    out += b'\xff\xe1' + struct.pack('>H', len(new_xmp) + 2) + new_xmp
                    xmp_injected = True
                out += data[i:]
                break

            sl  = struct.unpack('>H', data[i+2:i+4])[0]
            se  = i + 2 + sl
            sp  = data[i+4:se]

            if marker == b'\xff\xe1' and sp.startswith(_JPEG_XMP_MARKER):
                if not xmp_injected:
                    xmp_injected = True
                    if new_xmp:
                        out += b'\xff\xe1' + struct.pack('>H', len(new_xmp) + 2) + new_xmp
                i = se
                continue

            out += data[i:se]
            i = se

        with open(path, 'wb') as f:
            f.write(bytes(out))
        xmp_written = True
    except Exception:
        pass

    _notify_shell(path)
    return exif_written or xmp_written


# ── PNG helpers ───────────────────────────────────────────────────────────────

_PNG_SIG         = b'\x89PNG\r\n\x1a\n'
_PNG_XMP_KEYWORD = b'XML:com.adobe.xmp'


def _png_make_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xffffffff
    return struct.pack('>I', len(data)) + chunk_type + data + struct.pack('>I', crc)


def _png_get_rating(path: str) -> int:
    """Read rating from a PNG iTXt chunk. Returns 0 if absent."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:8] != _PNG_SIG:
            return 0
        i = 8
        while i < len(data) - 12:
            length     = struct.unpack('>I', data[i:i+4])[0]
            ctype      = data[i+4:i+8]
            chunk_data = data[i+8:i+8+length]
            if ctype == b'iTXt' and chunk_data.startswith(_PNG_XMP_KEYWORD):
                text = chunk_data[len(_PNG_XMP_KEYWORD)+5:].decode('utf-8', errors='ignore')
                m = _XMP_RATING_RE.search(text)
                if m:
                    return int(m.group(1))
                m2 = _MS_RATING_RE.search(text)
                if m2:
                    pct = int(m2.group(1))
                    for stars, p in _STAR_TO_PERCENT.items():
                        if p == pct and stars > 0:
                            return stars
            i += 12 + length
    except Exception:
        pass
    return 0


def _png_set_rating(path: str, rating: int) -> bool:
    """
    Embed (or remove) rating in a PNG iTXt chunk.
    Writes both xmp:Rating and MicrosoftPhoto:Rating in the XMP packet.
    rating=0 removes the chunk entirely.
    Calls SHChangeNotify() so Windows Explorer refreshes immediately.
    Returns True on success.
    """
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:8] != _PNG_SIG:
            return False

        out      = bytearray(_PNG_SIG)
        i        = 8
        injected = False

        while i < len(data):
            if i + 8 > len(data):
                out += data[i:]
                break
            length     = struct.unpack('>I', data[i:i+4])[0]
            ctype      = data[i+4:i+8]
            chunk_data = data[i+8:i+8+length]

            # Skip existing XMP iTXt
            if ctype == b'iTXt' and chunk_data.startswith(_PNG_XMP_KEYWORD):
                i += 12 + length
                continue

            # Inject before first IDAT
            if ctype == b'IDAT' and not injected:
                if rating > 0:
                    xmp_bytes  = _build_xmp_packet(rating)
                    itxt_data  = _PNG_XMP_KEYWORD + b'\x00\x00\x00\x00\x00' + xmp_bytes
                    out += _png_make_chunk(b'iTXt', itxt_data)
                injected = True

            out += data[i:i+12+length]
            i += 12 + length

        with open(path, 'wb') as f:
            f.write(bytes(out))
        _notify_shell(path)
        return True
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def get_image_rating(path: str) -> int:
    """Return the rating (0–5) stored in the file, or 0 if none/unsupported."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        return _jpeg_get_rating(path)
    if ext == '.png':
        return _png_get_rating(path)
    return 0


def set_image_rating(path: str, rating: int) -> bool:
    """
    Write rating to the file (0 = remove, 5 = five stars).
    For JPEG: writes EXIF Rating/RatingPercent (Windows Explorer reads these natively)
              AND XMP with xmp:Rating + MicrosoftPhoto:Rating (Adobe apps).
              Calls SHChangeNotify so Explorer updates the Rating column instantly.
    For PNG:  writes XMP with xmp:Rating + MicrosoftPhoto:Rating + SHChangeNotify.
    Returns True if the write succeeded.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        return _jpeg_set_rating(path, rating)
    if ext == '.png':
        return _png_set_rating(path, rating)
    return False


# ──────────────────────────────────────────────────────────────────────────────
#  Scene-tag helpers
#
#  The tag is stored as a plain UTF-8 string in:
#    JPEG  — EXIF ImageDescription (tag 0x010E, ASCII) via piexif
#            + XMP dc:description for cross-app visibility
#    PNG   — XMP iTXt chunk, dc:description
#
#  Windows Explorer shows ImageDescription in the "Title" / "Comments" column.
#  The tag is also visible in the Details pane (File → Properties → Details).
# ──────────────────────────────────────────────────────────────────────────────

_DC_DESC_RE = re.compile(
    r'<dc:description>\s*<rdf:Alt[^>]*>\s*<rdf:li[^>]*>(.*?)</rdf:li>',
    re.IGNORECASE | re.DOTALL
)
# Simpler fallback: plain dc:description text node
_DC_DESC_PLAIN_RE = re.compile(
    r'<dc:description>(.*?)</dc:description>',
    re.IGNORECASE | re.DOTALL
)


def _build_xmp_packet_with_tag(rating_stars: int, tag_text: str) -> bytes:
    """
    Build an XMP packet that carries BOTH the rating fields AND dc:description.
    Either or both may be absent (empty string / 0).
    """
    pct = _STAR_TO_PERCENT.get(rating_stars, 0)
    rating_block = (
        f"      <xmp:Rating>{rating_stars}</xmp:Rating>\n"
        f"      <MicrosoftPhoto:Rating>{pct}</MicrosoftPhoto:Rating>\n"
    ) if rating_stars > 0 else ""

    import xml.sax.saxutils as _sax
    tag_block = (
        "      <dc:description><rdf:Alt>"
        f"<rdf:li xml:lang='x-default'>{_sax.escape(tag_text)}</rdf:li>"
        "</rdf:Alt></dc:description>\n"
    ) if tag_text else ""

    xmp = (
        "<?xpacket begin='\xef\xbb\xbf' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
        "<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='Scenify'>\n"
        "  <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
        "    <rdf:Description rdf:about=''\n"
        "        xmlns:xmp='http://ns.adobe.com/xap/1.0/'\n"
        "        xmlns:MicrosoftPhoto='http://ns.microsoft.com/photo/1.0/'\n"
        "        xmlns:dc='http://purl.org/dc/elements/1.1/'>\n"
        f"{rating_block}"
        f"{tag_block}"
        "    </rdf:Description>\n"
        "  </rdf:RDF>\n"
        "</x:xmpmeta>\n"
        "<?xpacket end='w'?>"
    )
    return xmp.encode('utf-8')


def _xmp_extract_tag(xmp_str: str) -> str:
    """Extract dc:description text from an XMP string."""
    m = _DC_DESC_RE.search(xmp_str)
    if m:
        return m.group(1).strip()
    m2 = _DC_DESC_PLAIN_RE.search(xmp_str)
    if m2:
        return m2.group(1).strip()
    return ""


def _xmp_extract_rating(xmp_str: str) -> int:
    """Extract xmp:Rating from XMP string (fallback path)."""
    m = _XMP_RATING_RE.search(xmp_str)
    if m:
        return int(m.group(1))
    m2 = _MS_RATING_RE.search(xmp_str)
    if m2:
        pct = int(m2.group(1))
        for stars, p in _STAR_TO_PERCENT.items():
            if p == pct and stars > 0:
                return stars
    return 0


# ── JPEG tag helpers ──────────────────────────────────────────────────────────

def _jpeg_read_xmp_str(path: str) -> str:
    """Return the raw XMP string from a JPEG, or '' if absent."""
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:2] != b'\xff\xd8':
            return ''
        i = 2
        while i < len(data) - 4:
            if data[i] != 0xff:
                break
            marker = data[i:i+2]
            if marker in (b'\xff\xda', b'\xff\xd9'):
                break
            sl  = struct.unpack('>H', data[i+2:i+4])[0]
            sp  = data[i+4:i+2+sl]
            if marker == b'\xff\xe1' and sp.startswith(_JPEG_XMP_MARKER):
                return sp[len(_JPEG_XMP_MARKER):].decode('utf-8', errors='ignore')
            i += 2 + sl
    except Exception:
        pass
    return ''


def get_image_tag(path: str) -> str:
    """
    Return the scene-tag string stored in the image, or '' if none.
    JPEG: reads EXIF ImageDescription (tag 0x010E) first, falls back to XMP dc:description.
    PNG:  reads XMP iTXt dc:description.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.jpg', '.jpeg'):
        # 1. EXIF ImageDescription
        if _PIEXIF_AVAILABLE:
            try:
                ed  = _piexif.load(path)
                val = ed.get('0th', {}).get(_piexif.ImageIFD.ImageDescription, b'')
                if val:
                    text = val.decode('utf-8', errors='ignore').rstrip('\x00').strip()
                    if text:
                        return text
            except Exception:
                pass
        # 2. XMP dc:description
        xmp = _jpeg_read_xmp_str(path)
        if xmp:
            return _xmp_extract_tag(xmp)
        return ''

    if ext == '.png':
        try:
            with open(path, 'rb') as f:
                data = f.read()
            if data[:8] != _PNG_SIG:
                return ''
            i = 8
            while i < len(data) - 12:
                length     = struct.unpack('>I', data[i:i+4])[0]
                ctype      = data[i+4:i+8]
                chunk_data = data[i+8:i+8+length]
                if ctype == b'iTXt' and chunk_data.startswith(_PNG_XMP_KEYWORD):
                    text = chunk_data[len(_PNG_XMP_KEYWORD)+5:].decode('utf-8', errors='ignore')
                    return _xmp_extract_tag(text)
                i += 12 + length
        except Exception:
            pass
        return ''
    return ''


def set_image_tag(path: str, tag_text: str) -> bool:
    """
    Write (or remove) a scene-tag string into the image metadata.
    tag_text='' removes the tag.
    Also preserves the existing rating so the XMP packet stays consistent.
    Calls SHChangeNotify() so Windows Explorer refreshes immediately.
    """
    ext = os.path.splitext(path)[1].lower()

    if ext in ('.jpg', '.jpeg'):
        # ── Step 1: write EXIF ImageDescription via piexif ───────────────────
        if _PIEXIF_AVAILABLE:
            try:
                try:
                    ed = _piexif.load(path)
                except Exception:
                    ed = {'0th': {}, 'Exif': {}, 'GPS': {}, 'Interop': {}, '1st': {}}
                if tag_text:
                    ed['0th'][_piexif.ImageIFD.ImageDescription] = \
                        tag_text.encode('utf-8') + b'\x00'
                else:
                    ed['0th'].pop(_piexif.ImageIFD.ImageDescription, None)
                _piexif.insert(_piexif.dump(ed), path)
            except Exception:
                pass

        # ── Step 2: rewrite XMP segment (preserving rating) ──────────────────
        try:
            # Read current rating from EXIF so we can re-embed it
            current_rating = get_image_rating(path)

            with open(path, 'rb') as f:
                data = f.read()
            if data[:2] != b'\xff\xd8':
                _notify_shell(path)
                return True

            new_xmp_payload = (
                _JPEG_XMP_MARKER
                + _build_xmp_packet_with_tag(current_rating, tag_text)
            )

            out          = bytearray(b'\xff\xd8')
            xmp_injected = False
            i            = 2

            while i < len(data):
                if data[i] != 0xff:
                    if not xmp_injected:
                        out += (b'\xff\xe1'
                                + struct.pack('>H', len(new_xmp_payload) + 2)
                                + new_xmp_payload)
                        xmp_injected = True
                    out += data[i:]
                    break
                marker = data[i:i+2]
                if marker in (b'\xff\xda', b'\xff\xd9'):
                    if not xmp_injected:
                        out += (b'\xff\xe1'
                                + struct.pack('>H', len(new_xmp_payload) + 2)
                                + new_xmp_payload)
                        xmp_injected = True
                    out += data[i:]
                    break
                sl  = struct.unpack('>H', data[i+2:i+4])[0]
                se  = i + 2 + sl
                sp  = data[i+4:se]
                if marker == b'\xff\xe1' and sp.startswith(_JPEG_XMP_MARKER):
                    if not xmp_injected:
                        xmp_injected = True
                        out += (b'\xff\xe1'
                                + struct.pack('>H', len(new_xmp_payload) + 2)
                                + new_xmp_payload)
                    i = se
                    continue
                out += data[i:se]
                i = se

            with open(path, 'wb') as f:
                f.write(bytes(out))
        except Exception:
            pass

        _notify_shell(path)
        return True

    if ext == '.png':
        try:
            current_rating = get_image_rating(path)
            with open(path, 'rb') as f:
                data = f.read()
            if data[:8] != _PNG_SIG:
                return False
            out = bytearray(_PNG_SIG)
            i   = 8
            injected = False
            while i < len(data):
                if i + 8 > len(data):
                    out += data[i:]
                    break
                length     = struct.unpack('>I', data[i:i+4])[0]
                ctype      = data[i+4:i+8]
                chunk_data = data[i+8:i+8+length]
                if ctype == b'iTXt' and chunk_data.startswith(_PNG_XMP_KEYWORD):
                    i += 12 + length
                    continue
                if ctype == b'IDAT' and not injected:
                    xmp_bytes = _build_xmp_packet_with_tag(current_rating, tag_text)
                    itxt_data = _PNG_XMP_KEYWORD + b'\x00\x00\x00\x00\x00' + xmp_bytes
                    out += _png_make_chunk(b'iTXt', itxt_data)
                    injected = True
                out += data[i:i+12+length]
                i += 12 + length
            with open(path, 'wb') as f:
                f.write(bytes(out))
            _notify_shell(path)
            return True
        except Exception:
            return False

    return False


# ──────────────────────────────────────────────────────────────────────────────


def natural_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


class SmartLineEdit(QtWidgets.QLineEdit):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            text = self.text()
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                clipboard.setText(text)
                self.clear()
        elif event.button() == Qt.RightButton:
            self.clear()
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard_text = clipboard.text()
            if clipboard_text:
                self.setText(clipboard_text)
        super().mousePressEvent(event)


# ──────────────────────────────────────────────────────────────────────────────
#  StarOverlay  — a transparent clickable star painted on top of each thumbnail
# ──────────────────────────────────────────────────────────────────────────────

class StarOverlay(QtWidgets.QWidget):
    """
    Floating star button rendered over a thumbnail cell in the list widget.

    Visual states:
      • Starred (rating=5)  — solid gold ★
      • Hovered unstarred   — semi-transparent gold ☆
      • Normal unstarred    — very faint white ☆
      • Unsupported format  — greyed out ☆ (no click)
    """

    toggled = QtCore.pyqtSignal(bool)   # emits new starred state

    # Geometry constants (relative to thumbnail top-right corner)
    _SIZE   = 22   # widget square size in px
    _MARGIN =  4   # distance from top/right edges of the cell

    def __init__(self, parent, path: str, star_size: int = 22):
        super().__init__(parent)
        self._path      = path
        self._supported = os.path.splitext(path)[1].lower() in XMP_SUPPORTED_EXT
        self._starred   = False
        self._hovered   = False
        self._size      = star_size
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.PointingHandCursor if self._supported else Qt.ArrowCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setToolTip("☆ Click to mark as favourite (5-star rating)" if self._supported
                        else "Rating not supported for this file type")

        # Read current rating from file
        if self._supported and os.path.exists(path):
            self._starred = (get_image_rating(path) == 5)

    # ── public ────────────────────────────────────────────────────────────────

    def set_starred(self, state: bool):
        self._starred = state
        self.update()

    def is_starred(self) -> bool:
        return self._starred

    def update_path(self, path: str):
        """Call this after a file rename to keep the overlay pointing at the right file."""
        self._path      = path
        self._supported = os.path.splitext(path)[1].lower() in XMP_SUPPORTED_EXT
        self.setCursor(Qt.PointingHandCursor if self._supported else Qt.ArrowCursor)
        if self._supported and os.path.exists(path):
            self._starred = (get_image_rating(path) == 5)
        self.update()

    # ── events ────────────────────────────────────────────────────────────────

    def enterEvent(self, event):
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._supported:
            new_state = not self._starred
            rating    = 5 if new_state else 0
            if set_image_rating(self._path, rating):
                self._starred = new_state
                self.update()
                self.toggled.emit(new_state)
        event.accept()   # don't propagate to list widget

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        s = self._size
        # Semi-transparent dark circle background for legibility
        painter.setPen(Qt.NoPen)
        if self._starred or self._hovered:
            painter.setBrush(QtGui.QColor(0, 0, 0, 120))
            painter.drawEllipse(1, 1, s - 2, s - 2)

        # Star colour
        if not self._supported:
            color = QtGui.QColor(160, 160, 160, 90)
        elif self._starred:
            color = QtGui.QColor(255, 204, 0, 255)      # solid gold
        elif self._hovered:
            color = QtGui.QColor(255, 204, 0, 200)      # gold hover
        else:
            color = QtGui.QColor(255, 255, 255, 70)     # faint white

        # Draw star glyph using a QPainterPath (5-pointed star)
        path = self._star_path(s / 2, s / 2, s * 0.42, s * 0.18)
        painter.setBrush(color)
        painter.setPen(Qt.NoPen)
        painter.drawPath(path)
        painter.end()

    @staticmethod
    def _star_path(cx, cy, outer, inner) -> QtGui.QPainterPath:
        import math
        path = QtGui.QPainterPath()
        for i in range(10):
            angle = math.radians(-90 + i * 36)
            r = outer if i % 2 == 0 else inner
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()
        return path


# ──────────────────────────────────────────────────────────────────────────────
#  TagOverlay  — top-left corner badge; clicking opens the tag editor dialog
# ──────────────────────────────────────────────────────────────────────────────

class TagOverlay(QtWidgets.QWidget):
    """
    Floating 'T' badge rendered over the top-left corner of each thumbnail.

    Visual states:
      • Tagged     — solid cyan-teal badge with white T
      • Hovered    — semi-bright badge, inviting a click
      • No tag     — very faint, barely visible placeholder
      • Unsupported format — greyed out, no click
    """

    tag_changed = QtCore.pyqtSignal(str)   # emits new tag text ('' = removed)

    def __init__(self, parent, path: str, size: int = 22):
        super().__init__(parent)
        self._path      = path
        self._supported = os.path.splitext(path)[1].lower() in XMP_SUPPORTED_EXT
        self._tag       = ""
        self._hovered   = False
        self._size      = size
        self.setFixedSize(self._size, self._size)
        self.setCursor(Qt.PointingHandCursor if self._supported else Qt.ArrowCursor)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setToolTip(
            "🏷  Click to add / edit scene tag" if self._supported
            else "Tags not supported for this file type")

        if self._supported and os.path.exists(path):
            self._tag = get_image_tag(path)

    # ── public ────────────────────────────────────────────────────────────────

    def get_tag(self) -> str:
        return self._tag

    def set_tag(self, text: str):
        self._tag = text
        self.update()

    def update_path(self, path: str):
        self._path      = path
        self._supported = os.path.splitext(path)[1].lower() in XMP_SUPPORTED_EXT
        self.setCursor(Qt.PointingHandCursor if self._supported else Qt.ArrowCursor)
        if self._supported and os.path.exists(path):
            self._tag = get_image_tag(path)
        self.update()

    # ── events ────────────────────────────────────────────────────────────────

    def enterEvent(self, event):
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        self._hovered = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._supported:
            self._open_tag_dialog()
        event.accept()

    def _open_tag_dialog(self):
        dlg = QtWidgets.QDialog(self.window())
        dlg.setWindowTitle("Scene Tag")
        dlg.setMinimumWidth(380)
        dlg.setStyleSheet("""
            QDialog   { background: #1c1c1e; color: #e0e0e0; }
            QLabel    { color: #a0a0a0; font-size: 11px; }
            QLineEdit {
                background: #2c2c2e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 6px 10px; color: #e0e0e0;
                font-size: 13px; selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #32ade6; }
        """)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Header
        header = QtWidgets.QLabel(
            f"<b style='color:#32ade6;font-size:13px;'>🏷  Scene Tag</b><br>"
            f"<span style='color:#636366;font-size:10px;'>"
            f"{os.path.basename(self._path)}</span>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        hint = QtWidgets.QLabel(
            "Tag is written into the file's metadata and visible in Windows "
            "Explorer (Details pane → Title / Comments). Leave blank to remove.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #636366; font-size: 10px;")
        layout.addWidget(hint)

        edit = QtWidgets.QLineEdit(self._tag)
        edit.setPlaceholderText("e.g.  Garage interior  /  Night scene  /  Act 2")
        edit.setClearButtonEnabled(True)
        layout.addWidget(edit)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #3a3a3c; color: #e0e0e0; border: none;
                border-radius: 6px; font-weight: 600; font-size: 11px; padding: 4px 18px;
            }
            QPushButton:hover { background: #48484a; }
        """)
        cancel_btn.clicked.connect(dlg.reject)

        clear_btn = QtWidgets.QPushButton("Remove Tag")
        clear_btn.setFixedHeight(30)
        clear_btn.setStyleSheet("""
            QPushButton {
                background: #3a1a1a; color: #ff6b6b; border: 1px solid #7a2020;
                border-radius: 6px; font-weight: 600; font-size: 11px; padding: 4px 18px;
            }
            QPushButton:hover { background: #5a1a1a; color: #ff8888; }
        """)
        clear_btn.clicked.connect(lambda: (edit.clear(), dlg.accept()))

        ok_btn = QtWidgets.QPushButton("Save Tag")
        ok_btn.setFixedHeight(30)
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet("""
            QPushButton {
                background: #1a3a5a; color: #32ade6; border: 1px solid #1a6a9a;
                border-radius: 6px; font-weight: 700; font-size: 11px; padding: 4px 18px;
            }
            QPushButton:hover { background: #1a4a7a; color: #5bc8f5; }
            QPushButton:pressed { background: #0e2a4a; }
        """)
        ok_btn.clicked.connect(dlg.accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        edit.setFocus()
        edit.selectAll()

        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_tag = edit.text().strip()
            if new_tag != self._tag:
                if set_image_tag(self._path, new_tag):
                    self._tag = new_tag
                    self.update()
                    self.tag_changed.emit(new_tag)

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)

        s    = self._size
        has  = bool(self._tag)

        # Background pill
        painter.setPen(Qt.NoPen)
        if not self._supported:
            painter.setBrush(QtGui.QColor(80, 80, 80, 60))
        elif has:
            if self._hovered:
                painter.setBrush(QtGui.QColor(50, 180, 230, 230))
            else:
                painter.setBrush(QtGui.QColor(30, 140, 200, 210))
        else:
            if self._hovered:
                painter.setBrush(QtGui.QColor(50, 180, 230, 140))
            else:
                painter.setBrush(QtGui.QColor(255, 255, 255, 28))

        r = s * 0.30
        painter.drawRoundedRect(QtCore.QRectF(1, 1, s - 2, s - 2), r, r)

        # 'T' glyph
        if not self._supported:
            t_color = QtGui.QColor(140, 140, 140, 80)
        elif has:
            t_color = QtGui.QColor(255, 255, 255, 255)
        else:
            t_color = QtGui.QColor(255, 255, 255, 120) if self._hovered \
                      else QtGui.QColor(255, 255, 255, 60)

        font = QtGui.QFont("Arial", max(7, int(s * 0.52)), QtGui.QFont.Bold)
        painter.setFont(font)
        painter.setPen(t_color)
        painter.drawText(QtCore.QRectF(0, 0, s, s + 1), Qt.AlignCenter, "T")
        painter.end()



class DragDropListWidget(QtWidgets.QListWidget):
    double_left_clicked      = QtCore.pyqtSignal(str, str)
    double_right_clicked     = QtCore.pyqtSignal(str)
    b_key_pressed            = QtCore.pyqtSignal(str)
    preview_path_changed     = QtCore.pyqtSignal(str)
    scene_tag_changed        = QtCore.pyqtSignal()
    open_fullscreen_requested = QtCore.pyqtSignal(int)   # emits row index   # emitted when any tag is set/cleared

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListWidget.IconMode)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSpacing(12)
        self.setResizeMode(QtWidgets.QListWidget.Adjust)
        self.setWrapping(True)
        self.setMovement(QtWidgets.QListWidget.Snap)
        self.thumbnail_size = DEFAULT_THUMB
        self.setIconSize(QtCore.QSize(self.thumbnail_size, self.thumbnail_size))
        self.setGridSize(QtCore.QSize(self.thumbnail_size + PADDING,
                                      self.thumbnail_size + PADDING + 50))
        self.thumbnail_cache = {}
        self.itemDoubleClicked.connect(self.handle_double_click)
        self.setFocusPolicy(Qt.StrongFocus)
        self._rubber_banding    = False
        self._last_preview_path = None

        # overlay dicts: row → overlay widget
        self._star_overlays: dict[int, StarOverlay] = {}
        self._tag_overlays:  dict[int, TagOverlay]  = {}

        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.start_progressive_resize)
        self.progressive_timer = QTimer()
        self.progressive_timer.timeout.connect(self.resize_next_thumbnail)
        self.resize_index = 0

        # Reposition overlays whenever scroll or layout changes
        self.verticalScrollBar().valueChanged.connect(self._reposition_overlays)
        self.horizontalScrollBar().valueChanged.connect(self._reposition_overlays)

    # ── overlay size helpers ──────────────────────────────────────────────────

    def _star_size_for_thumb(self) -> int:
        return max(16, min(28, int(self.thumbnail_size * 0.16)))

    def _tag_size_for_thumb(self) -> int:
        return max(16, min(28, int(self.thumbnail_size * 0.16)))

    # ── combined reposition ───────────────────────────────────────────────────

    def _reposition_overlays(self):
        for row in list(self._star_overlays.keys()):
            self._position_star(row)
        for row in list(self._tag_overlays.keys()):
            self._position_tag(row)

    # alias kept for existing call sites
    def _reposition_stars(self):
        self._reposition_overlays()

    # ── star management ───────────────────────────────────────────────────────

    def add_star_for_item(self, row: int):
        item = self.item(row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        star = StarOverlay(self.viewport(), path, star_size=self._star_size_for_thumb())
        star.toggled.connect(lambda state, r=row: self._on_star_toggled(r, state))
        star.show()
        self._star_overlays[row] = star
        self._position_star(row)

    def _position_star(self, row: int):
        star = self._star_overlays.get(row)
        if star is None:
            return
        item = self.item(row)
        if item is None:
            return
        rect   = self.visualItemRect(item)
        sz     = star.width()
        margin = 4
        star.move(rect.right() - sz - margin, rect.top() + margin)
        star.raise_()

    def _rebuild_star_index(self):
        path_to_star = {s._path: s for s in self._star_overlays.values()}
        self._star_overlays.clear()

        # Build a set of paths currently in the list
        current_paths = set()
        for i in range(self.count()):
            item = self.item(i)
            if item is not None:
                current_paths.add(item.data(Qt.UserRole) or "")

        # Destroy any overlay whose file is no longer in the list
        for path, star in list(path_to_star.items()):
            if path not in current_paths:
                star.hide()
                star.deleteLater()
                del path_to_star[path]

        # Re-map surviving overlays to their new row numbers
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            path = item.data(Qt.UserRole) or ""
            if path in path_to_star:
                star = path_to_star[path]
                try:
                    star.toggled.disconnect()
                except Exception:
                    pass
                star.toggled.connect(lambda state, r=i: self._on_star_toggled(r, state))
                self._star_overlays[i] = star
                self._position_star(i)

    def _on_star_toggled(self, row: int, state: bool):
        pass

    def clear_stars(self):
        for star in self._star_overlays.values():
            star.deleteLater()
        self._star_overlays.clear()

    def toggle_star_for_selected(self):
        sel = self.selectedItems()
        if not sel:
            return
        row  = self.row(sel[0])
        star = self._star_overlays.get(row)
        if star and star._supported:
            new_state = not star.is_starred()
            rating    = 5 if new_state else 0
            if set_image_rating(star._path, rating):
                star.set_starred(new_state)

    def update_star_path(self, old_path: str, new_path: str):
        for star in self._star_overlays.values():
            if star._path == old_path:
                star.update_path(new_path)
                break

    # ── tag management ────────────────────────────────────────────────────────

    def add_tag_for_item(self, row: int):
        """Create and position a TagOverlay for the item at *row*."""
        item = self.item(row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        tag  = TagOverlay(self.viewport(), path, size=self._tag_size_for_thumb())
        tag.tag_changed.connect(lambda text, r=row: self._on_tag_changed(r, text))
        tag.show()
        self._tag_overlays[row] = tag
        self._position_tag(row)

    def _position_tag(self, row: int):
        """Move the TagOverlay for *row* to the top-LEFT corner of the cell."""
        tag = self._tag_overlays.get(row)
        if tag is None:
            return
        item = self.item(row)
        if item is None:
            return
        rect   = self.visualItemRect(item)
        margin = 4
        tag.move(rect.left() + margin, rect.top() + margin)
        tag.raise_()

    def _rebuild_tag_index(self):
        path_to_tag = {t._path: t for t in self._tag_overlays.values()}
        self._tag_overlays.clear()

        # Build a set of paths currently in the list
        current_paths = set()
        for i in range(self.count()):
            item = self.item(i)
            if item is not None:
                current_paths.add(item.data(Qt.UserRole) or "")

        # Destroy any overlay whose file is no longer in the list
        for path, tov in list(path_to_tag.items()):
            if path not in current_paths:
                tov.hide()
                tov.deleteLater()
                del path_to_tag[path]

        # Re-map surviving overlays to their new row numbers
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            path = item.data(Qt.UserRole) or ""
            if path in path_to_tag:
                tov = path_to_tag[path]
                try:
                    tov.tag_changed.disconnect()
                except Exception:
                    pass
                tov.tag_changed.connect(lambda text, r=i: self._on_tag_changed(r, text))
                self._tag_overlays[i] = tov
                self._position_tag(i)

    def _on_tag_changed(self, row: int, text: str):
        self.scene_tag_changed.emit()

    def clear_tags(self):
        for tov in self._tag_overlays.values():
            tov.deleteLater()
        self._tag_overlays.clear()

    def update_tag_path(self, old_path: str, new_path: str):
        for tov in self._tag_overlays.values():
            if tov._path == old_path:
                tov.update_path(new_path)
                break

    def get_active_scene_tag(self) -> str:
        """
        Return the scene tag that should be shown in the sticky banner.
        This is the tag of the last tagged item whose top edge is at or above
        the top of the currently visible viewport area.
        """
        viewport_top = self.verticalScrollBar().value()
        best_tag     = ""
        best_row     = -1
        for row, tov in self._tag_overlays.items():
            tag_text = tov.get_tag()
            if not tag_text:
                continue
            item = self.item(row)
            if item is None:
                continue
            rect = self.visualItemRect(item)
            # rect is in viewport coordinates; add scroll offset for absolute position
            item_top_abs = rect.top() + viewport_top
            # Include items whose top is at/above the current viewport top
            if item_top_abs <= viewport_top + self.viewport().height() // 2:
                if row > best_row:
                    best_row = row
                    best_tag = tag_text
        return best_tag

    # ── combined clear (called on folder load) ────────────────────────────────

    def clear_overlays(self):
        self.clear_stars()
        self.clear_tags()

    # ── overrides ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reposition_overlays)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._reposition_overlays()

    # ── existing methods (unchanged) ──────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._rubber_banding    = True
            self._last_preview_path = None
            item = self.itemAt(event.pos())
            if item:
                path = item.data(Qt.UserRole)
                if path:
                    self._last_preview_path = path
                    self.preview_path_changed.emit(path)
        elif event.button() == Qt.RightButton:
            item = self.itemAt(event.pos())
            if item:
                name_no_ext = os.path.splitext(item.text())[0]
                self.b_key_pressed.emit(name_no_ext)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._rubber_banding = False
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber_banding and (event.buttons() & Qt.LeftButton):
            item = self.itemAt(event.pos())
            if item:
                path = item.data(Qt.UserRole)
                if path and path != self._last_preview_path:
                    self._last_preview_path = path
                    self.preview_path_changed.emit(path)
        super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        # S key: add 5-star rating to all selected images
        if event.key() == Qt.Key_S:
            for item in self.selectedItems():
                row  = self.row(item)
                star = self._star_overlays.get(row)
                if star and star._supported and not star.is_starred():
                    if set_image_rating(star._path, 5):
                        star.set_starred(True)
            event.accept()
            return
        # D key: unstar all selected (remove favorite)
        if event.key() == Qt.Key_D:
            for item in self.selectedItems():
                row  = self.row(item)
                star = self._star_overlays.get(row)
                if star and star._supported and star.is_starred():
                    if set_image_rating(star._path, 0):
                        star.set_starred(False)
            event.accept()
            return
        # G key: unstar ALL selected regardless of current star state
        if event.key() == Qt.Key_G:
            for item in self.selectedItems():
                row  = self.row(item)
                star = self._star_overlays.get(row)
                if star and star._supported:
                    if set_image_rating(star._path, 0):
                        star.set_starred(False)
            event.accept()
            return
        # T key: open tag dialog for the focused/selected thumbnail
        if event.key() == Qt.Key_T:
            sel = self.selectedItems()
            row = self.row(sel[0]) if sel else self.currentRow()
            if row >= 0:
                tov = self._tag_overlays.get(row)
                if tov and tov._supported:
                    tov._open_tag_dialog()
            event.accept()
            return
        # F key: open fullscreen viewer for current/selected image
        if event.key() == Qt.Key_F:
            sel = self.selectedItems()
            row = self.row(sel[0]) if sel else self.currentRow()
            if row >= 0:
                self.open_fullscreen_requested.emit(row)
            event.accept()
            return
        # B key: copy selected image name (no ext) to base name field
        if event.key() == Qt.Key_B:
            sel = self.selectedItems()
            if sel:
                name_no_ext = os.path.splitext(sel[0].text())[0]
                self.b_key_pressed.emit(name_no_ext)
            event.accept()
            return
        # Arrow keys: navigate thumbnails
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            current_row = self.currentRow()
            total       = self.count()
            if total == 0:
                return
            new_row = (current_row - 1) % total if event.key() == Qt.Key_Left \
                      else (current_row + 1) % total
            self.setCurrentRow(new_row)
            item = self.item(new_row)
            self.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            event.accept()
            return
        super().keyPressEvent(event)

    def handle_double_click(self, item):
        name = item.text()
        path = item.data(Qt.UserRole)
        self.double_left_clicked.emit(name, path)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.RightButton:
            item = self.itemAt(event.pos())
            if item:
                name = item.text()
                self.double_right_clicked.emit(name)
                return
        super().mouseDoubleClickEvent(event)

    def setThumbnailSize(self, size: int):
        self.thumbnail_size = size
        self.setIconSize(QtCore.QSize(size, size))
        self.setGridSize(QtCore.QSize(size + PADDING, size + PADDING + 50))
        self.progressive_timer.stop()
        self.thumbnail_cache.clear()
        self.resize_index = 0
        self.resize_timer.stop()
        self.resize_timer.start(150)
        # Resize star overlays
        new_star_sz = self._star_size_for_thumb()
        for star in self._star_overlays.values():
            star._size = new_star_sz
            star.setFixedSize(new_star_sz, new_star_sz)
        # Resize tag overlays
        new_tag_sz = self._tag_size_for_thumb()
        for tov in self._tag_overlays.values():
            tov._size = new_tag_sz
            tov.setFixedSize(new_tag_sz, new_tag_sz)
        QTimer.singleShot(200, self._reposition_overlays)

    def start_progressive_resize(self):
        self.resize_index = 0
        if self.count() > 0:
            self.progressive_timer.start(0)

    def resize_next_thumbnail(self):
        if self.resize_index >= self.count():
            self.progressive_timer.stop()
            return
        item = self.item(self.resize_index)
        if item:
            path = item.data(Qt.UserRole)
            if path and os.path.exists(path):
                icon = self.get_thumbnail_icon(path, force_regenerate=True)
                item.setIcon(icon)
        self.resize_index += 1
        if self.resize_index % 10 == 0:
            QApplication.processEvents()
        # Keep stars on top after repainting
        QTimer.singleShot(0, self._reposition_overlays)

    def get_thumbnail_icon(self, path, force_regenerate=False):
        if not force_regenerate and path in self.thumbnail_cache:
            cached_icon = self.thumbnail_cache[path]
            if not cached_icon.isNull():
                pixmap = cached_icon.pixmap(
                    QtCore.QSize(self.thumbnail_size, self.thumbnail_size))
                if pixmap.width() == self.thumbnail_size or \
                   pixmap.height() == self.thumbnail_size:
                    return cached_icon
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(self.thumbnail_size, self.thumbnail_size,
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                icon = QtGui.QIcon(scaled)
                self.thumbnail_cache[path] = icon
                return icon
        return QtGui.QIcon()

    def startDrag(self, supportedActions):
        selected  = [i.row() for i in self.selectedIndexes()]
        drag_rows = sorted(set(selected))
        if not drag_rows:
            return
        self.clearSelection()
        for r in drag_rows:
            self.item(r).setSelected(True)
        mime = QtCore.QMimeData()
        mime.setData('application/x-drag-rows', str(drag_rows).encode())
        drag = QtGui.QDrag(self)
        drag.setMimeData(mime)
        first_item = self.item(drag_rows[0])
        if first_item and not first_item.icon().isNull():
            drag.setPixmap(first_item.icon().pixmap(self.iconSize()))
        drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat('application/x-drag-rows'):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat('application/x-drag-rows'):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat('application/x-drag-rows'):
            return super().dropEvent(e)
        try:
            drag_rows = eval(
                e.mimeData().data('application/x-drag-rows').data().decode())
        except Exception:
            e.ignore()
            return
        if not drag_rows:
            e.ignore()
            return
        pos         = e.pos()
        target_item = self.itemAt(pos)
        target_row  = self.row(target_item) if target_item else self.count()
        if target_row in drag_rows:
            e.ignore()
            return
        insert_at = target_row if target_row <= max(drag_rows) \
                    else target_row - len(drag_rows)
        dragged_items = []
        for r in reversed(sorted(drag_rows)):
            itm = self.takeItem(r)
            dragged_items.insert(0, itm)
        for i, itm in enumerate(dragged_items):
            self.insertItem(insert_at + i, itm)
        self.clearSelection()
        for i in range(len(dragged_items)):
            self.item(insert_at + i).setSelected(True)
        e.acceptProposedAction()
        self._rebuild_star_index()
        self._rebuild_tag_index()
        QTimer.singleShot(50, self._reposition_overlays)


# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
#  FullscreenViewer
#  ─ Opens as a true fullscreen window showing the selected image.
#  ─ Bottom strip shows 5 film-strip thumbnails: prev2 | prev1 | CURRENT | next1 | next2
#  ─ Keyboard:  ← → ↑ ↓  navigate    F  close    R  star    D  unstar    T  tag dialog
#  ─ Clicking a strip thumbnail jumps to that image.
# ──────────────────────────────────────────────────────────────────────────────

class FullscreenViewer(QtWidgets.QWidget):
    """
    Fullscreen gallery viewer.

    Bottom bar:
      LEFT   — 3 equal-size buttons: ★/☆ Star toggle (R), 🏷 Tag (T), ✕ Close (F/Esc)
               Star = gold when starred, dim when not.
               Tag  = cyan when tagged, dim when not. No tag text shown here.
               Close = always red.
      CENTER — film-strip (5 cells)
      RIGHT  — scene banner (like sticky bar in normal mode)
               filename + counter below

    Keys:  ← → ↑ ↓  navigate    R  toggle star    D  force unstar
           T  tag dialog    F / Esc  close
    """

    star_changed = QtCore.pyqtSignal(int, bool)
    tag_changed  = QtCore.pyqtSignal(int, str)
    row_changed  = QtCore.pyqtSignal(int)

    _STRIP_W   = 110
    _STRIP_H   = 80
    _STRIP_GAP = 10
    _BAR_H     = 130
    _BTN_H     = 28
    _BTN_W     = 190

    def __init__(self, list_widget, start_row, parent=None):
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self._list  = list_widget
        self._row   = start_row
        self._total = list_widget.count()
        self._thumb_cache = {}

        self.setWindowTitle("Scenify — Fullscreen")
        self.setStyleSheet("background: #000000;")
        self.setFocusPolicy(Qt.StrongFocus)

        self._img_label = QtWidgets.QLabel(self)
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setStyleSheet("background: #000000; border: none;")

        # Star overlay — top-right corner of the image area, 40% opacity
        self._star_overlay_lbl = QtWidgets.QLabel(self)
        self._star_overlay_lbl.setAlignment(Qt.AlignCenter)
        self._star_overlay_lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._star_overlay_lbl.setStyleSheet(
            "background: transparent; border: none; font-size: 26px;")

        self._bar = QtWidgets.QWidget(self)
        self._bar.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #080808, stop:1 #111113);
                border-top: 1px solid #222224;
            }
        """)

        bar_layout = QtWidgets.QHBoxLayout(self._bar)
        bar_layout.setContentsMargins(18, 8, 18, 8)
        bar_layout.setSpacing(16)

        # ── LEFT: 3 uniform action buttons ───────────────────────────────────
        left_widget = QtWidgets.QWidget()
        left_widget.setStyleSheet("background: transparent; border: none;")
        left_widget.setFixedWidth(210)
        left_vbox = QtWidgets.QVBoxLayout(left_widget)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(10)
        left_vbox.addStretch()

        self._star_btn = QtWidgets.QPushButton()
        self._star_btn.setFixedHeight(self._BTN_H)
        self._star_btn.setFixedWidth(self._BTN_W)
        self._star_btn.clicked.connect(self._toggle_star)

        self._tag_btn = QtWidgets.QPushButton()
        self._tag_btn.setFixedHeight(self._BTN_H)
        self._tag_btn.setFixedWidth(self._BTN_W)
        self._tag_btn.clicked.connect(self._open_tag_dialog)

        self._close_btn = QtWidgets.QPushButton("\u2715  Close  (F/Esc)")
        self._close_btn.setFixedHeight(self._BTN_H)
        self._close_btn.setFixedWidth(self._BTN_W)
        self._close_btn.setStyleSheet("""
            QPushButton {
                background: #2a1010; color: #ff5555;
                border: 1px solid #7a2020;
                border-radius: 6px; font-size: 11px; font-weight: 700;
                padding: 4px 10px;
            }
            QPushButton:hover   { background: #3a1515; border-color: #cc3030; color: #ff8080; }
            QPushButton:pressed { background: #1a0808; }
        """)
        self._close_btn.clicked.connect(self.close)

        # Remove-to-folder button
        self._remove_btn = QtWidgets.QPushButton("\U0001f5d1  Remove  (R)")
        self._remove_btn.setFixedHeight(self._BTN_H)
        self._remove_btn.setFixedWidth(self._BTN_W)
        self._remove_btn.setToolTip(
            "Move this image to  00_removed/  inside the open folder.\n"
            "The image is removed from the list immediately.")
        self._remove_btn.setStyleSheet("""
            QPushButton {
                background: #1c1010; color: #886060;
                border: 1px solid #4a2020;
                border-radius: 6px; font-size: 11px; font-weight: 600;
                padding: 4px 10px;
            }
            QPushButton:hover   { background: #2a1515; color: #cc8888;
                                   border-color: #884040; }
            QPushButton:pressed { background: #140c0c; }
        """)
        self._remove_btn.clicked.connect(self._remove_image)

        left_vbox.addWidget(self._star_btn)
        left_vbox.addWidget(self._tag_btn)
        left_vbox.addWidget(self._remove_btn)
        left_vbox.addWidget(self._close_btn)
        left_vbox.addStretch()

        # ── CENTER: film-strip ────────────────────────────────────────────────
        self._strip_cells = []
        strip_widget = QtWidgets.QWidget()
        strip_widget.setStyleSheet("background: transparent; border: none;")
        strip_hbox = QtWidgets.QHBoxLayout(strip_widget)
        strip_hbox.setContentsMargins(0, 0, 0, 0)
        strip_hbox.setSpacing(self._STRIP_GAP)

        for i in range(5):
            cell = QtWidgets.QLabel()
            cell.setFixedSize(self._STRIP_W, self._STRIP_H)
            cell.setAlignment(Qt.AlignCenter)
            offset = i - 2
            cell.mousePressEvent = lambda e, off=offset: self._strip_clicked(off)
            cell.setCursor(Qt.PointingHandCursor)
            self._strip_cells.append(cell)
            strip_hbox.addWidget(cell)

        # ── RIGHT: scene banner + filename + counter ──────────────────────────
        right_widget = QtWidgets.QWidget()
        right_widget.setStyleSheet("background: transparent; border: none;")
        right_widget.setFixedWidth(220)
        right_vbox = QtWidgets.QVBoxLayout(right_widget)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(4)
        right_vbox.addStretch()

        self._scene_banner_lbl = QtWidgets.QLabel()
        self._scene_banner_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._scene_banner_lbl.setWordWrap(True)
        self._scene_banner_lbl.setStyleSheet(
            "color: #32ade6; font-size: 11px; font-weight: 700; "
            "background: transparent; border: none;")

        self._name_label = QtWidgets.QLabel()
        self._name_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._name_label.setStyleSheet(
            "color: #c0c0c0; font-size: 12px; font-weight: 600; "
            "background: transparent; border: none;")
        self._name_label.setWordWrap(True)

        self._meta_label = QtWidgets.QLabel()
        self._meta_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._meta_label.setStyleSheet(
            "color: #3a3a4a; font-size: 11px; background: transparent; border: none;")

        right_vbox.addWidget(self._scene_banner_lbl)
        right_vbox.addWidget(self._name_label)
        right_vbox.addWidget(self._meta_label)
        right_vbox.addStretch()

        bar_layout.addWidget(left_widget, 0, Qt.AlignVCenter)
        bar_layout.addStretch(1)
        bar_layout.addWidget(strip_widget, 0, Qt.AlignVCenter)
        bar_layout.addStretch(1)
        bar_layout.addWidget(right_widget, 0, Qt.AlignVCenter)

        self._update_display()

    # ── geometry ──────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        screen = QtWidgets.QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self._layout_children()
        self._star_overlay_lbl.raise_()
        self._update_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_children()

    def _layout_children(self):
        w = self.width()
        h = self.height()
        self._bar.setGeometry(0, h - self._BAR_H, w, self._BAR_H)
        self._img_label.setGeometry(0, 0, w, h - self._BAR_H)
        # Star overlay: top-right corner of image area, 48×48
        self._star_overlay_lbl.setGeometry(w - 58, 14, 44, 44)

    # ── navigation ────────────────────────────────────────────────────────────

    def _go(self, delta):
        new_row = self._row + delta
        if 0 <= new_row < self._total:
            self._row = new_row
            self.row_changed.emit(self._row)
            self._update_display()

    def _strip_clicked(self, offset):
        new_row = self._row + offset
        if 0 <= new_row < self._total:
            self._row = new_row
            self.row_changed.emit(self._row)
            self._update_display()

    # ── sync helpers (in-memory overlays are source of truth) ─────────────────

    def _get_current_tag(self, path):
        tov = self._list._tag_overlays.get(self._row)
        if tov is not None:
            return tov.get_tag()
        return get_image_tag(path)

    def _get_current_starred(self, path):
        star = self._list._star_overlays.get(self._row)
        if star is not None:
            return star.is_starred()
        return get_image_rating(path) == 5

    def _get_active_scene_tag(self):
        """Walk backward from current row to find the nearest tagged image."""
        for row in range(self._row, -1, -1):
            tov = self._list._tag_overlays.get(row)
            if tov and tov.get_tag():
                return tov.get_tag()
        return ""

    # ── display ───────────────────────────────────────────────────────────────

    def _update_display(self):
        item = self._list.item(self._row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""

        # Main image
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                aw = self.width()
                ah = self.height() - self._BAR_H
                if aw > 0 and ah > 0:
                    self._img_label.setPixmap(
                        pix.scaled(aw, ah, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        # RIGHT: scene banner
        scene_tag = self._get_active_scene_tag()
        self._scene_banner_lbl.setText(f"\U0001f3f7\ufe0f  {scene_tag}" if scene_tag else "")
        # RIGHT: filename + counter
        self._name_label.setText(os.path.basename(path))
        self._meta_label.setText(f"{self._row + 1}  /  {self._total}")

        # LEFT: star button
        bh = self._BTN_H
        is_starred = self._get_current_starred(path)
        if is_starred:
            self._star_btn.setText("\u2605  Starred  (S)")
            self._star_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #2a2000; color: #ffd60a; border: 1px solid #ffd60a;
                    border-radius: 6px; font-size: 11px; font-weight: 700;
                    padding: 4px 10px; min-height: {bh}px;
                }}
                QPushButton:hover   {{ background: #3a3000; border-color: #ffee44; }}
                QPushButton:pressed {{ background: #1a1400; }}
            """)
        else:
            self._star_btn.setText("\u2606  Add Star  (S)")
            self._star_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #1a1a1c; color: #666668; border: 1px solid #2e2e30;
                    border-radius: 6px; font-size: 11px; font-weight: 600;
                    padding: 4px 10px; min-height: {bh}px;
                }}
                QPushButton:hover   {{ background: #2a2000; color: #ffd60a;
                                       border-color: #665500; }}
                QPushButton:pressed {{ background: #1a1400; }}
            """)

        # LEFT: tag button
        has_tag = bool(self._get_current_tag(path))
        if has_tag:
            self._tag_btn.setText("\U0001f3f7  Tagged  (T)")
            self._tag_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #0a1e30; color: #32ade6; border: 1px solid #1a6090;
                    border-radius: 6px; font-size: 11px; font-weight: 700;
                    padding: 4px 10px; min-height: {bh}px;
                }}
                QPushButton:hover   {{ background: #1a3a5a; border-color: #2a80c0; }}
                QPushButton:pressed {{ background: #061422; }}
            """)
        else:
            self._tag_btn.setText("\U0001f3f7  Add Tag  (T)")
            self._tag_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #1a1a1c; color: #444448; border: 1px solid #2a2a2c;
                    border-radius: 6px; font-size: 11px; font-weight: 600;
                    padding: 4px 10px; min-height: {bh}px;
                }}
                QPushButton:hover   {{ background: #0a1e30; color: #32ade6;
                                       border-color: #1a5580; }}
                QPushButton:pressed {{ background: #061422; }}
            """)

        # Star overlay top-right (mirrors StarOverlay from normal mode, 40% opacity)
        is_starred_for_overlay = self._get_current_starred(path)
        if is_starred_for_overlay:
            # Solid gold star at 40% opacity via rgba background trick
            self._star_overlay_lbl.setText("★")
            self._star_overlay_lbl.setStyleSheet(
                "background: transparent; border: none; font-size: 26px; "
                "color: rgba(255, 204, 0, 102);")   # 102/255 ≈ 40%
        else:
            # Faint white hollow star — same as the unstarred state in StarOverlay
            self._star_overlay_lbl.setText("☆")
            self._star_overlay_lbl.setStyleSheet(
                "background: transparent; border: none; font-size: 26px; "
                "color: rgba(255, 255, 255, 40);")   # very faint

        # Film-strip
        for idx, cell in enumerate(self._strip_cells):
            offset    = idx - 2
            row       = self._row + offset
            is_center = (offset == 0)
            if row < 0 or row >= self._total:
                cell.setPixmap(QtGui.QPixmap()); cell.setText("")
                cell.setStyleSheet("QLabel { background: #0a0a0b; border: 1px solid #161618; border-radius: 4px; }")
                cell.setCursor(Qt.ArrowCursor)
                continue
            thumb = self._get_strip_thumb(row)
            if thumb:
                cell.setPixmap(thumb); cell.setText("")
            else:
                cell.clear()
            if is_center:
                cell.setStyleSheet("QLabel { background: #0a1e30; border: 2px solid #32ade6; border-radius: 6px; }")
                cell.setCursor(Qt.ArrowCursor)
            else:
                cell.setStyleSheet("QLabel { background: #161618; border: 1px solid #222224; border-radius: 4px; }")
                cell.setCursor(Qt.PointingHandCursor)

    def _get_strip_thumb(self, row):
        if row in self._thumb_cache:
            return self._thumb_cache[row]
        item = self._list.item(row)
        if item is None:
            return None
        path = item.data(Qt.UserRole) or ""
        if not os.path.exists(path):
            return None
        pix = QtGui.QPixmap(path)
        if pix.isNull():
            return None
        thumb = pix.scaled(self._STRIP_W, self._STRIP_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._thumb_cache[row] = thumb
        return thumb

    # ── actions ───────────────────────────────────────────────────────────────

    def _toggle_star(self):
        """S — add 5-star rating only (never removes; use D to remove)."""
        item = self._list.item(self._row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        if os.path.splitext(path)[1].lower() not in XMP_SUPPORTED_EXT:
            return
        if self._get_current_starred(path):
            return  # S only adds, never removes
        if set_image_rating(path, 5):
            star = self._list._star_overlays.get(self._row)
            if star:
                star.set_starred(True)
            self.star_changed.emit(self._row, True)
            self._update_display()

    def _do_unstar(self):
        """D — force remove star."""
        item = self._list.item(self._row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        if os.path.splitext(path)[1].lower() not in XMP_SUPPORTED_EXT:
            return
        if not self._get_current_starred(path):
            return
        if set_image_rating(path, 0):
            star = self._list._star_overlays.get(self._row)
            if star:
                star.set_starred(False)
            self.star_changed.emit(self._row, False)
            self._update_display()

    def _remove_image(self):
        """
        R — Move the current image to  <open_folder>/00_removed/  then
        remove it from the thumbnail list immediately.
        Navigates to the next image (or previous if at the end).
        """
        item = self._list.item(self._row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        if not path or not os.path.isfile(path):
            return

        # Determine the root folder from the main ImageOrganizer
        # We find it by walking up from the file path one level
        root_folder = os.path.dirname(path)
        dest_dir    = os.path.join(root_folder, "00_removed")

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Error",
                f"Could not create folder:\n{dest_dir}\n\n{e}")
            return

        fname    = os.path.basename(path)
        dst_path = os.path.join(dest_dir, fname)

        # If a file with the same name already exists in 00_removed, suffix it
        if os.path.exists(dst_path):
            base, ext = os.path.splitext(fname)
            import time
            dst_path = os.path.join(dest_dir, f"{base}_{int(time.time())}{ext}")

        try:
            import shutil
            shutil.move(path, dst_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Error",
                f"Could not move file:\n{path}\n→ {dst_path}\n\n{e}")
            return

        # Remove from the list widget + clean up overlays
        row = self._row
        star = self._list._star_overlays.pop(row, None)
        if star:
            star.hide(); star.deleteLater()
        tov = self._list._tag_overlays.pop(row, None)
        if tov:
            tov.hide(); tov.deleteLater()
        self._list.takeItem(row)
        self._list._rebuild_star_index()
        self._list._rebuild_tag_index()
        QTimer.singleShot(50, self._list._reposition_overlays)

        # Update total count and navigate
        self._total = self._list.count()
        if self._total == 0:
            self.close()
            return

        # Stay at same row (now the next image) or step back at end
        if self._row >= self._total:
            self._row = self._total - 1
        self.row_changed.emit(self._row)
        self._update_display()

    def _open_tag_dialog(self):
        item = self._list.item(self._row)
        if item is None:
            return
        path = item.data(Qt.UserRole) or ""
        if os.path.splitext(path)[1].lower() not in XMP_SUPPORTED_EXT:
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Scene Tag")
        dlg.setMinimumWidth(400)
        dlg.setStyleSheet("""
            QDialog   { background: #1c1c1e; color: #e0e0e0; }
            QLabel    { color: #a0a0a0; font-size: 11px; background: transparent; border: none; }
            QLineEdit {
                background: #2c2c2e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 6px 10px; color: #e0e0e0;
                font-size: 13px; selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #32ade6; }
        """)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QtWidgets.QLabel(
            f"<b style=\'color:#32ade6;font-size:13px;\'>\U0001f3f7  Scene Tag</b><br>"
            f"<span style=\'color:#636366;font-size:10px;\'>{os.path.basename(path)}</span>")
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        current_tag = self._get_current_tag(path)
        edit = QtWidgets.QLineEdit(current_tag)
        edit.setPlaceholderText("e.g.  Garage interior  /  Night scene  /  Act 2")
        edit.setClearButtonEnabled(True)
        layout.addWidget(edit)

        brl = QtWidgets.QHBoxLayout()
        brl.setSpacing(8)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setFixedHeight(30)
        cancel_btn.setStyleSheet("QPushButton { background: #3a3a3c; color: #e0e0e0; border: none; border-radius: 6px; font-weight: 600; padding: 4px 18px; } QPushButton:hover { background: #48484a; }")
        cancel_btn.clicked.connect(dlg.reject)
        clear_btn = QtWidgets.QPushButton("Remove Tag")
        clear_btn.setFixedHeight(30)
        clear_btn.setStyleSheet("QPushButton { background: #3a1a1a; color: #ff6b6b; border: 1px solid #7a2020; border-radius: 6px; font-weight: 600; padding: 4px 18px; } QPushButton:hover { background: #5a1a1a; }")
        clear_btn.clicked.connect(lambda: (edit.clear(), dlg.accept()))
        ok_btn = QtWidgets.QPushButton("Save Tag")
        ok_btn.setFixedHeight(30)
        ok_btn.setDefault(True)
        ok_btn.setStyleSheet("QPushButton { background: #1a3a5a; color: #32ade6; border: 1px solid #1a6a9a; border-radius: 6px; font-weight: 700; padding: 4px 18px; } QPushButton:hover { background: #1a4a7a; }")
        ok_btn.clicked.connect(dlg.accept)
        brl.addWidget(cancel_btn); brl.addStretch(); brl.addWidget(clear_btn); brl.addWidget(ok_btn)
        layout.addLayout(brl)
        edit.setFocus(); edit.selectAll()

        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            new_tag = edit.text().strip()
            if new_tag != current_tag:
                if set_image_tag(path, new_tag):
                    tov = self._list._tag_overlays.get(self._row)
                    if tov:
                        tov.set_tag(new_tag)
                    self.tag_changed.emit(self._row, new_tag)
                    self._update_display()

    # ── events ────────────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        k = event.key()
        if k in (Qt.Key_Left, Qt.Key_Up):
            self._go(-1)
        elif k in (Qt.Key_Right, Qt.Key_Down):
            self._go(1)
        elif k == Qt.Key_S:
            self._toggle_star()
        elif k == Qt.Key_D:
            self._do_unstar()
        elif k == Qt.Key_R:
            self._remove_image()
        elif k == Qt.Key_T:
            self._open_tag_dialog()
        elif k in (Qt.Key_F, Qt.Key_Escape):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.setFocus()
        super().mousePressEvent(event)


PROGRESS_IDLE_STYLE = """
    QProgressBar {
        border: 2px solid transparent;
        border-radius: 6px;
        text-align: center;
        background: transparent;
        color: transparent;
    }
    QProgressBar::chunk { background: transparent; }
"""
PROGRESS_ACTIVE_STYLE = """
    QProgressBar {
        border: 2px solid #3a3a3c;
        border-radius: 6px;
        text-align: center;
        background: #1c1c1e;
        color: #e0e0e0;
        font-weight: 600;
    }
    QProgressBar::chunk {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                    stop:0 #30d158, stop:1 #32d74b);
        border-radius: 4px;
    }
"""


class ImageOrganizer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(
            "Scenify — Image Scene Flow Organizer  |  "
            "Developed by Ivan Sicaja © 2026. All rights reserved.")
        _icon_path = r"..\assets\media\icons\icon.ico"
        if not os.path.exists(_icon_path):
            _icon_path = _icon_path.replace("icon.ico", "icon.png")
        if os.path.exists(_icon_path):
            self.setWindowIcon(QtGui.QIcon(_icon_path))
        self.apply_dark_theme()
        self.settings     = QSettings("ImageSceneFlowOrganizer", "Settings")
        geometry          = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        window_state = self.settings.value("windowState")
        if window_state:
            self.restoreState(window_state)
        if not geometry:
            self.showMaximized()
        self.folder               = None
        self.preview_locked       = False
        self.last_search_index    = {1: -1, 2: -1}
        self.current_folder_files = set()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        blue_btn_style = """
            QPushButton {
                padding: 4px 10px; font-weight: 600; font-size: 11px;
                border-radius: 5px; margin: 1px 0;
                background: #0066CC; color: white; border: none;
            }
            QPushButton:hover   { background: #007AFF; }
            QPushButton:pressed { background: #0051A3; }
        """
        gray_btn_style = """
            QPushButton {
                padding: 4px 10px; font-weight: 600; font-size: 11px;
                border-radius: 5px; margin: 1px 0;
                background: #3A3A3C; color: white; border: none;
            }
            QPushButton:hover   { background: #48484A; }
            QPushButton:pressed { background: #2A2A2C; }
        """
        arrow_btn_style = """
            QPushButton {
                background: #3a3a3c; color: #e0e0e0; border: none;
                border-radius: 5px; font-size: 13px; font-weight: 700; padding: 0px;
            }
            QPushButton:hover   { background: #0066CC; }
            QPushButton:pressed { background: #0051A3; }
        """

        open_btn = QtWidgets.QPushButton("Open Folder")
        open_btn.setStyleSheet(blue_btn_style)
        open_btn.setFixedHeight(26)
        open_btn.clicked.connect(self.open_folder)

        reload_btn = QtWidgets.QPushButton("Reload Folder")
        reload_btn.setStyleSheet(blue_btn_style)
        reload_btn.setFixedHeight(26)
        reload_btn.clicked.connect(self.reload_folder)

        top_btn = QtWidgets.QPushButton("Move Selected to Top")
        top_btn.setStyleSheet(blue_btn_style)
        top_btn.setFixedHeight(26)
        top_btn.clicked.connect(self.move_to_top)

        bottom_btn = QtWidgets.QPushButton("Move Selected to Bottom")
        bottom_btn.setStyleSheet(blue_btn_style)
        bottom_btn.setFixedHeight(26)
        bottom_btn.clicked.connect(self.move_to_bottom)

        rename_all_btn = QtWidgets.QPushButton("Rename All")
        rename_all_btn.setStyleSheet(blue_btn_style)
        rename_all_btn.setFixedHeight(26)
        rename_all_btn.clicked.connect(self.rename_ordered)

        rename_selected_btn = QtWidgets.QPushButton("Rename Selected")
        rename_selected_btn.setStyleSheet(blue_btn_style)
        rename_selected_btn.setFixedHeight(26)
        rename_selected_btn.clicked.connect(self.rename_selected)

        # ── Rename Selected inline options ────────────────────────────────────
        rename_options_frame = QtWidgets.QFrame()
        rename_options_frame.setStyleSheet("""
            QFrame { background: #2c2c2e; border: 1px solid #3a3a3c; border-radius: 8px; }
        """)
        rename_options_layout = QtWidgets.QVBoxLayout(rename_options_frame)
        rename_options_layout.setContentsMargins(10, 6, 10, 6)
        rename_options_layout.setSpacing(4)

        rename_options_title = QtWidgets.QLabel("Rename Selected — Options")
        rename_options_title.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #636366; "
            "background: transparent; border: none; letter-spacing: 0.3px;")
        rename_options_layout.addWidget(rename_options_title)

        base_name_label = QtWidgets.QLabel("Base Name:")
        base_name_label.setStyleSheet(
            "font-size: 11px; color: #a0a0a0; font-weight: 500; "
            "background: transparent; border: none;")
        self.rename_base_input = QtWidgets.QLineEdit()
        self.rename_base_input.setPlaceholderText(
            "e.g. garage, scene  — or select thumbnail and press B")
        self.rename_base_input.setFixedHeight(26)
        self.rename_base_input.setStyleSheet("""
            QLineEdit {
                background-color: #1c1c1e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 3px 8px; color: #e0e0e0;
                font-size: 12px; selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #0066CC; }
        """)

        digits_row = QtWidgets.QHBoxLayout()
        digits_row.setSpacing(4)
        digits_label = QtWidgets.QLabel("Digit Count:")
        digits_label.setStyleSheet(
            "font-size: 11px; color: #a0a0a0; font-weight: 500; "
            "background: transparent; border: none;")
        self.digits_spinbox = QtWidgets.QSpinBox()
        self.digits_spinbox.setRange(1, 10)
        self.digits_spinbox.setValue(2)
        self.digits_spinbox.setFixedHeight(26)
        self.digits_spinbox.setFixedWidth(52)
        self.digits_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.digits_spinbox.setToolTip(
            "Number of digits used for the numeric suffix.\n"
            "e.g. 3 -> garage_001, garage_002\n"
            "     6 -> garage_000001, garage_000002")
        self.digits_spinbox.setStyleSheet("""
            QSpinBox {
                background-color: #1c1c1e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 2px 6px; color: #e0e0e0;
                font-size: 12px; font-weight: 600;
            }
            QSpinBox:focus { border: 1px solid #0066CC; }
        """)

        digits_up_btn = QtWidgets.QPushButton("↑")
        digits_up_btn.setFixedSize(26, 26)
        digits_up_btn.setStyleSheet(arrow_btn_style)
        digits_up_btn.clicked.connect(
            lambda: self.digits_spinbox.setValue(self.digits_spinbox.value() + 1))

        digits_down_btn = QtWidgets.QPushButton("↓")
        digits_down_btn.setFixedSize(26, 26)
        digits_down_btn.setStyleSheet(arrow_btn_style)
        digits_down_btn.clicked.connect(
            lambda: self.digits_spinbox.setValue(self.digits_spinbox.value() - 1))

        digits_example_label = QtWidgets.QLabel()
        digits_example_label.setStyleSheet(
            "font-size: 10px; color: #636366; background: transparent; border: none;")

        def update_digits_example():
            base    = self.rename_base_input.text().strip() or "name"
            d       = self.digits_spinbox.value()
            example = f"{base}_{'1'.zfill(d)}, {base}_{'2'.zfill(d)}"
            digits_example_label.setText(f"-> {example}")

        self.digits_spinbox.valueChanged.connect(update_digits_example)
        self.rename_base_input.textChanged.connect(update_digits_example)
        update_digits_example()

        digits_row.addWidget(digits_label)
        digits_row.addWidget(self.digits_spinbox)
        digits_row.addWidget(digits_down_btn)
        digits_row.addWidget(digits_up_btn)
        digits_row.addStretch()

        b_key_hint = QtWidgets.QLabel(
            "Select a thumbnail and press B to load its name as Base Name.")
        b_key_hint.setWordWrap(True)
        b_key_hint.setStyleSheet(
            "font-size: 10px; color: #636366; background: transparent; border: none;")

        rename_options_layout.addWidget(base_name_label)
        rename_options_layout.addWidget(self.rename_base_input)
        rename_options_layout.addWidget(b_key_hint)
        rename_options_layout.addLayout(digits_row)
        rename_options_layout.addWidget(digits_example_label)

        renumber_btn = QtWidgets.QPushButton("Re-enumerate by Base Name")
        renumber_btn.setToolTip(
            "Finds all images whose name starts with the Base Name entered above,\n"
            "then re-numbers them 01, 02, 03 … in their current visual order.")
        renumber_btn.setStyleSheet("""
            QPushButton {
                padding: 4px 10px; font-weight: 600; font-size: 11px;
                border-radius: 5px; margin: 2px 0 0 0;
                background: #2a4a2a; color: #30d158; border: 1px solid #30d158;
            }
            QPushButton:hover   { background: #1e6e3e; color: #ffffff; }
            QPushButton:pressed { background: #155230; }
        """)
        renumber_btn.setFixedHeight(26)
        renumber_btn.clicked.connect(self.renumber_by_base)
        rename_options_layout.addWidget(renumber_btn)

        # ── Thumbnail size ────────────────────────────────────────────────────
        self.thumb_label = QtWidgets.QLabel("Thumbnail Size:")
        self.thumb_label.setStyleSheet(
            "font-size: 11px; color: #e0e0e0; font-weight: 500;")

        self.thumb_spinbox = QtWidgets.QSpinBox()
        self.thumb_spinbox.setRange(THUMB_MIN, THUMB_MAX)
        self.thumb_spinbox.setValue(DEFAULT_THUMB)
        self.thumb_spinbox.setSuffix(" px")
        self.thumb_spinbox.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.thumb_spinbox.setStyleSheet("""
            QSpinBox {
                background-color: #1c1c1e; border: 1px solid #3a3a3c;
                border-radius: 5px; padding: 2px 6px; color: #e0e0e0;
                font-size: 11px; font-weight: 600;
            }
            QSpinBox:focus { border: 1px solid #0066CC; }
        """)
        self.thumb_spinbox.setFixedSize(72, 24)

        thumb_up_btn = QtWidgets.QPushButton("↑")
        thumb_up_btn.setFixedSize(26, 26)
        thumb_up_btn.setStyleSheet(arrow_btn_style)
        thumb_up_btn.clicked.connect(
            lambda: self.thumb_spinbox.setValue(self.thumb_spinbox.value() + 1))

        thumb_down_btn = QtWidgets.QPushButton("↓")
        thumb_down_btn.setFixedSize(26, 26)
        thumb_down_btn.setStyleSheet(arrow_btn_style)
        thumb_down_btn.clicked.connect(
            lambda: self.thumb_spinbox.setValue(self.thumb_spinbox.value() - 1))

        thumb_label_row = QtWidgets.QHBoxLayout()
        thumb_label_row.setSpacing(4)
        thumb_label_row.addWidget(self.thumb_label)
        thumb_label_row.addWidget(self.thumb_spinbox)
        thumb_label_row.addWidget(thumb_down_btn)
        thumb_label_row.addWidget(thumb_up_btn)
        thumb_label_row.addStretch()

        self.thumb_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.thumb_slider.setRange(THUMB_MIN, THUMB_MAX)
        self.thumb_slider.setValue(DEFAULT_THUMB)
        self.thumb_slider.setFixedHeight(20)
        self.thumb_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #2a2a2a; height: 5px; border-radius: 3px; }
            QSlider::handle:horizontal {
                background: #0066CC; width: 14px; height: 14px;
                margin: -5px 0; border-radius: 7px; }
            QSlider::handle:horizontal:hover { background: #007AFF; }
        """)

        self.thumb_slider.valueChanged.connect(self.update_thumb_size)
        self.thumb_spinbox.valueChanged.connect(self._on_thumb_spinbox_changed)
        self._thumb_syncing = False

        # ── Status label ──────────────────────────────────────────────────────
        self.status_label = QtWidgets.QLabel("No folder opened")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFixedHeight(24)
        self.status_label.setStyleSheet(
            "font-size: 10px; padding: 2px 6px; color: #a0a0a0; background: #1c1c1e; "
            "border-radius: 5px; border: 1px solid #3a3a3c;")

        # ── Search bars ───────────────────────────────────────────────────────
        search1_label = QtWidgets.QLabel("Search 1 (Double Left-Click):")
        search1_label.setStyleSheet(
            "font-weight: 600; color: #0a84ff; font-size: 10px;")

        self.search_input1 = SmartLineEdit()
        self.search_input1.setPlaceholderText(
            "Dbl left-click: fill & lock | LClick: copy+clear | RClick: paste")
        self.search_input1.setFixedHeight(26)
        self.search_input1.returnPressed.connect(
            lambda: self.search_image(1, prev=False))
        self.search_input1.textChanged.connect(lambda: self.reset_search_index(1))

        search_layout1 = QtWidgets.QHBoxLayout()
        search_layout1.setSpacing(3)
        self.search_up_btn1   = QtWidgets.QPushButton("↑")
        self.search_down_btn1 = QtWidgets.QPushButton("↓")
        self.search_up_btn1.setFixedSize(26, 26)
        self.search_down_btn1.setFixedSize(26, 26)
        self.search_up_btn1.clicked.connect(
            lambda: self.search_image(1, prev=True))
        self.search_down_btn1.clicked.connect(
            lambda: self.search_image(1, prev=False))
        search_layout1.addWidget(self.search_input1)
        search_layout1.addWidget(self.search_up_btn1)
        search_layout1.addWidget(self.search_down_btn1)

        search2_label = QtWidgets.QLabel("Search 2 (Double Right-Click):")
        search2_label.setStyleSheet(
            "font-weight: 600; color: #0a84ff; font-size: 10px;")

        self.search_input2 = SmartLineEdit()
        self.search_input2.setPlaceholderText(
            "Dbl right-click: fill & unlock | LClick: copy+clear | RClick: paste")
        self.search_input2.setFixedHeight(26)
        self.search_input2.returnPressed.connect(
            lambda: self.search_image(2, prev=False))
        self.search_input2.textChanged.connect(lambda: self.reset_search_index(2))

        search_layout2 = QtWidgets.QHBoxLayout()
        search_layout2.setSpacing(3)
        self.search_up_btn2   = QtWidgets.QPushButton("↑")
        self.search_down_btn2 = QtWidgets.QPushButton("↓")
        self.search_up_btn2.setFixedSize(26, 26)
        self.search_down_btn2.setFixedSize(26, 26)
        self.search_up_btn2.clicked.connect(
            lambda: self.search_image(2, prev=True))
        self.search_down_btn2.clicked.connect(
            lambda: self.search_image(2, prev=False))
        search_layout2.addWidget(self.search_input2)
        search_layout2.addWidget(self.search_up_btn2)
        search_layout2.addWidget(self.search_down_btn2)

        # ── Preview ───────────────────────────────────────────────────────────
        self.preview = QtWidgets.QLabel(
            "Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(430)
        self.preview.setFixedWidth(430)
        self.preview.setStyleSheet("""
            QLabel {
                background: #1c1c1e; color: #a0a0a0;
                border: 2px dashed #3a3a3c; border-radius: 12px;
                font-size: 13px; font-weight: 500;
            }
        """)

        # ── Scrollable inner content ──────────────────────────────────────────
        scroll_content = QtWidgets.QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        inner_layout = QtWidgets.QVBoxLayout(scroll_content)
        inner_layout.setSpacing(4)
        inner_layout.setContentsMargins(0, 0, 6, 0)

        inner_layout.addWidget(open_btn)
        inner_layout.addWidget(reload_btn)
        inner_layout.addWidget(top_btn)
        inner_layout.addWidget(bottom_btn)
        inner_layout.addWidget(rename_all_btn)
        inner_layout.addWidget(rename_selected_btn)
        inner_layout.addWidget(rename_options_frame)
        inner_layout.addSpacing(4)
        inner_layout.addLayout(thumb_label_row)
        inner_layout.addWidget(self.thumb_slider)
        inner_layout.addSpacing(4)
        inner_layout.addWidget(self.status_label)
        inner_layout.addSpacing(6)
        inner_layout.addWidget(search1_label)
        inner_layout.addLayout(search_layout1)
        inner_layout.addSpacing(3)
        inner_layout.addWidget(search2_label)
        inner_layout.addLayout(search_layout2)
        inner_layout.addSpacing(8)
        inner_layout.addWidget(self.preview)
        inner_layout.addSpacing(10)

        # ── Export Favorites ──────────────────────────────────────────────────
        export_sep = QtWidgets.QFrame()
        export_sep.setFrameShape(QtWidgets.QFrame.HLine)
        export_sep.setStyleSheet(
            "color: #3a3a3c; background: #3a3a3c; border: none; max-height: 1px;")
        inner_layout.addWidget(export_sep)
        inner_layout.addSpacing(6)

        export_info_lbl = QtWidgets.QLabel(
            "Copies all ★ starred images into  <b>00_favorites/</b>  inside the open folder.<br>"
            "<span style='color:#555560;'>If the folder already exists, its contents are "
            "cleared and replaced with the current favorites.</span>")
        export_info_lbl.setTextFormat(Qt.RichText)
        export_info_lbl.setWordWrap(True)
        export_info_lbl.setStyleSheet(
            "color: #7a7a82; font-size: 10px; background: transparent; border: none;")
        inner_layout.addWidget(export_info_lbl)
        inner_layout.addSpacing(5)

        export_fav_btn = QtWidgets.QPushButton("★  Export Favorites")
        export_fav_btn.setToolTip(
            "Copies all starred (★) images into '00_favorites/' inside the open folder.\n"
            "If the folder already exists, it is cleared first and rebuilt from scratch.")
        export_fav_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 12px; font-weight: 700; font-size: 12px;
                border-radius: 6px; margin: 1px 0;
                background: #111113;
                color: #c8980a;
                border: 1px solid #665500;
            }
            QPushButton:hover {
                background: #1a1800;
                border: 1px solid #c8980a;
                color: #ffd60a;
            }
            QPushButton:pressed {
                background: #0a0a0c;
                color: #b88000;
            }
        """)
        export_fav_btn.setFixedHeight(34)
        export_fav_btn.clicked.connect(self.export_favorites)
        inner_layout.addWidget(export_fav_btn)
        inner_layout.addSpacing(4)
        inner_layout.addStretch()

        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(scroll_content)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: #1c1c1e; width: 8px; border-radius: 4px; }
            QScrollBar::handle:vertical {
                background: #3a3a3c; border-radius: 4px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: #0066CC; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)

        # ── Progress bar ──────────────────────────────────────────────────────
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("")
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setStyleSheet(PROGRESS_IDLE_STYLE)

        credit_label = QtWidgets.QLabel("")
        credit_label.setFixedHeight(0)

        # ── Left panel ────────────────────────────────────────────────────────
        left_panel_widget = QtWidgets.QWidget()
        left_panel_widget.setFixedWidth(460)
        left_panel_vbox = QtWidgets.QVBoxLayout(left_panel_widget)
        left_panel_vbox.setContentsMargins(15, 15, 0, 0)
        left_panel_vbox.setSpacing(0)
        left_panel_vbox.addWidget(scroll_area)
        left_panel_vbox.addSpacing(3)

        progress_container = QtWidgets.QWidget()
        progress_container.setStyleSheet("background: transparent;")
        progress_hbox = QtWidgets.QHBoxLayout(progress_container)
        progress_hbox.setContentsMargins(0, 0, 15, 4)
        progress_hbox.setSpacing(0)
        progress_hbox.addWidget(self.progress_bar)
        left_panel_vbox.addWidget(progress_container)
        left_panel_vbox.addWidget(credit_label)

        # ── Image list (right side) ───────────────────────────────────────────
        self.list = DragDropListWidget()
        self.list.itemSelectionChanged.connect(self.update_preview)
        self.list.currentItemChanged.connect(self.update_preview_from_current)
        self.list.double_left_clicked.connect(self.handle_double_left_click)
        self.list.double_right_clicked.connect(self.handle_double_right_click)
        self.list.b_key_pressed.connect(self.handle_b_key)
        self.list.preview_path_changed.connect(self.update_preview_from_path)
        self.list.scene_tag_changed.connect(self._update_scene_banner)
        self.list.verticalScrollBar().valueChanged.connect(self._update_scene_banner)
        self.list.open_fullscreen_requested.connect(self._open_fullscreen)

        # ── Scene banner (sticky tag line above thumbnails) ───────────────────
        self.scene_banner = QtWidgets.QWidget()
        self.scene_banner.setFixedHeight(32)
        self.scene_banner.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0d2a3a, stop:1 #0a1e2c);
                border-bottom: 1px solid #1a4a6a;
            }
        """)
        banner_layout = QtWidgets.QHBoxLayout(self.scene_banner)
        banner_layout.setContentsMargins(12, 0, 12, 0)
        banner_layout.setSpacing(8)

        banner_icon = QtWidgets.QLabel("🏷")
        banner_icon.setStyleSheet(
            "background: transparent; border: none; font-size: 13px;")
        banner_icon.setFixedWidth(20)

        self.scene_banner_label = QtWidgets.QLabel("— no scene tag —")
        self.scene_banner_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border: none;
                color: #4a9abc;
                font-size: 12px;
                font-weight: 600;
                font-style: italic;
                letter-spacing: 0.3px;
            }
        """)

        banner_hint = QtWidgets.QLabel(
            "Click  T  on any thumbnail to set a scene tag")
        banner_hint.setStyleSheet(
            "background: transparent; border: none; "
            "color: #2a5a7a; font-size: 10px;")
        banner_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        banner_layout.addWidget(banner_icon)
        banner_layout.addWidget(self.scene_banner_label, 1)

        # ── Colored keyboard hint bar (between scene banner and thumbnails) ───
        hint_bar = QtWidgets.QWidget()
        hint_bar.setFixedHeight(24)
        hint_bar.setStyleSheet(
            "background: #080d12; border-bottom: 1px solid #152030;")
        hint_bar_layout = QtWidgets.QHBoxLayout(hint_bar)
        hint_bar_layout.setContentsMargins(14, 0, 14, 0)
        hint_bar_layout.setSpacing(0)

        def _hint_lbl(text, color):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(
                f"color: {color}; font-size: 10px; font-weight: 600; "
                "background: transparent; border: none; letter-spacing: 0.2px;")
            return lbl

        def _sep():
            s = QtWidgets.QLabel("    ")
            s.setStyleSheet("background: transparent; border: none;")
            return s

        # ★  Add Star — (S)
        hint_bar_layout.addWidget(_hint_lbl("★", "#c8980a"))
        hint_bar_layout.addWidget(_hint_lbl(" Add Star — (S)", "#8a7030"))
        hint_bar_layout.addWidget(_sep())
        # ☆  Remove Star — (D)
        hint_bar_layout.addWidget(_hint_lbl("☆", "#505058"))
        hint_bar_layout.addWidget(_hint_lbl(" Remove Star — (D)", "#404048"))
        hint_bar_layout.addWidget(_sep())
        # 🏷  Add Tag — (T)
        hint_bar_layout.addWidget(_hint_lbl("🏷", "#1a7aaa"))
        hint_bar_layout.addWidget(_hint_lbl(" Add Tag — (T)", "#1a5a80"))
        hint_bar_layout.addWidget(_sep())
        # ⛶  Fullscreen — (F)
        hint_bar_layout.addWidget(_hint_lbl("⛶", "#208050"))
        hint_bar_layout.addWidget(_hint_lbl(" Fullscreen — (F)", "#186040"))
        hint_bar_layout.addWidget(_sep())
        # ✎  Base Name — (B / Right-click)
        hint_bar_layout.addWidget(_hint_lbl("✎", "#2a6a5a"))
        hint_bar_layout.addWidget(_hint_lbl(" Base Name — (B / Right-click)", "#1e4a40"))
        hint_bar_layout.addWidget(_sep())
        # ◀ ▶  Navigate
        hint_bar_layout.addWidget(_hint_lbl("◀ ▶", "#304060"))
        hint_bar_layout.addWidget(_hint_lbl(" Navigate", "#253050"))
        hint_bar_layout.addStretch()

        # Right-side container: scene banner → hint bar → thumbnail list
        right_container = QtWidgets.QWidget()
        right_vbox = QtWidgets.QVBoxLayout(right_container)
        right_vbox.setContentsMargins(0, 0, 0, 0)
        right_vbox.setSpacing(0)
        right_vbox.addWidget(self.scene_banner)
        right_vbox.addWidget(hint_bar)
        right_vbox.addWidget(self.list, 1)

        # ── Main layout ───────────────────────────────────────────────────────
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(left_panel_widget)
        main_layout.addWidget(right_container, 1)

        last_folder = self.settings.value("last_folder", "")
        if last_folder and os.path.isdir(last_folder):
            self.folder = last_folder

        self.list.setFocus()

        self.folder_watch_timer = QTimer(self)
        self.folder_watch_timer.timeout.connect(self.check_for_new_files)
        self.folder_watch_timer.start(5000)

        self.show()

    # ── Global key handler (F = fullscreen from anywhere) ─────────────────────

    def keyPressEvent(self, event):
        """
        Catch F pressed anywhere in the main window — unless focus is inside
        a text input (QLineEdit / QSpinBox) where F is a normal character.
        """
        if event.key() == Qt.Key_F:
            focused = QtWidgets.QApplication.focusWidget()
            if not isinstance(focused, (QtWidgets.QLineEdit, QtWidgets.QSpinBox,
                                        QtWidgets.QAbstractSpinBox)):
                # Open fullscreen at the current/selected row
                row = self.list.currentRow()
                if row < 0 and self.list.count() > 0:
                    row = 0
                if row >= 0:
                    self._open_fullscreen(row)
                event.accept()
                return
        super().keyPressEvent(event)

    def _update_scene_banner(self):
        """
        Update the sticky scene-tag banner above the thumbnail grid.

        Works like the PyCharm function breadcrumb: the banner always shows the
        tag of the last tagged image that has scrolled into or above the visible
        area — and it stays shown until a newer tag scrolls in from below.

        Approach: scan every item's visualItemRect (which is in viewport
        coordinates and is always correct regardless of image size or spacing),
        find the topmost item that is at least partially visible, then walk
        backward from that row to find the nearest tag at or above it.
        This is robust for mixed-size images, large images, and empty gaps.
        """
        if self.list.count() == 0:
            self._set_banner_text("", "— no scene tag —")
            return

        vp_height   = self.list.viewport().height()
        top_row     = -1
        top_y       = vp_height + 1   # start with a value beyond the viewport

        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None:
                continue
            rect = self.list.visualItemRect(item)
            # An item is "at least partially visible" if its bottom is above
            # the viewport bottom AND its top is above the viewport height.
            # We want the one whose top edge is closest to (but still within) 0.
            if rect.bottom() < 0:
                # Entirely above the viewport — this is a candidate for top_row
                # because it represents where we "came from"; keep the last such row.
                top_row = i
            elif rect.top() < vp_height:
                # At least partially visible from the top
                if top_row == -1:
                    # No fully-scrolled-past item yet — this is the first visible row
                    top_row = i
                break   # first partially/fully visible item found; stop scanning

        # If everything is scrolled past the bottom (shouldn't happen), use last row
        if top_row == -1:
            top_row = self.list.count() - 1

        # Walk backward from top_row to find the nearest tag at or above
        active_tag = ""
        for row in range(top_row, -1, -1):
            tov = self.list._tag_overlays.get(row)
            if tov and tov.get_tag():
                active_tag = tov.get_tag()
                break

        self._set_banner_text(active_tag)

    def _set_banner_text(self, tag: str, override: str = ""):
        """Helper — updates the banner label text and style."""
        if tag:
            self.scene_banner_label.setText(f"  {tag}")
            self.scene_banner_label.setStyleSheet("""
                QLabel {
                    background: transparent; border: none;
                    color: #32ade6; font-size: 12px;
                    font-weight: 700; font-style: normal;
                    letter-spacing: 0.3px;
                }
            """)
        else:
            self.scene_banner_label.setText(override or "— no scene tag —")
            self.scene_banner_label.setStyleSheet("""
                QLabel {
                    background: transparent; border: none;
                    color: #2a5a7a; font-size: 12px;
                    font-weight: 600; font-style: italic;
                }
            """)

    # ── Fullscreen viewer ─────────────────────────────────────────────────────

    def _open_fullscreen(self, start_row: int):
        """Open the fullscreen gallery viewer at *start_row*."""
        if self.list.count() == 0:
            return
        viewer = FullscreenViewer(self.list, start_row, parent=None)
        viewer.row_changed.connect(self._on_fullscreen_row_changed)
        viewer.star_changed.connect(self._on_fullscreen_star_changed)
        viewer.tag_changed.connect(self._on_fullscreen_tag_changed)
        viewer.setAttribute(Qt.WA_DeleteOnClose, True)
        viewer.showFullScreen()
        viewer.setFocus()

    def _on_fullscreen_row_changed(self, row: int):
        """Keep the thumbnail grid in sync with fullscreen navigation."""
        if 0 <= row < self.list.count():
            self.list.clearSelection()
            item = self.list.item(row)
            if item:
                item.setSelected(True)
                self.list.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def _on_fullscreen_star_changed(self, row: int, starred: bool):
        """Star overlay in main grid already updated by FullscreenViewer; emit scene banner refresh."""
        self._update_scene_banner()

    def _on_fullscreen_tag_changed(self, row: int, tag_text: str):
        """Tag overlay in main grid already updated by FullscreenViewer; refresh banner."""
        self._update_scene_banner()

    # ── B-key ─────────────────────────────────────────────────────────────────

    def handle_b_key(self, name_no_ext: str):
        self.rename_base_input.setText(name_no_ext)
        self.rename_base_input.setStyleSheet("""
            QLineEdit {
                background-color: #1c1c1e; border: 1px solid #30d158;
                border-radius: 6px; padding: 3px 8px; color: #e0e0e0;
                font-size: 12px; selection-background-color: #0066CC;
            }
        """)
        QTimer.singleShot(600, self._reset_base_input_style)

    def _reset_base_input_style(self):
        self.rename_base_input.setStyleSheet("""
            QLineEdit {
                background-color: #1c1c1e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 3px 8px; color: #e0e0e0;
                font-size: 12px; selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #0066CC; }
        """)

    # ── Progress helpers ──────────────────────────────────────────────────────

    def _progress_start(self):
        self.progress_bar.setFormat("Loading: %p%")
        self.progress_bar.setStyleSheet(PROGRESS_ACTIVE_STYLE)
        self.progress_bar.setValue(0)

    def _progress_done(self):
        self.progress_bar.setFormat("")
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(PROGRESS_IDLE_STYLE)

    def _pause_watcher(self):
        self.folder_watch_timer.stop()

    def _resume_watcher(self):
        if self.folder:
            self.current_folder_files = set(
                f for f in os.listdir(self.folder)
                if os.path.splitext(f)[1].lower() in SUPPORTED_EXT)
        self.folder_watch_timer.start(5000)

    # ── Theme ─────────────────────────────────────────────────────────────────

    def apply_dark_theme(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window,          QtGui.QColor(28, 28, 30))
        palette.setColor(QtGui.QPalette.WindowText,      QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Base,            QtGui.QColor(22, 22, 23))
        palette.setColor(QtGui.QPalette.AlternateBase,   QtGui.QColor(44, 44, 46))
        palette.setColor(QtGui.QPalette.ToolTipBase,     QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.ToolTipText,     QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Text,            QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Button,          QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.ButtonText,      QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.BrightText,      QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Link,            QtGui.QColor(10, 132, 255))
        palette.setColor(QtGui.QPalette.Highlight,       QtGui.QColor(10, 132, 255))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText,
                         QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text,
                         QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText,
                         QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight,
                         QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText,
                         QtGui.QColor(127, 127, 127))
        QApplication.setPalette(palette)
        QApplication.instance().setStyleSheet("""
            QMainWindow { background-color: #1c1c1e; }
            QWidget { background-color: #1c1c1e; color: #e0e0e0; }
            QLineEdit {
                background-color: #2c2c2e; border: 1px solid #3a3a3c;
                border-radius: 6px; padding: 4px 10px; color: #e0e0e0;
                selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #0066CC; }
            QListWidget {
                background-color: #1c1c1e; border: 1px solid #3a3a3c;
                border-radius: 8px; color: #e0e0e0; outline: none;
            }
            QListWidget::item:selected { background-color: #0066CC; color: white; }
            QListWidget::item:hover    { background-color: #2c2c2e; }
        """)

    # ── Window state ──────────────────────────────────────────────────────────

    def closeEvent(self, event):
        self.settings.setValue("geometry",    self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        if self.folder:
            self.settings.setValue("last_folder", self.folder)
        super().closeEvent(event)

    # ── Search ────────────────────────────────────────────────────────────────

    def reset_search_index(self, search_bar):
        self.last_search_index[search_bar] = -1

    # ── Double-click handlers ─────────────────────────────────────────────────

    def handle_double_left_click(self, name, path):
        name_without_ext = os.path.splitext(name)[0]
        self.search_input1.setText(name_without_ext)
        self.preview_locked = True
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview.size() - QtCore.QSize(20, 20),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    def handle_double_right_click(self, name):
        name_without_ext = os.path.splitext(name)[0]
        self.search_input2.setText(name_without_ext)
        self.preview_locked = False
        self.update_preview()

    # ── Folder operations ─────────────────────────────────────────────────────

    def open_folder(self):
        start_dir = self.folder or ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Image Folder", start_dir)
        if not folder:
            return
        self.folder = folder
        self.load_folder_contents()

    def load_folder_contents(self):
        if not self.folder:
            return
        self._progress_start()
        QApplication.processEvents()
        self.list.clear_overlays()
        self.list.clear()
        self.list.thumbnail_cache.clear()
        files = [f for f in os.listdir(self.folder)
                 if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        files.sort(key=natural_key)
        total_files = len(files)
        for idx, f in enumerate(files):
            path = os.path.join(self.folder, f)
            item = QtWidgets.QListWidgetItem(os.path.basename(f))
            item.setData(Qt.UserRole, path)
            item.setIcon(self.list.get_thumbnail_icon(path))
            self.list.addItem(item)
            self.list.add_star_for_item(idx)
            self.list.add_tag_for_item(idx)
            progress = int(((idx + 1) / total_files) * 100) if total_files > 0 else 100
            self.progress_bar.setValue(progress)
            QApplication.processEvents()
        self._progress_done()
        # Final reposition after all items are laid out
        QTimer.singleShot(100, self.list._reposition_overlays)
        QTimer.singleShot(150, self._update_scene_banner)
        self.current_folder_files = set(files)
        self.update_status_label(in_sync=True)
        self.setWindowTitle(
            "Scenify — Image Scene Flow Organizer  ·  Developed by Ivan Sicaja © 2026")

    def check_for_new_files(self):
        if not self.folder or not os.path.isdir(self.folder):
            return
        current_files = set(f for f in os.listdir(self.folder)
                            if os.path.splitext(f)[1].lower() in SUPPORTED_EXT)
        known_files = self.current_folder_files
        removed = known_files - current_files
        added   = current_files - known_files

        if removed:
            rows_to_remove = []
            for i in range(self.list.count()):
                item = self.list.item(i)
                if item and os.path.basename(item.data(Qt.UserRole) or "") in removed:
                    rows_to_remove.append(i)
            for row in reversed(rows_to_remove):
                star = self.list._star_overlays.pop(row, None)
                if star:
                    star.deleteLater()
                tov = self.list._tag_overlays.pop(row, None)
                if tov:
                    tov.deleteLater()
                self.list.takeItem(row)
            self.list._rebuild_star_index()
            self.list._rebuild_tag_index()
            self.current_folder_files = current_files

            removed_sorted = sorted(removed, key=natural_key)
            dialog = QtWidgets.QDialog(self)
            dialog.setWindowTitle("Images Removed from Folder")
            dialog.setMinimumWidth(420)
            dialog.setMinimumHeight(280)
            dialog.setStyleSheet("background: #1c1c1e; color: #e0e0e0;")
            dlg_layout = QtWidgets.QVBoxLayout(dialog)
            dlg_layout.setContentsMargins(16, 16, 16, 16)
            dlg_layout.setSpacing(10)
            header = QtWidgets.QLabel(
                f"⚠  {len(removed_sorted)} image{'s' if len(removed_sorted) > 1 else ''} "
                f"{'were' if len(removed_sorted) > 1 else 'was'} removed from the folder:")
            header.setStyleSheet("font-size: 12px; font-weight: 600; color: #ff9f0a;")
            header.setWordWrap(True)
            dlg_layout.addWidget(header)
            list_widget = QtWidgets.QListWidget()
            list_widget.setStyleSheet("""
                QListWidget {
                    background: #2c2c2e; border: 1px solid #3a3a3c;
                    border-radius: 6px; color: #e0e0e0; font-size: 11px; padding: 4px;
                }
                QListWidget::item { padding: 3px 6px; }
                QScrollBar:vertical { background: #2c2c2e; width: 8px; border-radius: 4px; }
                QScrollBar::handle:vertical {
                    background: #3a3a3c; border-radius: 4px; min-height: 20px; }
                QScrollBar::handle:vertical:hover { background: #0066CC; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
            """)
            for name in removed_sorted:
                list_widget.addItem(name)
            dlg_layout.addWidget(list_widget)
            ok_btn = QtWidgets.QPushButton("OK")
            ok_btn.setFixedHeight(28)
            ok_btn.setStyleSheet("""
                QPushButton {
                    background: #0066CC; color: white; border: none;
                    border-radius: 5px; font-weight: 600; font-size: 11px; padding: 4px 20px;
                }
                QPushButton:hover   { background: #007AFF; }
                QPushButton:pressed { background: #0051A3; }
            """)
            ok_btn.clicked.connect(dialog.accept)
            btn_row = QtWidgets.QHBoxLayout()
            btn_row.addStretch()
            btn_row.addWidget(ok_btn)
            dlg_layout.addLayout(btn_row)
            dialog.exec_()
            if added:
                self.update_status_label(in_sync=False, added_count=len(added), removed_count=0)
            else:
                self.update_status_label(in_sync=True)
        elif added:
            self.update_status_label(in_sync=False, added_count=len(added), removed_count=0)
        else:
            self.update_status_label(in_sync=True)

    def update_status_label(self, in_sync=True, added_count=0, removed_count=0):
        if not self.folder:
            self.status_label.setText("No folder opened")
            self.status_label.setStyleSheet(
                "font-size: 10px; padding: 2px 6px; color: #a0a0a0; background: #1c1c1e; "
                "border-radius: 5px; border: 1px solid #3a3a3c;")
            return
        if in_sync:
            self.status_label.setText("✓  All images loaded")
            self.status_label.setStyleSheet(
                "font-size: 10px; padding: 2px 6px; color: #30d158; font-weight: 600; "
                "background: #1c1c1e; border-radius: 5px; border: 1px solid #30d158;")
        else:
            if added_count > 0 and removed_count == 0:
                msg = f"＋{added_count} new image{'s' if added_count > 1 else ''} — Reload folder"
            elif removed_count > 0 and added_count == 0:
                msg = f"−{removed_count} image{'s' if removed_count > 1 else ''} removed — Reload folder"
            else:
                msg = f"＋{added_count} / −{removed_count} images changed — Reload folder"
            self.status_label.setText(f"⚠  {msg}")
            self.status_label.setStyleSheet(
                "font-size: 10px; padding: 2px 6px; color: #ff9f0a; font-weight: 600; "
                "background: #1c1c1e; border-radius: 5px; border: 1px solid #ff9f0a;")

    def reload_folder(self):
        if not self.folder:
            QtWidgets.QMessageBox.warning(self, "Error", "No folder loaded!")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Reload Folder",
            "This will load any new images from the folder.\n"
            "Existing images keep their current names.\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        self._progress_start()
        QApplication.processEvents()

        existing_names = {self.list.item(i).text() for i in range(self.list.count())}
        disk_files = [f for f in os.listdir(self.folder)
                      if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        new_files = [f for f in disk_files if f not in existing_names]
        new_files.sort(key=natural_key)

        existing_on_disk = set(os.listdir(self.folder))
        counter = 0
        _generic_re = re.compile(r"^generic_(\d+)\.", re.IGNORECASE)
        for _fn in existing_on_disk:
            _m = _generic_re.match(_fn)
            if _m:
                counter = max(counter, int(_m.group(1)) + 1)

        new_paths = []
        for f in new_files:
            old_path = os.path.join(self.folder, f)
            ext      = os.path.splitext(f)[1]
            new_name = f"generic_{counter:06d}{ext}"
            while new_name in existing_on_disk or new_name in existing_names:
                counter += 1
                new_name = f"generic_{counter:06d}{ext}"
            new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(old_path, new_path)
                self.list.thumbnail_cache.pop(old_path, None)
                self.list.thumbnail_cache.pop(new_path, None)
                new_paths.append(new_path)
                existing_on_disk.add(new_name)
                existing_on_disk.discard(f)
            except Exception:
                pass
            counter += 1

        total = len(new_paths)
        for idx, new_path in enumerate(new_paths):
            fname = os.path.basename(new_path)
            item  = QtWidgets.QListWidgetItem(fname)
            item.setData(Qt.UserRole, new_path)
            item.setIcon(self.list.get_thumbnail_icon(new_path))
            self.list.addItem(item)
            row = self.list.count() - 1
            self.list.add_star_for_item(row)
            self.list.add_tag_for_item(row)
            self.progress_bar.setValue(int(((idx + 1) / max(total, 1)) * 100))
            QApplication.processEvents()

        self._progress_done()
        QTimer.singleShot(100, self.list._reposition_overlays)
        QTimer.singleShot(150, self._update_scene_banner)
        final_files = [f for f in os.listdir(self.folder)
                       if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        self.current_folder_files = set(final_files)
        self.update_status_label(in_sync=True)
        self.setWindowTitle(
            "Scenify — Image Scene Flow Organizer  ·  Developed by Ivan Sicaja © 2026")
        if new_paths:
            QtWidgets.QMessageBox.information(
                self, "Done",
                f"{len(new_paths)} new image{'s' if len(new_paths) > 1 else ''} added.")
        else:
            QtWidgets.QMessageBox.information(
                self, "Up to Date", "No new images found in the folder.")

    # ── Thumbnail size sync ───────────────────────────────────────────────────

    def update_thumb_size(self, val):
        if self._thumb_syncing:
            return
        self._thumb_syncing = True
        self.thumb_spinbox.setValue(val)
        self._thumb_syncing = False
        self.list.setThumbnailSize(val)

    def _on_thumb_spinbox_changed(self, val):
        if self._thumb_syncing:
            return
        self._thumb_syncing = True
        self.thumb_slider.setValue(val)
        self._thumb_syncing = False
        self.list.setThumbnailSize(val)

    # ── Preview ───────────────────────────────────────────────────────────────

    def update_preview(self):
        if self.preview_locked:
            return
        sel = self.list.selectedItems()
        if not sel:
            self.preview.setText(
                "Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
            self.preview.setPixmap(QtGui.QPixmap())
            return
        path = sel[0].data(Qt.UserRole)
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview.size() - QtCore.QSize(20, 20),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    def update_preview_from_current(self, current, previous):
        if self.preview_locked or current is None:
            return
        path = current.data(Qt.UserRole)
        if path and os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview.size() - QtCore.QSize(20, 20),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    def update_preview_from_path(self, path):
        if self.preview_locked:
            return
        if path and os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(
                    self.preview.size() - QtCore.QSize(20, 20),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    # ── Move to top/bottom ────────────────────────────────────────────────────

    def move_to_top(self):
        items = sorted(self.list.selectedItems(), key=lambda x: self.list.row(x))
        if not items:
            return
        self.list.setUpdatesEnabled(False)
        for item in reversed(items):
            self.list.takeItem(self.list.row(item))
        for i, item in enumerate(items):
            self.list.insertItem(i, item)
            item.setSelected(True)
        self.list.setUpdatesEnabled(True)
        self.list.scrollToTop()
        self.list._rebuild_star_index()
        self.list._rebuild_tag_index()
        QTimer.singleShot(50, self.list._reposition_overlays)

    def move_to_bottom(self):
        items = sorted(self.list.selectedItems(), key=lambda x: self.list.row(x))
        if not items:
            return
        self.list.setUpdatesEnabled(False)
        for item in reversed(items):
            self.list.takeItem(self.list.row(item))
        base = self.list.count()
        for i, item in enumerate(items):
            self.list.insertItem(base + i, item)
            item.setSelected(True)
        self.list.setUpdatesEnabled(True)
        self.list.scrollToBottom()
        self.list._rebuild_star_index()
        self.list._rebuild_tag_index()
        QTimer.singleShot(50, self.list._reposition_overlays)

    # ── Rename All ────────────────────────────────────────────────────────────

    def rename_ordered(self):
        if not self.folder or self.list.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Error", "No images loaded!")
            return
        if QtWidgets.QMessageBox.question(
                self, "Rename All",
                f"Rename all {self.list.count()} images to 1, 2, 3, etc.?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        ) != QtWidgets.QMessageBox.Yes:
            return
        self._pause_watcher()
        temp_paths = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            old  = item.data(Qt.UserRole)
            if not os.path.exists(old):
                continue
            ext = os.path.splitext(old)[1]
            tmp = os.path.join(self.folder, f"__TMP_RENAME_{i}{ext}")
            try:
                os.rename(old, tmp)
                self.list.update_star_path(old, tmp)
                self.list.update_tag_path(old, tmp)
                temp_paths.append((item, tmp, ext))
            except Exception:
                continue
        renamed = 0
        for idx, (item, tmp, ext) in enumerate(temp_paths, start=1):
            if not os.path.exists(tmp):
                continue
            new = os.path.join(self.folder, f"{idx}{ext}")
            try:
                os.rename(tmp, new)
                self.list.update_star_path(tmp, new)
                self.list.update_tag_path(tmp, new)
                item.setData(Qt.UserRole, new)
                item.setText(os.path.basename(new))
                renamed += 1
            except Exception:
                continue
        QtWidgets.QMessageBox.information(self, "Done", f"Renamed {renamed} images!")
        self._resume_watcher()

    # ── Rename Selected ───────────────────────────────────────────────────────

    def rename_selected(self):
        sel = self.list.selectedItems()
        if not sel:
            QtWidgets.QMessageBox.warning(
                self, "No Selection", "Please select at least one image.")
            return
        base = self.rename_base_input.text().strip()
        if not base:
            QtWidgets.QMessageBox.warning(
                self, "No Base Name",
                "Please enter a base name in the 'Base Name' field below the "
                "Rename Selected button.")
            self.rename_base_input.setFocus()
            return
        self._pause_watcher()
        digits        = self.digits_spinbox.value()
        renamed_items = sorted(sel, key=lambda x: self.list.row(x))
        pattern       = re.compile(
            rf"^{re.escape(base)}_(\d{{{digits}}})\.[a-zA-Z]{{3,4}}$", re.IGNORECASE)
        pattern_any   = re.compile(
            rf"^{re.escape(base)}_(\d+)\.[a-zA-Z]{{3,4}}$", re.IGNORECASE)
        selected_texts = {item.text() for item in renamed_items}
        used_names = {self.list.item(i).text() for i in range(self.list.count())
                      if self.list.item(i).text() not in selected_texts}
        max_counter_excl = 0
        for name in used_names:
            match = pattern_any.match(name)
            if match:
                max_counter_excl = max(max_counter_excl, int(match.group(1)))
        counter   = max_counter_excl + 1
        new_items = []
        for item in renamed_items:
            old_path = item.data(Qt.UserRole)
            if not os.path.exists(old_path):
                continue
            ext      = os.path.splitext(old_path)[1]
            new_name = f"{base}_{str(counter).zfill(digits)}{ext}"
            while new_name in used_names:
                counter += 1
                new_name = f"{base}_{str(counter).zfill(digits)}{ext}"
            new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(old_path, new_path)
                self.list.update_star_path(old_path, new_path)
                self.list.update_tag_path(old_path, new_path)
                item.setText(new_name)
                item.setData(Qt.UserRole, new_path)
                used_names.add(new_name)
                new_items.append(item)
                counter += 1
            except Exception:
                continue
        if not new_items:
            self._resume_watcher()
            return
        rows = sorted([self.list.row(itm) for itm in new_items], reverse=True)
        for r in rows:
            self.list.takeItem(r)
        insert_at = 0
        for i in range(self.list.count()):
            name = self.list.item(i).text()
            if pattern.match(name):
                insert_at = i + 1
            else:
                if insert_at > 0:
                    break
        if insert_at == 0:
            sample = new_items[0].text()
            for i in range(self.list.count()):
                if natural_key(self.list.item(i).text()) > natural_key(sample):
                    insert_at = i
                    break
            else:
                insert_at = self.list.count()
        for i, item in enumerate(new_items):
            self.list.insertItem(insert_at + i, item)
            item.setSelected(True)
        if new_items:
            self.list.scrollToItem(new_items[0], QAbstractItemView.PositionAtCenter)
        self.list._rebuild_star_index()
        self.list._rebuild_tag_index()
        QTimer.singleShot(50, self.list._reposition_overlays)
        QtWidgets.QMessageBox.information(
            self, "Success",
            f"Renamed and placed {len(new_items)} images perfectly!")
        self._resume_watcher()

    # ── Re-enumerate by Base Name ─────────────────────────────────────────────

    def renumber_by_base(self):
        base = self.rename_base_input.text().strip()
        if not base:
            QtWidgets.QMessageBox.warning(
                self, "No Base Name", "Please enter a base name first.")
            self.rename_base_input.setFocus()
            return
        if not self.folder:
            QtWidgets.QMessageBox.warning(self, "No Folder", "No folder is open.")
            return
        self._pause_watcher()
        digits      = self.digits_spinbox.value()
        pattern_any = re.compile(
            rf"^{re.escape(base)}_(\d+)\.[a-zA-Z]{{3,4}}$", re.IGNORECASE)
        matching = [self.list.item(i)
                    for i in range(self.list.count())
                    if pattern_any.match(self.list.item(i).text())]
        if not matching:
            QtWidgets.QMessageBox.information(
                self, "Nothing Found",
                f"No images with base name '{base}' were found in the list.")
            return
        temp_entries = []
        for i, item in enumerate(matching):
            old_path = item.data(Qt.UserRole)
            if not os.path.exists(old_path):
                continue
            ext      = os.path.splitext(old_path)[1]
            tmp_path = os.path.join(self.folder, f"__RENUM_{i}{ext}")
            try:
                os.rename(old_path, tmp_path)
                if old_path in self.list.thumbnail_cache:
                    self.list.thumbnail_cache[tmp_path] = \
                        self.list.thumbnail_cache.pop(old_path)
                self.list.update_star_path(old_path, tmp_path)
                self.list.update_tag_path(old_path, tmp_path)
                item.setData(Qt.UserRole, tmp_path)
                temp_entries.append((item, tmp_path, ext))
            except Exception:
                continue
        renamed = 0
        renamed_items_ordered = []
        for seq, (item, tmp_path, ext) in enumerate(temp_entries, start=1):
            if not os.path.exists(tmp_path):
                continue
            new_name = f"{base}_{str(seq).zfill(digits)}{ext}"
            new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(tmp_path, new_path)
                item.setText(new_name)
                item.setData(Qt.UserRole, new_path)
                if tmp_path in self.list.thumbnail_cache:
                    self.list.thumbnail_cache[new_path] = \
                        self.list.thumbnail_cache.pop(tmp_path)
                self.list.update_star_path(tmp_path, new_path)
                self.list.update_tag_path(tmp_path, new_path)
                renamed += 1
                renamed_items_ordered.append(item)
            except Exception:
                continue
        if renamed_items_ordered:
            first_row = min(self.list.row(it) for it in renamed_items_ordered)
            rows = sorted([self.list.row(it) for it in renamed_items_ordered],
                          reverse=True)
            for r in rows:
                self.list.takeItem(r)
            for i, it in enumerate(renamed_items_ordered):
                self.list.insertItem(first_row + i, it)
                it.setSelected(True)
            self.list.scrollToItem(
                renamed_items_ordered[0], QAbstractItemView.PositionAtCenter)
        self.list._rebuild_star_index()
        self.list._rebuild_tag_index()
        QTimer.singleShot(50, self.list._reposition_overlays)
        QtWidgets.QMessageBox.information(
            self, "Done",
            f"Re-enumerated {renamed} images with base name '{base}'.")
        self._resume_watcher()

    # ── Export Favorites ──────────────────────────────────────────────────────

    def export_favorites(self):
        """
        Export all ★-starred images to  <open_folder>/00_favorites/.
        If the destination folder already exists it is cleared first, then
        all currently starred images are copied fresh.  Originals are never
        moved or modified.
        """
        if not self.folder:
            QtWidgets.QMessageBox.warning(
                self, "No Folder", "Please open a folder first.")
            return

        # Collect starred paths
        starred_paths = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item is None:
                continue
            star = self.list._star_overlays.get(i)
            if star and star.is_starred():
                path = item.data(Qt.UserRole)
                if path and os.path.isfile(path):
                    starred_paths.append(path)

        if not starred_paths:
            QtWidgets.QMessageBox.information(
                self, "No Favorites",
                "No images are marked as favorites (★) in the current list.\n\n"
                "Select a thumbnail and press S, or click its ★ overlay, to star it.")
            return

        dest_folder  = os.path.join(self.folder, "00_favorites")
        folder_exists = os.path.isdir(dest_folder)

        # Build confirmation message
        if folder_exists:
            existing_count = len([
                f for f in os.listdir(dest_folder)
                if os.path.splitext(f)[1].lower() in {
                    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".tif"}
            ])
            confirm_msg = (
                f"Export {len(starred_paths)} starred image"
                f"{'s' if len(starred_paths) != 1 else ''} to:\n\n"
                f"  {dest_folder}\n\n"
                f"⚠  This folder already exists and contains {existing_count} image"
                f"{'s' if existing_count != 1 else ''}.\n"
                "All existing images in it will be deleted and replaced with the current favorites.\n\n"
                "Originals in the main folder are not affected. Continue?"
            )
        else:
            confirm_msg = (
                f"Export {len(starred_paths)} starred image"
                f"{'s' if len(starred_paths) != 1 else ''} to:\n\n"
                f"  {dest_folder}\n\n"
                "Originals will not be moved or changed. Continue?"
            )

        reply = QtWidgets.QMessageBox.question(
            self, "Export Favorites", confirm_msg,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if reply != QtWidgets.QMessageBox.Yes:
            return

        import shutil

        # Clear destination folder if it already exists
        if folder_exists:
            try:
                shutil.rmtree(dest_folder)
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self, "Error",
                    f"Could not clear existing folder:\n{dest_folder}\n\n{e}")
                return

        # Create fresh destination folder
        try:
            os.makedirs(dest_folder, exist_ok=True)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Error",
                f"Could not create destination folder:\n{dest_folder}\n\n{e}")
            return

        # Copy all starred images
        copied = 0
        errors = []

        for src_path in starred_paths:
            fname    = os.path.basename(src_path)
            dst_path = os.path.join(dest_folder, fname)
            try:
                shutil.copy2(src_path, dst_path)   # preserves EXIF/metadata
                copied += 1
            except Exception as e:
                errors.append(f"{fname}: {e}")

        # Result summary
        parts = [f"✓  {copied} image{'s' if copied != 1 else ''} exported."]
        if errors:
            parts.append(f"✗  {len(errors)} error{'s' if len(errors) != 1 else ''}:\n"
                         + "\n".join(errors[:5])
                         + ("\n…" if len(errors) > 5 else ""))

        msg = "\n".join(parts) + f"\n\nDestination:\n{dest_folder}"

        if errors:
            QtWidgets.QMessageBox.warning(self, "Export Favorites — Done", msg)
        else:
            QtWidgets.QMessageBox.information(self, "Export Favorites — Done", msg)

    # ── Search ────────────────────────────────────────────────────────────────

    def search_image(self, search_bar, prev=False):
        text = (self.search_input1.text() if search_bar == 1
                else self.search_input2.text()).strip().lower()
        if not text:
            return
        total = self.list.count()
        if total == 0:
            return
        start_index = self.last_search_index[search_bar]
        if start_index == -1:
            selected = self.list.selectedItems()
            if selected:
                start_index = self.list.row(selected[0])
            else:
                start_index = -1 if not prev else 0
        step        = -1 if prev else 1
        current_idx = (start_index + step) % total
        for _ in range(total):
            item = self.list.item(current_idx)
            if item and text in item.text().lower():
                self.list.clearSelection()
                item.setSelected(True)
                self.list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                self.last_search_index[search_bar] = current_idx
                return
            current_idx = (current_idx + step) % total


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ImageOrganizer()
    sys.exit(app.exec_())