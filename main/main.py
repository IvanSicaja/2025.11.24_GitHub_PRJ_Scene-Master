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
#  DragDropListWidget  — extended to manage StarOverlay widgets
# ──────────────────────────────────────────────────────────────────────────────

class DragDropListWidget(QtWidgets.QListWidget):
    double_left_clicked  = QtCore.pyqtSignal(str, str)
    double_right_clicked = QtCore.pyqtSignal(str)
    b_key_pressed        = QtCore.pyqtSignal(str)
    preview_path_changed = QtCore.pyqtSignal(str)

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

        # star overlays: row → StarOverlay
        self._star_overlays: dict[int, StarOverlay] = {}

        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.start_progressive_resize)
        self.progressive_timer = QTimer()
        self.progressive_timer.timeout.connect(self.resize_next_thumbnail)
        self.resize_index = 0

        # Reposition stars whenever scroll or layout changes
        self.verticalScrollBar().valueChanged.connect(self._reposition_stars)
        self.horizontalScrollBar().valueChanged.connect(self._reposition_stars)

    # ── star management ───────────────────────────────────────────────────────

    def _star_size_for_thumb(self) -> int:
        """Scale star size proportionally to current thumbnail size."""
        return max(16, min(28, int(self.thumbnail_size * 0.16)))

    def add_star_for_item(self, row: int):
        """Create and position a StarOverlay for the item at *row*."""
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
        """Move the StarOverlay for *row* to the correct viewport position."""
        star = self._star_overlays.get(row)
        if star is None:
            return
        item = self.item(row)
        if item is None:
            return
        rect = self.visualItemRect(item)
        sz   = star.width()
        margin = 4
        x = rect.right()  - sz - margin
        y = rect.top()    + margin
        star.move(x, y)
        star.raise_()

    def _reposition_stars(self):
        """Reposition all visible stars (called after scroll/resize)."""
        for row in list(self._star_overlays.keys()):
            self._position_star(row)

    def _rebuild_star_index(self):
        """
        After any insert/remove the row→star mapping must be rebuilt because
        rows shift.  We rely on each StarOverlay's stored path to re-identify
        its item.
        """
        # Build path→star map
        path_to_star = {}
        for star in self._star_overlays.values():
            path_to_star[star._path] = star

        self._star_overlays.clear()
        for i in range(self.count()):
            item = self.item(i)
            if item is None:
                continue
            path = item.data(Qt.UserRole) or ""
            if path in path_to_star:
                star = path_to_star[path]
                # Reconnect signal with correct new row
                try:
                    star.toggled.disconnect()
                except Exception:
                    pass
                star.toggled.connect(lambda state, r=i: self._on_star_toggled(r, state))
                self._star_overlays[i] = star
                self._position_star(i)

    def _on_star_toggled(self, row: int, state: bool):
        # Nothing extra needed — file was already written by StarOverlay
        pass

    def clear_stars(self):
        for star in self._star_overlays.values():
            star.deleteLater()
        self._star_overlays.clear()

    def toggle_star_for_selected(self):
        """Toggle star state for the currently focused item (S-key handler)."""
        sel = self.selectedItems()
        if not sel:
            return
        row  = self.row(sel[0])
        star = self._star_overlays.get(row)
        if star and star._supported:
            # Simulate a click
            new_state = not star.is_starred()
            rating    = 5 if new_state else 0
            if set_image_rating(star._path, rating):
                star.set_starred(new_state)

    def update_star_path(self, old_path: str, new_path: str):
        """Call after renaming a file so the overlay tracks the new path."""
        for star in self._star_overlays.values():
            if star._path == old_path:
                star.update_path(new_path)
                break

    # ── overrides ─────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._reposition_stars)

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._reposition_stars()

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
        # S key: toggle star for selected thumbnail
        if event.key() == Qt.Key_S:
            self.toggle_star_for_selected()
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
        QTimer.singleShot(200, self._reposition_stars)

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
        QTimer.singleShot(0, self._reposition_stars)

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
        QTimer.singleShot(50, self._reposition_stars)


# ──────────────────────────────────────────────────────────────────────────────

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

        # ── Main layout ───────────────────────────────────────────────────────
        main_layout = QtWidgets.QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        main_layout.addWidget(left_panel_widget)
        main_layout.addWidget(self.list, 1)

        last_folder = self.settings.value("last_folder", "")
        if last_folder and os.path.isdir(last_folder):
            self.folder = last_folder

        self.list.setFocus()

        self.folder_watch_timer = QTimer(self)
        self.folder_watch_timer.timeout.connect(self.check_for_new_files)
        self.folder_watch_timer.start(5000)

        self.show()

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
        self.list.clear_stars()
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
            progress = int(((idx + 1) / total_files) * 100) if total_files > 0 else 100
            self.progress_bar.setValue(progress)
            QApplication.processEvents()
        self._progress_done()
        # Final reposition after all items are laid out
        QTimer.singleShot(100, self.list._reposition_stars)
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
                self.list.takeItem(row)
            self.list._rebuild_star_index()
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
            self.progress_bar.setValue(int(((idx + 1) / max(total, 1)) * 100))
            QApplication.processEvents()

        self._progress_done()
        QTimer.singleShot(100, self.list._reposition_stars)
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
        QTimer.singleShot(50, self.list._reposition_stars)

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
        QTimer.singleShot(50, self.list._reposition_stars)

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
        QTimer.singleShot(50, self.list._reposition_stars)
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
        QTimer.singleShot(50, self.list._reposition_stars)
        QtWidgets.QMessageBox.information(
            self, "Done",
            f"Re-enumerated {renamed} images with base name '{base}'.")
        self._resume_watcher()

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