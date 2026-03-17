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
"""
import os
import sys
import re
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtCore import Qt, QSettings, QTimer
from PyQt5.QtWidgets import QAbstractItemView, QApplication

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".tif"}
THUMB_MIN, THUMB_MAX, DEFAULT_THUMB = 60, 400, 170
PADDING = 30


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


class DragDropListWidget(QtWidgets.QListWidget):
    double_left_clicked = QtCore.pyqtSignal(str, str)
    double_right_clicked = QtCore.pyqtSignal(str)

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
        self.setGridSize(QtCore.QSize(self.thumbnail_size + PADDING, self.thumbnail_size + PADDING + 50))
        self.thumbnail_cache = {}
        self.itemDoubleClicked.connect(self.handle_double_click)
        self.setFocusPolicy(Qt.StrongFocus)

        self.resize_timer = QTimer()
        self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self.start_progressive_resize)
        self.progressive_timer = QTimer()
        self.progressive_timer.timeout.connect(self.resize_next_thumbnail)
        self.resize_index = 0

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left or event.key() == Qt.Key_Right:
            current_row = self.currentRow()
            total = self.count()
            if total == 0:
                return
            if event.key() == Qt.Key_Left:
                new_row = (current_row - 1) % total
            else:
                new_row = (current_row + 1) % total
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

    def get_thumbnail_icon(self, path, force_regenerate=False):
        if not force_regenerate and path in self.thumbnail_cache:
            cached_icon = self.thumbnail_cache[path]
            if not cached_icon.isNull():
                pixmap = cached_icon.pixmap(QtCore.QSize(self.thumbnail_size, self.thumbnail_size))
                if pixmap.width() == self.thumbnail_size or pixmap.height() == self.thumbnail_size:
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
        selected = [i.row() for i in self.selectedIndexes()]
        drag_rows = sorted(set(selected))
        if not drag_rows: return
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
            drag_rows = eval(e.mimeData().data('application/x-drag-rows').data().decode())
        except:
            e.ignore()
            return
        if not drag_rows: e.ignore(); return
        pos = e.pos()
        target_item = self.itemAt(pos)
        target_row = self.row(target_item) if target_item else self.count()
        if target_row in drag_rows: e.ignore(); return
        insert_at = target_row if target_row <= max(drag_rows) else target_row - len(drag_rows)
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


# ── Progress bar style constants ──────────────────────────────────────────────
# IDLE: bar occupies space but is visually invisible (transparent everything)
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
# ACTIVE: green loading bar
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
        self.setWindowTitle("Scenify - Image Scene Flow Organizer")
        self.apply_dark_theme()
        self.settings = QSettings("ImageSceneFlowOrganizer", "Settings")
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        window_state = self.settings.value("windowState")
        if window_state:
            self.restoreState(window_state)
        if not geometry:
            self.showMaximized()
        self.folder = None
        self.preview_locked = False
        self.last_search_index = {1: -1, 2: -1}
        self.current_folder_files = set()

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        # ── Button styles ─────────────────────────────────────────────────────
        blue_btn_style = """
            QPushButton {
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
                border-radius: 6px;
                margin: 2px 0;
                background: #0066CC;
                color: white;
                border: none;
            }
            QPushButton:hover { background: #007AFF; }
            QPushButton:pressed { background: #0051A3; }
        """
        gray_btn_style = """
            QPushButton {
                padding: 6px 12px;
                font-weight: 600;
                font-size: 12px;
                border-radius: 6px;
                margin: 2px 0;
                background: #3A3A3C;
                color: white;
                border: none;
            }
            QPushButton:hover { background: #48484A; }
            QPushButton:pressed { background: #2A2A2C; }
        """

        # ── Buttons ───────────────────────────────────────────────────────────
        open_btn = QtWidgets.QPushButton("Open Folder")
        open_btn.setStyleSheet(blue_btn_style)
        open_btn.clicked.connect(self.open_folder)

        reload_btn = QtWidgets.QPushButton("Reload Folder")
        reload_btn.setStyleSheet(blue_btn_style)
        reload_btn.clicked.connect(self.reload_folder)

        top_btn = QtWidgets.QPushButton("Move Selected to Top")
        top_btn.setStyleSheet(blue_btn_style)
        top_btn.clicked.connect(self.move_to_top)

        bottom_btn = QtWidgets.QPushButton("Move Selected to Bottom")
        bottom_btn.setStyleSheet(blue_btn_style)
        bottom_btn.clicked.connect(self.move_to_bottom)

        clear_btn = QtWidgets.QPushButton("Clear Selection")
        clear_btn.setStyleSheet(gray_btn_style)
        clear_btn.clicked.connect(lambda: self.list.clearSelection())

        rename_all_btn = QtWidgets.QPushButton("Rename All")
        rename_all_btn.setStyleSheet(blue_btn_style)
        rename_all_btn.clicked.connect(self.rename_ordered)

        rename_selected_btn = QtWidgets.QPushButton("Rename Selected")
        rename_selected_btn.setStyleSheet(blue_btn_style)
        rename_selected_btn.clicked.connect(self.rename_selected)

        # ── Rename Selected inline options ────────────────────────────────────
        rename_options_frame = QtWidgets.QFrame()
        rename_options_frame.setStyleSheet("""
            QFrame {
                background: #2c2c2e;
                border: 1px solid #3a3a3c;
                border-radius: 8px;
            }
        """)
        rename_options_layout = QtWidgets.QVBoxLayout(rename_options_frame)
        rename_options_layout.setContentsMargins(10, 8, 10, 8)
        rename_options_layout.setSpacing(5)

        rename_options_title = QtWidgets.QLabel("Rename Selected — Options")
        rename_options_title.setStyleSheet(
            "font-size: 10px; font-weight: 700; color: #636366; "
            "background: transparent; border: none; letter-spacing: 0.5px;"
        )
        rename_options_layout.addWidget(rename_options_title)

        base_name_label = QtWidgets.QLabel("Base Name:")
        base_name_label.setStyleSheet(
            "font-size: 11px; color: #a0a0a0; font-weight: 500; "
            "background: transparent; border: none;"
        )
        self.rename_base_input = QtWidgets.QLineEdit()
        self.rename_base_input.setPlaceholderText("e.g. garage, scene, shot ...")
        self.rename_base_input.setStyleSheet("""
            QLineEdit {
                background-color: #1c1c1e;
                border: 1px solid #3a3a3c;
                border-radius: 6px;
                padding: 5px 8px;
                color: #e0e0e0;
                font-size: 12px;
                selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #0066CC; }
        """)

        digits_row = QtWidgets.QHBoxLayout()
        digits_label = QtWidgets.QLabel("Digit Count:")
        digits_label.setStyleSheet(
            "font-size: 11px; color: #a0a0a0; font-weight: 500; "
            "background: transparent; border: none;"
        )
        self.digits_spinbox = QtWidgets.QSpinBox()
        self.digits_spinbox.setRange(1, 10)
        self.digits_spinbox.setValue(2)
        self.digits_spinbox.setToolTip(
            "Number of digits used for the numeric suffix.\n"
            "e.g. 3 → garage_001, garage_002\n"
            "     6 → garage_000001, garage_000002"
        )
        self.digits_spinbox.setStyleSheet("""
            QSpinBox {
                background-color: #1c1c1e;
                border: 1px solid #3a3a3c;
                border-radius: 6px;
                padding: 4px 6px;
                color: #e0e0e0;
                font-size: 12px;
                font-weight: 600;
            }
            QSpinBox:focus { border: 1px solid #0066CC; }
            QSpinBox::up-button, QSpinBox::down-button {
                background: #3a3a3c;
                border: none;
                border-radius: 3px;
                width: 18px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background: #0066CC;
            }
        """)
        digits_example_label = QtWidgets.QLabel()
        digits_example_label.setStyleSheet(
            "font-size: 10px; color: #636366; background: transparent; border: none;"
        )

        def update_digits_example():
            base = self.rename_base_input.text().strip() or "name"
            d = self.digits_spinbox.value()
            example = f"{base}_{'1'.zfill(d)}, {base}_{'2'.zfill(d)}"
            digits_example_label.setText(f"→ {example}")

        self.digits_spinbox.valueChanged.connect(update_digits_example)
        self.rename_base_input.textChanged.connect(update_digits_example)
        update_digits_example()

        digits_row.addWidget(digits_label)
        digits_row.addWidget(self.digits_spinbox)
        digits_row.addStretch()

        rename_options_layout.addWidget(base_name_label)
        rename_options_layout.addWidget(self.rename_base_input)
        rename_options_layout.addLayout(digits_row)
        rename_options_layout.addWidget(digits_example_label)

        # ── Thumbnail slider ──────────────────────────────────────────────────
        self.thumb_label = QtWidgets.QLabel(f"Thumbnail Size: {DEFAULT_THUMB}px")
        self.thumb_label.setStyleSheet("font-size: 12px; color: #e0e0e0; font-weight: 500;")

        self.thumb_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.thumb_slider.setRange(THUMB_MIN, THUMB_MAX)
        self.thumb_slider.setValue(DEFAULT_THUMB)
        self.thumb_slider.valueChanged.connect(self.update_thumb_size)
        self.thumb_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #2a2a2a;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #0066CC;
                width: 16px;
                height: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QSlider::handle:horizontal:hover { background: #007AFF; }
        """)

        # ── Status label ──────────────────────────────────────────────────────
        self.status_label = QtWidgets.QLabel("No folder opened")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet(
            "font-size: 11px; padding: 6px; color: #a0a0a0; background: #1c1c1e; "
            "border-radius: 6px; border: 1px solid #3a3a3c;")
        self.status_label.setFixedHeight(32)

        # ── Search bars ───────────────────────────────────────────────────────
        search1_label = QtWidgets.QLabel("Search 1 (Double Left-Click):")
        search1_label.setStyleSheet("font-weight: 600; color: #0a84ff; font-size: 11px;")

        self.search_input1 = SmartLineEdit()
        self.search_input1.setPlaceholderText("Double left-click → fill & lock | LClick: copy+clear | RClick: paste")
        self.search_input1.returnPressed.connect(lambda: self.search_image(1, prev=False))
        self.search_input1.textChanged.connect(lambda: self.reset_search_index(1))

        search_layout1 = QtWidgets.QHBoxLayout()
        self.search_up_btn1 = QtWidgets.QPushButton("↑")
        self.search_down_btn1 = QtWidgets.QPushButton("↓")
        self.search_up_btn1.clicked.connect(lambda: self.search_image(1, prev=True))
        self.search_down_btn1.clicked.connect(lambda: self.search_image(1, prev=False))
        search_layout1.addWidget(self.search_input1)
        search_layout1.addWidget(self.search_up_btn1)
        search_layout1.addWidget(self.search_down_btn1)

        search2_label = QtWidgets.QLabel("Search 2 (Double Right-Click):")
        search2_label.setStyleSheet("font-weight: 600; color: #0a84ff; font-size: 11px;")

        self.search_input2 = SmartLineEdit()
        self.search_input2.setPlaceholderText("Double right-click → fill & unlock | LClick: copy+clear | RClick: paste")
        self.search_input2.returnPressed.connect(lambda: self.search_image(2, prev=False))
        self.search_input2.textChanged.connect(lambda: self.reset_search_index(2))

        search_layout2 = QtWidgets.QHBoxLayout()
        self.search_up_btn2 = QtWidgets.QPushButton("↑")
        self.search_down_btn2 = QtWidgets.QPushButton("↓")
        self.search_up_btn2.clicked.connect(lambda: self.search_image(2, prev=True))
        self.search_down_btn2.clicked.connect(lambda: self.search_image(2, prev=False))
        search_layout2.addWidget(self.search_input2)
        search_layout2.addWidget(self.search_up_btn2)
        search_layout2.addWidget(self.search_down_btn2)

        # ── Preview ───────────────────────────────────────────────────────────
        self.preview = QtWidgets.QLabel("Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedHeight(350)
        self.preview.setStyleSheet("""
            QLabel {
                background: #1c1c1e;
                color: #a0a0a0;
                border: 2px dashed #3a3a3c;
                border-radius: 12px;
                font-size: 15px;
                font-weight: 500;
            }
        """)

        # ── Scrollable inner content ──────────────────────────────────────────
        # NOTE: progress bar is NOT inside the scroll area — it lives below it.
        scroll_content = QtWidgets.QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        inner_layout = QtWidgets.QVBoxLayout(scroll_content)
        inner_layout.setSpacing(6)
        inner_layout.setContentsMargins(0, 0, 6, 0)

        inner_layout.addWidget(open_btn)
        inner_layout.addWidget(reload_btn)
        inner_layout.addWidget(top_btn)
        inner_layout.addWidget(bottom_btn)
        inner_layout.addWidget(clear_btn)
        inner_layout.addWidget(rename_all_btn)
        inner_layout.addWidget(rename_selected_btn)
        inner_layout.addWidget(rename_options_frame)
        inner_layout.addSpacing(6)
        inner_layout.addWidget(self.thumb_label)
        inner_layout.addWidget(self.thumb_slider)
        inner_layout.addSpacing(6)
        inner_layout.addWidget(self.status_label)
        inner_layout.addSpacing(8)
        inner_layout.addWidget(search1_label)
        inner_layout.addLayout(search_layout1)
        inner_layout.addSpacing(4)
        inner_layout.addWidget(search2_label)
        inner_layout.addLayout(search_layout2)
        inner_layout.addSpacing(10)
        inner_layout.addWidget(self.preview)
        inner_layout.addStretch()

        # ── Scroll area ───────────────────────────────────────────────────────
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(scroll_content)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_area.setStyleSheet("""
            QScrollArea { background: transparent; border: none; }
            QScrollBar:vertical {
                background: #1c1c1e;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #3a3a3c;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover { background: #0066CC; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
        """)

        # ── Progress bar — ALWAYS in the layout, never hidden ─────────────────
        # Starts transparent/idle; turns green during loading; back to idle when done.
        # Space is always reserved — nothing ever shifts.
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("")   # no text while idle
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setStyleSheet(PROGRESS_IDLE_STYLE)

        # ── Copyright — pinned at the very bottom ─────────────────────────────
        credit_label = QtWidgets.QLabel("Developed by Ivan Sicaja © 2026. All rights reserved.")
        credit_label.setAlignment(Qt.AlignCenter)
        credit_label.setStyleSheet(
            "font-size: 9px; color: #636366; padding: 8px 0; "
            "border-top: 1px solid #2a2a2a; background: #1c1c1e;")

        # ── Left panel: [scroll area]  →  [progress bar]  →  [copyright] ─────
        left_panel_widget = QtWidgets.QWidget()
        left_panel_widget.setFixedWidth(460)
        left_panel_vbox = QtWidgets.QVBoxLayout(left_panel_widget)
        left_panel_vbox.setContentsMargins(15, 15, 0, 0)
        left_panel_vbox.setSpacing(0)
        left_panel_vbox.addWidget(scroll_area)          # flexible, fills space
        left_panel_vbox.addSpacing(4)
        left_panel_vbox.addWidget(self.progress_bar)    # fixed slot, always present
        left_panel_vbox.addWidget(credit_label)         # always at the very bottom

        # ── Image list (right side) ───────────────────────────────────────────
        self.list = DragDropListWidget()
        self.list.itemSelectionChanged.connect(self.update_preview)
        self.list.double_left_clicked.connect(self.handle_double_left_click)
        self.list.double_right_clicked.connect(self.handle_double_right_click)

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

    # ── Progress bar helpers ──────────────────────────────────────────────────
    def _progress_start(self):
        """Switch bar to active (green) state."""
        self.progress_bar.setFormat("Loading: %p%")
        self.progress_bar.setStyleSheet(PROGRESS_ACTIVE_STYLE)
        self.progress_bar.setValue(0)

    def _progress_done(self):
        """Switch bar back to idle (invisible) state."""
        self.progress_bar.setFormat("")
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet(PROGRESS_IDLE_STYLE)

    def apply_dark_theme(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor(28, 28, 30))
        palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor(22, 22, 23))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(44, 44, 46))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Text, QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(224, 224, 224))
        palette.setColor(QtGui.QPalette.BrightText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Link, QtGui.QColor(10, 132, 255))
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor(10, 132, 255))
        palette.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.WindowText, QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Text, QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.ButtonText, QtGui.QColor(127, 127, 127))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.Highlight, QtGui.QColor(58, 58, 60))
        palette.setColor(QtGui.QPalette.Disabled, QtGui.QPalette.HighlightedText, QtGui.QColor(127, 127, 127))
        QApplication.setPalette(palette)

        app_stylesheet = """
            QMainWindow { background-color: #1c1c1e; }
            QWidget { background-color: #1c1c1e; color: #e0e0e0; }
            QLineEdit {
                background-color: #2c2c2e;
                border: 1px solid #3a3a3c;
                border-radius: 6px;
                padding: 6px 10px;
                color: #e0e0e0;
                selection-background-color: #0066CC;
            }
            QLineEdit:focus { border: 1px solid #0066CC; }
            QListWidget {
                background-color: #1c1c1e;
                border: 1px solid #3a3a3c;
                border-radius: 8px;
                color: #e0e0e0;
                outline: none;
            }
            QListWidget::item:selected { background-color: #0066CC; color: white; }
            QListWidget::item:hover { background-color: #2c2c2e; }
        """
        QApplication.instance().setStyleSheet(app_stylesheet)

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        if self.folder:
            self.settings.setValue("last_folder", self.folder)
        super().closeEvent(event)

    def reset_search_index(self, search_bar):
        self.last_search_index[search_bar] = -1

    def handle_double_left_click(self, name, path):
        name_without_ext = os.path.splitext(name)[0]
        self.search_input1.setText(name_without_ext)
        self.preview_locked = True
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(self.preview.size() - QtCore.QSize(20, 20),
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    def handle_double_right_click(self, name):
        name_without_ext = os.path.splitext(name)[0]
        self.search_input2.setText(name_without_ext)
        self.preview_locked = False
        self.update_preview()

    def open_folder(self):
        start_dir = self.folder or ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Image Folder", start_dir)
        if not folder:
            return
        self.folder = folder
        self.load_folder_contents()

    def load_folder_contents(self):
        if not self.folder:
            return
        self._progress_start()
        QApplication.processEvents()
        self.list.clear()
        self.list.thumbnail_cache.clear()
        files = [f for f in os.listdir(self.folder) if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        files.sort(key=natural_key)
        total_files = len(files)
        for idx, f in enumerate(files):
            path = os.path.join(self.folder, f)
            item = QtWidgets.QListWidgetItem(os.path.basename(f))
            item.setData(Qt.UserRole, path)
            item.setIcon(self.list.get_thumbnail_icon(path))
            self.list.addItem(item)
            progress = int(((idx + 1) / total_files) * 100) if total_files > 0 else 100
            self.progress_bar.setValue(progress)
            QApplication.processEvents()
        self._progress_done()
        self.current_folder_files = set(files)
        self.update_status_label(in_sync=True)
        self.setWindowTitle(f"Scenify - Image Scene Flow Organizer — {len(files)} images")

    def check_for_new_files(self):
        if not self.folder or not os.path.isdir(self.folder):
            return
        current_files = [f for f in os.listdir(self.folder) if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        current_set = set(current_files)
        if current_set == self.current_folder_files:
            self.update_status_label(in_sync=True)
        else:
            added = current_set - self.current_folder_files
            removed = self.current_folder_files - current_set
            self.update_status_label(in_sync=False, added_count=len(added), removed_count=len(removed))

    def update_status_label(self, in_sync=True, added_count=0, removed_count=0):
        if not self.folder:
            self.status_label.setText("No folder opened")
            self.status_label.setStyleSheet(
                "font-size: 11px; padding: 6px; color: #a0a0a0; background: #1c1c1e; "
                "border-radius: 6px; border: 1px solid #3a3a3c;")
            return
        if in_sync:
            self.status_label.setText("✓ All images in folder are loaded")
            self.status_label.setStyleSheet(
                "font-size: 11px; padding: 6px; color: #30d158; font-weight: 600; "
                "background: #1c1c1e; border-radius: 6px; border: 1px solid #30d158;")
        else:
            parts = []
            if added_count > 0:
                parts.append(f"{added_count} added")
            if removed_count > 0:
                parts.append(f"{removed_count} removed")
            msg = " | ".join(parts) if parts else "Changes detected"
            self.status_label.setText(f"⚠ {msg} – Reload recommended")
            self.status_label.setStyleSheet(
                "font-size: 11px; padding: 6px; color: #ff9f0a; font-weight: 600; "
                "background: #1c1c1e; border-radius: 6px; border: 1px solid #ff9f0a;")

    def reload_folder(self):
        if not self.folder or self.list.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Error", "No folder loaded!")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Reload Folder",
            "This will:\n1. Rename all current images to 1,2,3...\n2. Load any new images from the folder\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return
        self._progress_start()
        QApplication.processEvents()
        temp_paths = []
        total_operations = self.list.count()
        for i in range(self.list.count()):
            item = self.list.item(i)
            old_path = item.data(Qt.UserRole)
            if not os.path.exists(old_path):
                continue
            ext = os.path.splitext(old_path)[1]
            tmp_path = os.path.join(self.folder, f"__TMP_RENAME_{i}{ext}")
            try:
                os.rename(old_path, tmp_path)
                if old_path in self.list.thumbnail_cache:
                    self.list.thumbnail_cache[tmp_path] = self.list.thumbnail_cache.pop(old_path)
                item.setData(Qt.UserRole, tmp_path)
                temp_paths.append((item, tmp_path, ext))
                progress = int(((i + 1) / (total_operations * 2)) * 100)
                self.progress_bar.setValue(progress)
                QApplication.processEvents()
            except Exception:
                continue
        new_files = [f for f in os.listdir(self.folder) if
                     os.path.splitext(f)[1].lower() in SUPPORTED_EXT and not f.startswith("__TMP_RENAME_")]
        new_files.sort(key=natural_key)
        new_paths = []
        counter = 0
        for f in new_files:
            old_new_path = os.path.join(self.folder, f)
            ext = os.path.splitext(f)[1]
            new_name = f"generic_{counter:06d}{ext}"
            new_path = os.path.join(self.folder, new_name)
            while os.path.exists(new_path):
                counter += 1
                new_name = f"generic_{counter:06d}{ext}"
                new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(old_new_path, new_path)
                new_paths.append(new_path)
            except Exception:
                pass
            counter += 1
        num_counter = 1
        for idx, (item, tmp_path, ext) in enumerate(temp_paths):
            if not os.path.exists(tmp_path):
                continue
            new_name = f"{num_counter}{ext}"
            new_path = os.path.join(self.folder, new_name)
            while os.path.exists(new_path):
                num_counter += 1
                new_name = f"{num_counter}{ext}"
                new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(tmp_path, new_path)
                item.setData(Qt.UserRole, new_path)
                item.setText(new_name)
                if tmp_path in self.list.thumbnail_cache:
                    self.list.thumbnail_cache[new_path] = self.list.thumbnail_cache.pop(tmp_path)
                progress = 50 + int(((idx + 1) / total_operations) * 50)
                self.progress_bar.setValue(progress)
                QApplication.processEvents()
            except Exception:
                pass
            num_counter += 1
        for new_path in new_paths:
            f = os.path.basename(new_path)
            item = QtWidgets.QListWidgetItem(f)
            item.setData(Qt.UserRole, new_path)
            item.setIcon(self.list.get_thumbnail_icon(new_path))
            self.list.addItem(item)
        items = []
        for _ in range(self.list.count()):
            items.append(self.list.takeItem(0))
        items.sort(key=lambda it: natural_key(it.text()))
        for it in items:
            self.list.addItem(it)
        self.progress_bar.setValue(100)
        QApplication.processEvents()
        self._progress_done()
        final_files = [f for f in os.listdir(self.folder) if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        self.current_folder_files = set(final_files)
        self.update_status_label(in_sync=True)
        self.setWindowTitle(f"Scenify - Image Scene Flow Organizer — {self.list.count()} images")
        QtWidgets.QMessageBox.information(self, "Success",
                                          f"Folder reloaded! Existing files renamed, {len(new_paths)} new files added.")

    def update_thumb_size(self, val):
        self.thumb_label.setText(f"Thumbnail Size: {val}px")
        self.list.setThumbnailSize(val)

    def update_preview(self):
        if self.preview_locked:
            return
        sel = self.list.selectedItems()
        if not sel:
            self.preview.setText("Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
            self.preview.setPixmap(QtGui.QPixmap())
            return
        path = sel[0].data(Qt.UserRole)
        if os.path.exists(path):
            pix = QtGui.QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(self.preview.size() - QtCore.QSize(20, 20),
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview.setPixmap(scaled)

    def move_to_top(self):
        items = sorted(self.list.selectedItems(), key=lambda x: self.list.row(x))
        if not items: return
        self.list.setUpdatesEnabled(False)
        for item in reversed(items):
            self.list.takeItem(self.list.row(item))
        for i, item in enumerate(items):
            self.list.insertItem(i, item)
            item.setSelected(True)
        self.list.setUpdatesEnabled(True)
        self.list.scrollToTop()

    def move_to_bottom(self):
        items = sorted(self.list.selectedItems(), key=lambda x: self.list.row(x))
        if not items: return
        self.list.setUpdatesEnabled(False)
        for item in reversed(items):
            self.list.takeItem(self.list.row(item))
        base = self.list.count()
        for i, item in enumerate(items):
            self.list.insertItem(base + i, item)
            item.setSelected(True)
        self.list.setUpdatesEnabled(True)
        self.list.scrollToBottom()

    def rename_ordered(self):
        if not self.folder or self.list.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Error", "No images loaded!")
            return
        if QtWidgets.QMessageBox.question(self, "Rename All",
                                          f"Rename all {self.list.count()} images to 1, 2, 3, etc.?",
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        temp_paths = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            old = item.data(Qt.UserRole)
            if not os.path.exists(old):
                continue
            ext = os.path.splitext(old)[1]
            tmp = os.path.join(self.folder, f"__TMP_RENAME_{i}{ext}")
            try:
                os.rename(old, tmp)
                temp_paths.append((item, tmp, ext))
            except:
                continue
        renamed = 0
        for idx, (item, tmp, ext) in enumerate(temp_paths, start=1):
            if not os.path.exists(tmp):
                continue
            new = os.path.join(self.folder, f"{idx}{ext}")
            try:
                os.rename(tmp, new)
                item.setData(Qt.UserRole, new)
                item.setText(os.path.basename(new))
                renamed += 1
            except:
                continue
        QtWidgets.QMessageBox.information(self, "Done", f"Renamed {renamed} images!")

    def rename_selected(self):
        sel = self.list.selectedItems()
        if not sel:
            QtWidgets.QMessageBox.warning(self, "No Selection", "Please select at least one image.")
            return
        base = self.rename_base_input.text().strip()
        if not base:
            QtWidgets.QMessageBox.warning(
                self, "No Base Name",
                "Please enter a base name in the 'Base Name' field below the Rename Selected button."
            )
            self.rename_base_input.setFocus()
            return
        digits = self.digits_spinbox.value()
        renamed_items = sorted(sel, key=lambda x: self.list.row(x))
        max_counter = 0
        pattern = re.compile(
            rf"^{re.escape(base)}_(\d{{{digits}}})\.[a-zA-Z]{{3,4}}$", re.IGNORECASE
        )
        pattern_any = re.compile(
            rf"^{re.escape(base)}_(\d+)\.[a-zA-Z]{{3,4}}$", re.IGNORECASE
        )
        for i in range(self.list.count()):
            name = self.list.item(i).text()
            match = pattern_any.match(name)
            if match:
                max_counter = max(max_counter, int(match.group(1)))
        counter = max_counter + 1
        used_names = {self.list.item(i).text() for i in range(self.list.count())}
        new_items = []
        for item in renamed_items:
            old_path = item.data(Qt.UserRole)
            if not os.path.exists(old_path):
                continue
            ext = os.path.splitext(old_path)[1]
            new_name = f"{base}_{str(counter).zfill(digits)}{ext}"
            while new_name in used_names:
                counter += 1
                new_name = f"{base}_{str(counter).zfill(digits)}{ext}"
            new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(old_path, new_path)
                item.setText(new_name)
                item.setData(Qt.UserRole, new_path)
                used_names.add(new_name)
                new_items.append(item)
                counter += 1
            except Exception:
                continue
        if not new_items:
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
        QtWidgets.QMessageBox.information(self, "Success", f"Renamed and placed {len(new_items)} images perfectly!")

    def search_image(self, search_bar, prev=False):
        text = self.search_input1.text() if search_bar == 1 else self.search_input2.text()
        text = text.strip().lower()
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
        step = -1 if prev else 1
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