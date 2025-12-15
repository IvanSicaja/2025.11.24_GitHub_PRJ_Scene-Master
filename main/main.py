#!/usr/bin/env python3
"""
Image Scene Flow Organizer - ENHANCED VERSION
→ All original features preserved
→ Two search bars with up/down navigation
→ Double-click (left) on image → name goes to first search bar + LOCKS PREVIEW
→ Double-click (right) on image → name goes to second search bar + UNLOCKS PREVIEW
→ RELOAD button: renames existing files (1,2,3...) then loads new files
→ Remembers last opened folder
→ Extensions are stripped when copying names to search bars
→ Smart search field click: left-click copies & clears, right-click clears & pastes
"""

import os
import sys
import re
from PyQt5 import QtWidgets, QtGui, QtCore
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QAbstractItemView

SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".webp", ".tif"}
THUMB_MIN, THUMB_MAX, DEFAULT_THUMB = 60, 400, 180
PADDING = 30
SETTINGS_FILE = "image_organizer_settings.txt"


def natural_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def save_last_folder(folder_path):
    """Save the last opened folder to a settings file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            f.write(folder_path)
    except:
        pass


def load_last_folder():
    """Load the last opened folder from settings file"""
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                folder = f.read().strip()
                if os.path.isdir(folder):
                    return folder
    except:
        pass
    return None


class SmartLineEdit(QtWidgets.QLineEdit):
    """Custom QLineEdit with clipboard functionality on mouse clicks"""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Left click: copy text to clipboard, then clear
            text = self.text()
            if text:
                clipboard = QtWidgets.QApplication.clipboard()
                clipboard.setText(text)
                self.clear()
        elif event.button() == Qt.RightButton:
            # Right click: clear and paste from clipboard
            self.clear()
            clipboard = QtWidgets.QApplication.clipboard()
            clipboard_text = clipboard.text()
            if clipboard_text:
                self.setText(clipboard_text)

        # Call the original mousePressEvent to maintain cursor positioning
        super().mousePressEvent(event)


class DragDropListWidget(QtWidgets.QListWidget):
    double_left_clicked = QtCore.pyqtSignal(str, str)  # Signal for double left-click (name, path)
    double_right_clicked = QtCore.pyqtSignal(str)  # Signal for double right-click (name)

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

        # Track double-click
        self.itemDoubleClicked.connect(self.handle_double_click)

    def handle_double_click(self, item):
        """Handle double left-click"""
        name = item.text()
        path = item.data(Qt.UserRole)
        self.double_left_clicked.emit(name, path)

    def mouseDoubleClickEvent(self, event):
        """Override to catch right double-click"""
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
        for i in range(self.count()):
            item = self.item(i)
            path = item.data(Qt.UserRole)
            if path:
                item.setIcon(self.get_thumbnail_icon(path))

    def get_thumbnail_icon(self, path):
        if path in self.thumbnail_cache:
            return self.thumbnail_cache[path]
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
            e.ignore();
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


class ImageOrganizer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Image Scene Flow Organizer")

        # Start maximized (fullscreen)
        self.showMaximized()

        self.folder = None
        self.preview_locked = False  # Track if preview is locked

        # Track last search indices for each search bar
        self.last_search_index = {1: -1, 2: -1}

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        left_panel = QtWidgets.QVBoxLayout()
        left_panel.setSpacing(10)
        left_panel.setContentsMargins(15, 15, 15, 15)

        btn_style = "QPushButton { padding: 10px 16px; font-weight: bold; font-size: 13px; border-radius: 6px; margin: 3px 0; }"

        open_btn = QtWidgets.QPushButton("Open Folder")
        open_btn.setStyleSheet(btn_style + "background: #1976D2; color: white;")
        open_btn.clicked.connect(self.open_folder)

        reload_btn = QtWidgets.QPushButton("Reload Folder")
        reload_btn.setStyleSheet(btn_style + "background: #7B1FA2; color: white;")
        reload_btn.clicked.connect(self.reload_folder)

        top_btn = QtWidgets.QPushButton("Move Selected to Top")
        top_btn.setStyleSheet(btn_style + "background: #2E7D32; color: white;")
        top_btn.clicked.connect(self.move_to_top)

        bottom_btn = QtWidgets.QPushButton("Move Selected to Bottom")
        bottom_btn.setStyleSheet(btn_style + "background: #C62828; color: white;")
        bottom_btn.clicked.connect(self.move_to_bottom)

        clear_btn = QtWidgets.QPushButton("Clear Selection")
        clear_btn.setStyleSheet(btn_style + "background: #757575; color: white;")
        clear_btn.clicked.connect(lambda: self.list.clearSelection())

        rename_all_btn = QtWidgets.QPushButton("Rename All")
        rename_all_btn.setStyleSheet(btn_style + "background: #F57C00; color: white;")
        rename_all_btn.clicked.connect(self.rename_ordered)

        rename_selected_btn = QtWidgets.QPushButton("Rename Selected")
        rename_selected_btn.setStyleSheet(btn_style + "background: #0288D1; color: white;")
        rename_selected_btn.clicked.connect(self.rename_selected)

        self.thumb_label = QtWidgets.QLabel(f"Thumbnail Size: {DEFAULT_THUMB}px")
        self.thumb_label.setStyleSheet("font-size: 13px; color: #333;")
        self.thumb_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.thumb_slider.setRange(THUMB_MIN, THUMB_MAX)
        self.thumb_slider.setValue(DEFAULT_THUMB)
        self.thumb_slider.valueChanged.connect(self.update_thumb_size)

        # === FIRST SEARCH BAR (Double Left-Click) ===
        search1_label = QtWidgets.QLabel("Search 1 (Double Left-Click):")
        search1_label.setStyleSheet("font-weight: bold; color: #1976D2;")

        self.search_input1 = SmartLineEdit()
        self.search_input1.setPlaceholderText("Double left-click image to fill & lock")
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

        # === SECOND SEARCH BAR (Double Right-Click) ===
        search2_label = QtWidgets.QLabel("Search 2 (Double Right-Click):")
        search2_label.setStyleSheet("font-weight: bold; color: #C62828;")

        self.search_input2 = SmartLineEdit()
        self.search_input2.setPlaceholderText("Double right-click image to fill & unlock")
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

        # Add all widgets to left panel
        left_panel.addWidget(open_btn)
        left_panel.addWidget(reload_btn)
        left_panel.addWidget(top_btn)
        left_panel.addWidget(bottom_btn)
        left_panel.addWidget(clear_btn)
        left_panel.addWidget(rename_all_btn)
        left_panel.addWidget(rename_selected_btn)
        left_panel.addSpacing(10)
        left_panel.addWidget(self.thumb_label)
        left_panel.addWidget(self.thumb_slider)
        left_panel.addSpacing(10)
        left_panel.addWidget(search1_label)
        left_panel.addLayout(search_layout1)
        left_panel.addSpacing(5)
        left_panel.addWidget(search2_label)
        left_panel.addLayout(search_layout2)
        left_panel.addSpacing(25)

        self.preview = QtWidgets.QLabel("Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(400, 300)
        self.preview.setMaximumHeight(400)
        self.preview.setStyleSheet("""
            QLabel {
                background: #0d1117;
                color: #ccc;
                border: 3px dashed #444;
                border-radius: 12px;
                font-size: 18px;
                font-weight: bold;
            }
        """)
        left_panel.addWidget(self.preview)
        left_panel.addStretch()

        self.list = DragDropListWidget()
        self.list.itemSelectionChanged.connect(self.update_preview)

        # Connect double-click signals
        self.list.double_left_clicked.connect(self.handle_double_left_click)
        self.list.double_right_clicked.connect(self.handle_double_right_click)

        main_layout = QtWidgets.QHBoxLayout(central)

        # Create left panel widget with fixed width
        left_widget = QtWidgets.QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(450)  # 25% of typical 1920px width

        main_layout.addWidget(left_widget)
        main_layout.addWidget(self.list, 1)

        # Try to load last folder on startup
        last_folder = load_last_folder()
        if last_folder:
            self.folder = last_folder
            self.load_folder_contents()

    def reset_search_index(self, search_bar):
        """Reset search index when search text changes"""
        self.last_search_index[search_bar] = -1

    def handle_double_left_click(self, name, path):
        """Double left-click → put name WITHOUT EXTENSION in first search bar + lock preview"""
        name_without_ext = os.path.splitext(name)[0]
        self.search_input1.setText(name_without_ext)
        self.preview_locked = True

        # Show the image in preview
        pix = QtGui.QPixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(self.preview.size() - QtCore.QSize(40, 40),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.preview.setPixmap(scaled)

    def handle_double_right_click(self, name):
        """Double right-click → put name WITHOUT EXTENSION in second search bar + UNLOCK preview"""
        name_without_ext = os.path.splitext(name)[0]
        self.search_input2.setText(name_without_ext)
        self.preview_locked = False  # UNLOCK the preview
        self.update_preview()  # Update preview immediately

    def open_folder(self):
        last_folder = load_last_folder()
        start_dir = last_folder if last_folder else ""
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Image Folder", start_dir)
        if not folder: return
        self.folder = folder
        save_last_folder(folder)  # Save for next time
        self.load_folder_contents()

    def load_folder_contents(self):
        """Load images from the current folder"""
        if not self.folder:
            return

        self.list.clear()
        self.list.thumbnail_cache.clear()

        files = [f for f in os.listdir(self.folder) if os.path.splitext(f)[1].lower() in SUPPORTED_EXT]
        files.sort(key=natural_key)

        for f in files:
            path = os.path.join(self.folder, f)
            item = QtWidgets.QListWidgetItem(os.path.basename(f))
            item.setData(Qt.UserRole, path)
            item.setIcon(self.list.get_thumbnail_icon(path))
            self.list.addItem(item)

        self.setWindowTitle(f"Image Scene Flow Organizer — {len(files)} images")

    def reload_folder(self):
        """Reload folder: rename existing files first, then load all files including new ones"""
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

        # STEP 1 — RENAME EXISTING FILES TO PRESERVE ORDER
        temp_paths = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            old = item.data(Qt.UserRole)
            ext = os.path.splitext(old)[1]
            tmp = os.path.join(self.folder, f"__TMP_RENAME_{i}{ext}")
            try:
                os.rename(old, tmp)
                temp_paths.append((tmp, ext))
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to rename: {e}")
                return

        # STEP 2 — FINAL SEQUENCE 1,2,3,...
        for idx, (tmp, ext) in enumerate(temp_paths, start=1):
            new = os.path.join(self.folder, f"{idx}{ext}")
            try:
                os.rename(tmp, new)
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to rename: {e}")
                return

        # STEP 3 — RELOAD ALL FILES (including new ones)
        self.load_folder_contents()
        QtWidgets.QMessageBox.information(self, "Success", "Folder reloaded! Existing files renamed, new files loaded.")

    def update_thumb_size(self, val):
        self.thumb_label.setText(f"Thumbnail Size: {val}px")
        self.list.setThumbnailSize(val)

    def update_preview(self):
        """Only update preview if not locked by left double-click"""
        if self.preview_locked:
            return  # Don't update if locked

        sel = self.list.selectedItems()
        if not sel:
            self.preview.setText("Preview\n(Double LEFT-click: lock | Double RIGHT-click: unlock)")
            self.preview.setPixmap(QtGui.QPixmap())
            return
        path = sel[0].data(Qt.UserRole)
        pix = QtGui.QPixmap(path)
        if not pix.isNull():
            scaled = pix.scaled(self.preview.size() - QtCore.QSize(40, 40),
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
        """Rename all images starting from 1, 2, 3… (collision safe)."""
        if not self.folder or self.list.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Error", "No images loaded!")
            return

        if QtWidgets.QMessageBox.question(self, "Rename All",
                                          f"Rename all {self.list.count()} images to 1, 2, 3, etc.?",
                                          QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return

        # STEP 1 — TEMPORARY NAMES
        temp_paths = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            old = item.data(Qt.UserRole)
            ext = os.path.splitext(old)[1]
            tmp = os.path.join(self.folder, f"__TMP_RENAME_{i}{ext}")
            os.rename(old, tmp)
            temp_paths.append((item, tmp, ext))

        # STEP 2 — FINAL SEQUENCE 1,2,3,...
        renamed = 0
        for idx, (item, tmp, ext) in enumerate(temp_paths, start=1):
            new = os.path.join(self.folder, f"{idx}{ext}")
            os.rename(tmp, new)
            item.setData(Qt.UserRole, new)
            item.setText(os.path.basename(new))
            renamed += 1

        QtWidgets.QMessageBox.information(self, "Done", f"Renamed {renamed} images!")

    def rename_selected(self):
        sel = self.list.selectedItems()
        if not sel:
            QtWidgets.QMessageBox.warning(self, "No Selection", "Please select at least one image.")
            return

        base, ok = QtWidgets.QInputDialog.getText(self, "Rename Selected", "Enter base name (e.g. 0):")
        if not ok or not base.strip():
            return
        base = base.strip()

        renamed_items = sorted(sel, key=lambda x: self.list.row(x))
        max_counter = 0
        pattern = re.compile(rf"^{re.escape(base)}_(\d{{6}})\.[a-zA-Z]{{3,4}}$", re.IGNORECASE)
        for i in range(self.list.count()):
            name = self.list.item(i).text()
            match = pattern.match(name)
            if match:
                max_counter = max(max_counter, int(match.group(1)))

        counter = max_counter + 1
        used_names = {self.list.item(i).text() for i in range(self.list.count())}
        new_items = []

        for item in renamed_items:
            old_path = item.data(Qt.UserRole)
            ext = os.path.splitext(old_path)[1]
            new_name = f"{base}_{counter:06d}{ext}"
            while new_name in used_names:
                counter += 1
                new_name = f"{base}_{counter:06d}{ext}"

            new_path = os.path.join(self.folder, new_name)
            try:
                os.rename(old_path, new_path)
                item.setText(new_name)
                item.setData(Qt.UserRole, new_path)
                used_names.add(new_name)
                new_items.append(item)
                counter += 1
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Error", f"Failed to rename: {e}")
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
        """Search for image in specified search bar (1 or 2) with proper cycling"""
        text = self.search_input1.text() if search_bar == 1 else self.search_input2.text()
        text = text.strip().lower()
        if not text:
            return

        total = self.list.count()
        if total == 0:
            return

        # Get the last search index for this search bar
        start_index = self.last_search_index[search_bar]

        # If this is the first search or text changed, start from current selection or beginning
        if start_index == -1:
            selected = self.list.selectedItems()
            if selected:
                start_index = self.list.row(selected[0])
            else:
                start_index = -1 if not prev else 0

        # Search in the appropriate direction
        if prev:
            # Search upward (backward)
            for offset in range(1, total + 1):
                idx = (start_index - offset) % total
                item = self.list.item(idx)
                if text in item.text().lower():
                    self.list.clearSelection()
                    item.setSelected(True)
                    self.list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                    self.last_search_index[search_bar] = idx
                    return
        else:
            # Search downward (forward)
            for offset in range(1, total + 1):
                idx = (start_index + offset) % total
                item = self.list.item(idx)
                if text in item.text().lower():
                    self.list.clearSelection()
                    item.setSelected(True)
                    self.list.scrollToItem(item, QAbstractItemView.PositionAtCenter)
                    self.last_search_index[search_bar] = idx
                    return


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ImageOrganizer()
    win.show()
    sys.exit(app.exec_())