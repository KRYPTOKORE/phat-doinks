"""PySide6 GUI for meme-sorter."""

import sys
import time
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QSize, QTimer
from PySide6.QtGui import QPixmap, QFont, QColor, QPalette, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QFileDialog,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QSplitter,
    QHeaderView,
    QGroupBox,
    QGridLayout,
    QMessageBox,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QTabWidget,
    QLineEdit,
)

from meme_sorter.config import build_app_config, get_state_dir, CATEGORIES_FILENAME
from meme_sorter.core import run_sort, run_recheck, run_undo, collect_unsorted, collect_for_recheck
from meme_sorter.events import EventBus, FileProcessed, ProgressUpdate, RunComplete, RunStarted
from meme_sorter.models import Category
from meme_sorter.state import StateStore


class WorkerSignals(QObject):
    """Signals to bridge threading events to Qt main thread."""
    file_processed = Signal(object)
    progress_update = Signal(object)
    run_started = Signal(object)
    run_complete = Signal(object)
    error = Signal(str)


class SortWorker:
    """Runs sorting in a background thread, emitting Qt signals."""

    def __init__(self, signals: WorkerSignals):
        self.signals = signals
        self._thread = None
        self.stop_event = threading.Event()

    def start(self, target, args):
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(target, args), daemon=True)
        self._thread.start()

    def _run(self, target, args):
        try:
            target(*args)
        except Exception as e:
            self.signals.error.emit(str(e))

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()


class ImagePreview(QLabel):
    """Widget to display the current image being processed."""

    def __init__(self):
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(200, 200)
        self.setStyleSheet("border: 1px solid #555; background-color: #1a1a1a;")
        self.setText("No image")

    def show_image(self, path: Path):
        try:
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                self.setText(path.name)
                return
            scaled = pixmap.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.setPixmap(scaled)
        except Exception:
            self.setText(path.name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.pixmap() and not self.pixmap().isNull():
            # Re-scale on resize
            pass


class CategoryTable(QTableWidget):
    """Live-updating table of category file counts."""

    def __init__(self):
        super().__init__()
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels(["Category", "Files", "Moved"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionMode(QTableWidget.NoSelection)
        self.verticalHeader().setVisible(False)
        self._counts = {}
        self._moved = {}

    def set_categories(self, categories: list[str]):
        self._counts = {c: 0 for c in categories}
        self._moved = {c: 0 for c in categories}
        self._refresh()

    def update_count(self, category: str, moved: bool):
        self._counts[category] = self._counts.get(category, 0) + 1
        if moved:
            self._moved[category] = self._moved.get(category, 0) + 1
        self._refresh()

    def load_initial_counts(self, meme_dir: Path, categories: list[str], media_exts: set[str]):
        """Load existing file counts from disk."""
        self._counts = {}
        for cat in categories:
            cat_dir = meme_dir / cat
            if cat_dir.is_dir():
                count = sum(
                    1 for f in cat_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in media_exts
                )
                self._counts[cat] = count
            else:
                self._counts[cat] = 0
        self._moved = {c: 0 for c in categories}
        self._refresh()

    def _refresh(self):
        sorted_cats = sorted(self._counts.items(), key=lambda x: -x[1])
        self.setRowCount(len(sorted_cats))
        for i, (cat, count) in enumerate(sorted_cats):
            self.setItem(i, 0, QTableWidgetItem(cat))
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.setItem(i, 1, count_item)
            moved_item = QTableWidgetItem(str(self._moved.get(cat, 0)))
            moved_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if self._moved.get(cat, 0) > 0:
                moved_item.setForeground(QColor("#f0c040"))
            self.setItem(i, 2, moved_item)


class LogPanel(QTextEdit):
    """Scrolling log of classification results."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Monospace", 9))
        self.setStyleSheet("background-color: #0d0d0d; color: #cccccc;")
        self._max_lines = 500

    def log(self, message: str, color: str = "#cccccc"):
        timestamp = time.strftime("%H:%M:%S")
        self.append(f'<span style="color: #666;">{timestamp}</span> '
                     f'<span style="color: {color};">{message}</span>')
        # Auto-scroll
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


class CategoryEditorDialog(QDialog):
    """Dialog for editing categories, descriptions, and priorities."""

    def __init__(self, categories: dict[str, Category], default_category: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Categories")
        self.setMinimumSize(800, 600)
        self.resize(900, 650)
        self._categories = {
            name: Category(name=cat.name, description=cat.description, priority=cat.priority)
            for name, cat in categories.items()
        }
        self._default_category = default_category
        self._selected_row = -1
        self._setup_ui()
        self._refresh_table()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Category table
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["Category", "Priority", "Description"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self._table.setColumnWidth(0, 200)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.currentCellChanged.connect(self._on_row_selected)
        layout.addWidget(self._table, 1)

        # Edit panel for selected category
        edit_group = QGroupBox("Edit Category")
        edit_layout = QGridLayout(edit_group)

        edit_layout.addWidget(QLabel("Name:"), 0, 0)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Category name")
        edit_layout.addWidget(self._name_edit, 0, 1)

        edit_layout.addWidget(QLabel("Priority:"), 0, 2)
        self._priority_spin = QSpinBox()
        self._priority_spin.setRange(0, 100)
        self._priority_spin.setToolTip("Higher priority = more likely to override. 0 = catch-all, 100 = always wins.")
        edit_layout.addWidget(self._priority_spin, 0, 3)

        edit_layout.addWidget(QLabel("Description:"), 1, 0, Qt.AlignTop)
        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setMaximumHeight(100)
        self._desc_edit.setPlaceholderText("Description shown to the AI model to help classify images into this category...")
        edit_layout.addWidget(self._desc_edit, 1, 1, 1, 3)

        # Apply edits to selected row
        apply_btn = QPushButton("Apply Changes")
        apply_btn.clicked.connect(self._apply_edit)
        edit_layout.addWidget(apply_btn, 2, 1)

        layout.addWidget(edit_group)

        # Buttons row
        btn_layout = QHBoxLayout()

        add_btn = QPushButton("+ Add Category")
        add_btn.setStyleSheet("background-color: #1a5c2a; border-color: #2a8c3a;")
        add_btn.clicked.connect(self._add_category)
        btn_layout.addWidget(add_btn)

        remove_btn = QPushButton("- Remove Selected")
        remove_btn.setStyleSheet("background-color: #6c1a1a; border-color: #8c2a2a;")
        remove_btn.clicked.connect(self._remove_category)
        btn_layout.addWidget(remove_btn)

        btn_layout.addStretch()

        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        btn_layout.addWidget(button_box)

        layout.addLayout(btn_layout)

    def _refresh_table(self):
        sorted_cats = sorted(self._categories.items(), key=lambda x: (-x[1].priority, x[0]))
        self._table.setRowCount(len(sorted_cats))
        for i, (name, cat) in enumerate(sorted_cats):
            name_item = QTableWidgetItem(name)
            if name == self._default_category:
                name_item.setForeground(QColor("#f0c040"))
                name_item.setToolTip("Default catch-all category")
            self._table.setItem(i, 0, name_item)

            pri_item = QTableWidgetItem(str(cat.priority))
            pri_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(i, 1, pri_item)

            desc_item = QTableWidgetItem(cat.description[:100] + ("..." if len(cat.description) > 100 else ""))
            desc_item.setForeground(QColor("#aaa"))
            self._table.setItem(i, 2, desc_item)

    def _on_row_selected(self, row, col, prev_row, prev_col):
        if row < 0:
            return
        self._selected_row = row
        name = self._table.item(row, 0).text()
        cat = self._categories.get(name)
        if cat:
            self._name_edit.setText(name)
            self._priority_spin.setValue(cat.priority)
            self._desc_edit.setPlainText(cat.description)

    def _apply_edit(self):
        if self._selected_row < 0:
            return
        old_name = self._table.item(self._selected_row, 0).text()
        new_name = self._name_edit.text().strip()
        if not new_name:
            return

        new_desc = self._desc_edit.toPlainText().strip()
        new_priority = self._priority_spin.value()

        # If renamed, remove old and add new
        if old_name != new_name:
            if new_name in self._categories and new_name != old_name:
                QMessageBox.warning(self, "Duplicate", f'Category "{new_name}" already exists.')
                return
            del self._categories[old_name]

        self._categories[new_name] = Category(
            name=new_name, description=new_desc, priority=new_priority,
        )
        self._refresh_table()

        # Re-select the edited row
        for i in range(self._table.rowCount()):
            if self._table.item(i, 0).text() == new_name:
                self._table.selectRow(i)
                break

    def _add_category(self):
        name = "New Category"
        suffix = 1
        while name in self._categories:
            name = f"New Category {suffix}"
            suffix += 1

        self._categories[name] = Category(name=name, description="", priority=5)
        self._refresh_table()

        # Select the new row
        for i in range(self._table.rowCount()):
            if self._table.item(i, 0).text() == name:
                self._table.selectRow(i)
                self._name_edit.setFocus()
                self._name_edit.selectAll()
                break

    def _remove_category(self):
        if self._selected_row < 0:
            return
        name = self._table.item(self._selected_row, 0).text()
        if name == self._default_category:
            QMessageBox.warning(self, "Cannot Remove", f'Cannot remove the default category "{name}".')
            return

        reply = QMessageBox.question(
            self, "Remove Category",
            f'Remove "{name}"? Files in this category will need re-sorting.',
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            del self._categories[name]
            self._selected_row = -1
            self._name_edit.clear()
            self._desc_edit.clear()
            self._refresh_table()

    def get_categories(self) -> dict[str, Category]:
        return self._categories


class SettingsDialog(QDialog):
    """Dialog for editing Ollama and processing settings."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(550, 450)
        self.resize(600, 500)
        self._config = config
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # === Ollama tab ===
        ollama_tab = QWidget()
        ol = QGridLayout(ollama_tab)

        ol.addWidget(QLabel("Endpoint:"), 0, 0)
        self._endpoint_edit = QLineEdit(self._config.ollama.endpoint)
        self._endpoint_edit.setPlaceholderText("http://localhost:11434")
        ol.addWidget(self._endpoint_edit, 0, 1)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_connection)
        ol.addWidget(test_btn, 0, 2)

        ol.addWidget(QLabel("Model:"), 1, 0)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setCurrentText(self._config.ollama.model)
        ol.addWidget(self._model_combo, 1, 1)

        refresh_btn = QPushButton("Refresh Models")
        refresh_btn.clicked.connect(self._refresh_models)
        ol.addWidget(refresh_btn, 1, 2)

        ol.addWidget(QLabel("Temperature:"), 2, 0)
        self._temp_spin = QSpinBox()
        self._temp_spin.setRange(0, 100)
        self._temp_spin.setValue(int(self._config.ollama.temperature * 100))
        self._temp_spin.setSuffix("%")
        self._temp_spin.setToolTip("0% = deterministic, 100% = maximum randomness. Recommended: 10%")
        ol.addWidget(self._temp_spin, 2, 1)

        ol.addWidget(QLabel("Max tokens:"), 3, 0)
        self._tokens_spin = QSpinBox()
        self._tokens_spin.setRange(50, 500)
        self._tokens_spin.setValue(self._config.ollama.max_tokens)
        self._tokens_spin.setToolTip("Max tokens for the model response. 100 is usually enough.")
        ol.addWidget(self._tokens_spin, 3, 1)

        ol.addWidget(QLabel("Timeout (sec):"), 4, 0)
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(10, 600)
        self._timeout_spin.setValue(self._config.ollama.timeout)
        self._timeout_spin.setSuffix("s")
        ol.addWidget(self._timeout_spin, 4, 1)

        ol.addWidget(QLabel("Retries:"), 5, 0)
        self._retries_spin = QSpinBox()
        self._retries_spin.setRange(0, 10)
        self._retries_spin.setValue(self._config.ollama.retries)
        ol.addWidget(self._retries_spin, 5, 1)

        self._connection_status = QLabel("")
        ol.addWidget(self._connection_status, 6, 0, 1, 3)

        ol.setRowStretch(7, 1)
        tabs.addTab(ollama_tab, "Ollama")

        # === Processing tab ===
        proc_tab = QWidget()
        pl = QGridLayout(proc_tab)

        pl.addWidget(QLabel("Concurrent workers:"), 0, 0)
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 16)
        self._workers_spin.setValue(self._config.processing.workers)
        self._workers_spin.setToolTip("Number of images to process in parallel. Higher = more GPU utilization but more VRAM needed.")
        pl.addWidget(self._workers_spin, 0, 1)

        pl.addWidget(QLabel("Save interval:"), 1, 0)
        self._save_spin = QSpinBox()
        self._save_spin.setRange(1, 500)
        self._save_spin.setValue(self._config.processing.save_interval)
        self._save_spin.setToolTip("Save progress every N images.")
        pl.addWidget(self._save_spin, 1, 1)

        pl.addWidget(QLabel("Image extensions:"), 2, 0)
        self._img_ext_edit = QLineEdit(", ".join(self._config.processing.image_extensions))
        self._img_ext_edit.setToolTip("Comma-separated list of image file extensions to process.")
        pl.addWidget(self._img_ext_edit, 2, 1)

        pl.addWidget(QLabel("Video extensions:"), 3, 0)
        self._vid_ext_edit = QLineEdit(", ".join(self._config.processing.video_extensions))
        self._vid_ext_edit.setToolTip("Comma-separated list of video file extensions to process.")
        pl.addWidget(self._vid_ext_edit, 3, 1)

        pl.setRowStretch(4, 1)
        tabs.addTab(proc_tab, "Processing")

        # === Video tab ===
        vid_tab = QWidget()
        vl = QGridLayout(vid_tab)

        vl.addWidget(QLabel("Frames to extract:"), 0, 0)
        self._frames_spin = QSpinBox()
        self._frames_spin.setRange(1, 16)
        self._frames_spin.setValue(self._config.video.num_frames)
        self._frames_spin.setToolTip("Number of frames to extract from videos and stitch into a grid for classification.")
        vl.addWidget(self._frames_spin, 0, 1)

        vl.addWidget(QLabel("Thumbnail size:"), 1, 0)
        self._thumb_spin = QSpinBox()
        self._thumb_spin.setRange(128, 2048)
        self._thumb_spin.setSingleStep(64)
        self._thumb_spin.setValue(self._config.video.thumbnail_size)
        self._thumb_spin.setSuffix("px")
        self._thumb_spin.setToolTip("Max width/height for each frame thumbnail in the grid.")
        vl.addWidget(self._thumb_spin, 1, 1)

        vl.setRowStretch(2, 1)
        tabs.addTab(vid_tab, "Video")

        # === Prompt tab ===
        prompt_tab = QWidget()
        pml = QVBoxLayout(prompt_tab)
        pml.addWidget(QLabel("Prompt preamble (instructions given to the AI before the category list):"))
        self._preamble_edit = QPlainTextEdit()
        self._preamble_edit.setPlainText(self._config.prompt_preamble or "")
        self._preamble_edit.setPlaceholderText("Instructions for the AI about how to classify images...")
        pml.addWidget(self._preamble_edit)

        pml.addWidget(QLabel("Default category (catch-all):"))
        self._default_cat_edit = QLineEdit(self._config.default_category)
        pml.addWidget(self._default_cat_edit)

        pml.addWidget(QLabel("Non-meme folder name:"))
        self._non_meme_edit = QLineEdit(self._config.non_meme_folder)
        pml.addWidget(self._non_meme_edit)

        tabs.addTab(prompt_tab, "Prompt")

        layout.addWidget(tabs)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Auto-refresh models on open
        QTimer.singleShot(100, self._refresh_models)

    def _test_connection(self):
        import requests
        endpoint = self._endpoint_edit.text().strip().rstrip("/")
        try:
            resp = requests.get(f"{endpoint}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            self._connection_status.setText(
                f'<span style="color: #88cc88;">Connected! {len(models)} model(s) available.</span>'
            )
            self._connection_status.setTextFormat(Qt.RichText)
        except Exception as e:
            self._connection_status.setText(
                f'<span style="color: #f04040;">Connection failed: {e}</span>'
            )
            self._connection_status.setTextFormat(Qt.RichText)

    def _refresh_models(self):
        import requests
        endpoint = self._endpoint_edit.text().strip().rstrip("/")
        current = self._model_combo.currentText()
        try:
            resp = requests.get(f"{endpoint}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            self._model_combo.clear()
            for m in models:
                name = m.get("name", "")
                size_gb = m.get("size", 0) / (1024**3)
                self._model_combo.addItem(f"{name}", name)
            # Re-select current model
            idx = self._model_combo.findData(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
            else:
                self._model_combo.setCurrentText(current)
            self._connection_status.setText(
                f'<span style="color: #88cc88;">Found {len(models)} model(s).</span>'
            )
            self._connection_status.setTextFormat(Qt.RichText)
        except Exception:
            # Keep editable so user can type manually
            if self._model_combo.count() == 0:
                self._model_combo.setCurrentText(current)

    def get_settings(self) -> dict:
        """Return all settings as a dict to apply to config."""
        model_text = self._model_combo.currentData()
        if not model_text:
            model_text = self._model_combo.currentText()
        return {
            "ollama": {
                "endpoint": self._endpoint_edit.text().strip(),
                "model": model_text,
                "temperature": self._temp_spin.value() / 100.0,
                "max_tokens": self._tokens_spin.value(),
                "timeout": self._timeout_spin.value(),
                "retries": self._retries_spin.value(),
            },
            "processing": {
                "workers": self._workers_spin.value(),
                "save_interval": self._save_spin.value(),
                "image_extensions": [e.strip() for e in self._img_ext_edit.text().split(",") if e.strip()],
                "video_extensions": [e.strip() for e in self._vid_ext_edit.text().split(",") if e.strip()],
            },
            "video": {
                "num_frames": self._frames_spin.value(),
                "thumbnail_size": self._thumb_spin.value(),
            },
            "prompt": {
                "preamble": self._preamble_edit.toPlainText().strip(),
                "default_category": self._default_cat_edit.text().strip(),
                "non_meme_folder": self._non_meme_edit.text().strip(),
            },
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phat Doinks")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

        self._meme_dir = None
        self._config = None
        self._state = None
        self._bus = None
        self._signals = WorkerSignals()
        self._worker = SortWorker(self._signals)

        # Connect signals
        self._signals.file_processed.connect(self._on_file_processed)
        self._signals.progress_update.connect(self._on_progress_update)
        self._signals.run_started.connect(self._on_run_started)
        self._signals.run_complete.connect(self._on_run_complete)
        self._signals.error.connect(self._on_error)

        self._setup_ui()
        self._apply_dark_theme()

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1e1e1e; }
            QWidget { background-color: #1e1e1e; color: #e0e0e0; }
            QGroupBox {
                border: 1px solid #444;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }
            QPushButton {
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 16px;
                color: #e0e0e0;
            }
            QPushButton:hover { background-color: #3d3d3d; }
            QPushButton:pressed { background-color: #4d4d4d; }
            QPushButton:disabled { color: #666; background-color: #222; }
            QPushButton#startBtn {
                background-color: #1a5c2a;
                border-color: #2a8c3a;
                font-weight: bold;
            }
            QPushButton#startBtn:hover { background-color: #2a7c3a; }
            QPushButton#stopBtn {
                background-color: #6c1a1a;
                border-color: #8c2a2a;
            }
            QPushButton#stopBtn:hover { background-color: #8c2a2a; }
            QPushButton#undoBtn {
                background-color: #4a3a1a;
                border-color: #6a5a2a;
            }
            QPushButton#undoBtn:hover { background-color: #6a5a2a; }
            QProgressBar {
                border: 1px solid #555;
                border-radius: 4px;
                text-align: center;
                background-color: #2d2d2d;
                color: #e0e0e0;
                height: 22px;
            }
            QProgressBar::chunk {
                background-color: #3a7a4a;
                border-radius: 3px;
            }
            QTableWidget {
                background-color: #1a1a1a;
                gridline-color: #333;
                border: 1px solid #444;
            }
            QTableWidget::item { padding: 2px 6px; }
            QHeaderView::section {
                background-color: #2d2d2d;
                border: 1px solid #444;
                padding: 4px;
                font-weight: bold;
            }
            QComboBox, QSpinBox {
                background-color: #2d2d2d;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 4px;
                color: #e0e0e0;
            }
            QComboBox::drop-down { border: none; }
            QSplitter::handle { background-color: #444; }
            QLabel#statsLabel { font-size: 13px; }
        """)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)

        # === Top bar: folder selection + controls ===
        top_bar = QHBoxLayout()

        self._folder_label = QLabel("No folder selected")
        self._folder_label.setStyleSheet("font-size: 12px; color: #aaa;")
        top_bar.addWidget(self._folder_label, 1)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_folder)
        top_bar.addWidget(browse_btn)

        main_layout.addLayout(top_bar)

        # === Controls bar ===
        controls = QHBoxLayout()

        mode_label = QLabel("Mode:")
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Sort (unsorted)", "Recheck all", "Recheck folder..."])
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        controls.addWidget(mode_label)
        controls.addWidget(self._mode_combo)

        self._folder_combo = QComboBox()
        self._folder_combo.setVisible(False)
        self._folder_combo.setMinimumWidth(200)
        controls.addWidget(self._folder_combo)

        controls.addSpacing(16)

        dry_run_label = QLabel("Dry run:")
        self._dry_run_check = QCheckBox()
        controls.addWidget(dry_run_label)
        controls.addWidget(self._dry_run_check)

        limit_label = QLabel("Limit:")
        self._limit_spin = QSpinBox()
        self._limit_spin.setRange(0, 999999)
        self._limit_spin.setSpecialValueText("All")
        controls.addWidget(limit_label)
        controls.addWidget(self._limit_spin)

        controls.addSpacing(16)

        model_label = QLabel("Backend:")
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(200)
        self._model_combo.setEditable(False)
        controls.addWidget(model_label)
        controls.addWidget(self._model_combo)

        controls.addStretch()

        self._start_btn = QPushButton("Start")
        self._start_btn.setObjectName("startBtn")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_sort)
        controls.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setObjectName("stopBtn")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop_sort)
        controls.addWidget(self._stop_btn)

        self._undo_btn = QPushButton("Undo Last Run")
        self._undo_btn.setObjectName("undoBtn")
        self._undo_btn.setEnabled(False)
        self._undo_btn.clicked.connect(self._undo_last)
        controls.addWidget(self._undo_btn)

        self._edit_cats_btn = QPushButton("Edit Categories")
        self._edit_cats_btn.setEnabled(False)
        self._edit_cats_btn.clicked.connect(self._edit_categories)
        controls.addWidget(self._edit_cats_btn)

        self._settings_btn = QPushButton("Settings")
        self._settings_btn.setEnabled(False)
        self._settings_btn.clicked.connect(self._open_settings)
        controls.addWidget(self._settings_btn)

        main_layout.addLayout(controls)

        # === Progress bar ===
        self._progress_bar = QProgressBar()
        self._progress_bar.setFormat("%v / %m  (%p%)")
        self._progress_bar.setValue(0)
        main_layout.addWidget(self._progress_bar)

        # === Stats line ===
        self._stats_label = QLabel("Ready")
        self._stats_label.setObjectName("statsLabel")
        main_layout.addWidget(self._stats_label)

        # === Main content: splitter with preview+table and log ===
        splitter = QSplitter(Qt.Vertical)

        # Top half: image preview + category table
        top_widget = QWidget()
        top_layout = QHBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)

        # Image preview
        preview_group = QGroupBox("Current Image")
        preview_layout = QVBoxLayout(preview_group)
        self._image_preview = ImagePreview()
        self._image_name_label = QLabel("")
        self._image_name_label.setAlignment(Qt.AlignCenter)
        self._image_name_label.setStyleSheet("color: #aaa; font-size: 11px;")
        self._image_result_label = QLabel("")
        self._image_result_label.setAlignment(Qt.AlignCenter)
        self._image_result_label.setStyleSheet("font-size: 12px; font-weight: bold;")
        preview_layout.addWidget(self._image_preview, 1)
        preview_layout.addWidget(self._image_name_label)
        preview_layout.addWidget(self._image_result_label)
        top_layout.addWidget(preview_group, 2)

        # Category table
        table_group = QGroupBox("Categories")
        table_layout = QVBoxLayout(table_group)
        self._category_table = CategoryTable()
        table_layout.addWidget(self._category_table)
        top_layout.addWidget(table_group, 3)

        splitter.addWidget(top_widget)

        # Bottom half: log
        log_group = QGroupBox("Log")
        log_layout = QVBoxLayout(log_group)
        self._log_panel = LogPanel()
        log_layout.addWidget(self._log_panel)

        splitter.addWidget(log_group)
        splitter.setSizes([400, 250])

        main_layout.addWidget(splitter, 1)

    def _browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Meme Folder")
        if not folder:
            return
        self._load_folder(Path(folder))

    def _load_folder(self, meme_dir: Path):
        self._meme_dir = meme_dir
        self._folder_label.setText(str(meme_dir))

        state_dir = get_state_dir(meme_dir)
        self._config = build_app_config(meme_dir)
        self._state = StateStore(state_dir / "state.db")

        # Populate category table with existing counts
        media_exts = (
            {"." + e for e in self._config.processing.image_extensions}
            | {"." + e for e in self._config.processing.video_extensions}
        )
        cat_names = list(self._config.categories.keys())
        self._category_table.load_initial_counts(meme_dir, cat_names, media_exts)

        # Populate folder combo for recheck mode
        self._folder_combo.clear()
        for cat in sorted(cat_names):
            cat_dir = meme_dir / cat
            if cat_dir.is_dir():
                self._folder_combo.addItem(cat)

        # Populate backend/model dropdown
        self._refresh_models()

        self._start_btn.setEnabled(True)
        self._undo_btn.setEnabled(True)
        self._edit_cats_btn.setEnabled(True)
        self._settings_btn.setEnabled(True)
        self._log_panel.log(f"Loaded: {meme_dir}", "#88cc88")
        if self._config.backend == "claude":
            self._log_panel.log(
                f"Backend: Claude ({self._config.claude.model})",
                "#8888cc",
            )
        else:
            self._log_panel.log(
                f"Backend: Ollama ({self._config.ollama.model} @ {self._config.ollama.endpoint})",
                "#8888cc",
            )
        self._log_panel.log(f"Categories: {len(cat_names)}", "#8888cc")

    def _refresh_models(self):
        """Populate the backend/model dropdown."""
        import requests
        self._model_combo.clear()

        # Always add Claude option
        claude_label = f"Claude ({self._config.claude.model})"
        self._model_combo.addItem(claude_label, "claude")

        # Try to fetch Ollama models
        try:
            endpoint = self._config.ollama.endpoint
            resp = requests.get(f"{endpoint}/api/tags", timeout=3)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            for m in sorted(models, key=lambda x: x["name"]):
                name = m["name"]
                self._model_combo.addItem(f"Ollama ({name})", f"ollama:{name}")
        except Exception:
            # Ollama not running — add configured model as fallback
            self._model_combo.addItem(
                f"Ollama ({self._config.ollama.model})",
                f"ollama:{self._config.ollama.model}",
            )

        # Select active backend
        if self._config.backend == "claude":
            self._model_combo.setCurrentIndex(0)
        else:
            for i in range(self._model_combo.count()):
                data = self._model_combo.itemData(i)
                if data == f"ollama:{self._config.ollama.model}":
                    self._model_combo.setCurrentIndex(i)
                    break

    def _on_mode_changed(self, index):
        self._folder_combo.setVisible(index == 2)

    def _start_sort(self):
        if not self._meme_dir or not self._config:
            return
        if self._worker.is_running():
            return

        self._bus = EventBus()

        # Wire events to Qt signals
        self._bus.subscribe(FileProcessed, lambda e: self._signals.file_processed.emit(e))
        self._bus.subscribe(ProgressUpdate, lambda e: self._signals.progress_update.emit(e))
        self._bus.subscribe(RunStarted, lambda e: self._signals.run_started.emit(e))
        self._bus.subscribe(RunComplete, lambda e: self._signals.run_complete.emit(e))

        # Apply selected backend/model from dropdown
        selected_data = self._model_combo.currentData()
        if selected_data == "claude":
            self._config.backend = "claude"
            self._log_panel.log(f"Using: Claude ({self._config.claude.model})", "#8888cc")
        elif selected_data and selected_data.startswith("ollama:"):
            self._config.backend = "ollama"
            model_name = selected_data[7:]
            self._config.ollama.model = model_name
            self._log_panel.log(f"Using: Ollama ({model_name})", "#8888cc")

        dry_run = self._dry_run_check.isChecked()
        limit = self._limit_spin.value()
        mode = self._mode_combo.currentIndex()

        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._undo_btn.setEnabled(False)
        self._log_panel.log("Starting...", "#f0c040")

        stop = self._worker.stop_event
        if mode == 0:
            # Sort unsorted
            args = (self._meme_dir, self._config, self._bus, self._state, dry_run, limit, None, stop)
            self._worker.start(run_sort, args)
        elif mode == 1:
            # Recheck all
            args = (self._meme_dir, self._config, self._bus, self._state, dry_run, limit, None, None, stop)
            self._worker.start(run_recheck, args)
        elif mode == 2:
            # Recheck specific folder
            folder = self._folder_combo.currentText()
            args = (self._meme_dir, self._config, self._bus, self._state, dry_run, limit, folder, None, stop)
            self._worker.start(run_recheck, args)

    def _stop_sort(self):
        # Signal stop — the worker thread will finish current item then exit
        self._worker.stop_event.set()
        self._stop_btn.setEnabled(False)
        self._log_panel.log("Stop requested — finishing current image...", "#f04040")

    def _undo_last(self):
        if not self._state:
            return
        batch = self._state.get_undo_batch()
        if not batch:
            self._log_panel.log("Nothing to undo.", "#888")
            return

        reply = QMessageBox.question(
            self,
            "Undo Last Run",
            f"Revert {len(batch)} file moves?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            reverted = run_undo(self._state)
            self._log_panel.log(f"Reverted {reverted} moves.", "#88cc88")
            # Refresh table
            if self._config:
                media_exts = (
                    {"." + e for e in self._config.processing.image_extensions}
                    | {"." + e for e in self._config.processing.video_extensions}
                )
                self._category_table.load_initial_counts(
                    self._meme_dir,
                    list(self._config.categories.keys()),
                    media_exts,
                )

    def _edit_categories(self):
        if not self._config:
            return
        if self._worker.is_running():
            QMessageBox.warning(self, "Busy", "Cannot edit categories while sorting is in progress.")
            return

        dialog = CategoryEditorDialog(
            self._config.categories,
            self._config.default_category,
            parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            new_cats = dialog.get_categories()
            self._config.categories = new_cats
            self._save_categories(new_cats)

            # Refresh the category table
            media_exts = (
                {"." + e for e in self._config.processing.image_extensions}
                | {"." + e for e in self._config.processing.video_extensions}
            )
            self._category_table.load_initial_counts(
                self._meme_dir, list(new_cats.keys()), media_exts,
            )

            # Refresh folder combo
            self._folder_combo.clear()
            for cat in sorted(new_cats.keys()):
                cat_dir = self._meme_dir / cat
                if cat_dir.is_dir():
                    self._folder_combo.addItem(cat)

            self._log_panel.log(f"Categories updated: {len(new_cats)} categories", "#88cc88")

    def _save_categories(self, categories: dict[str, Category]):
        """Save categories back to the TOML file."""
        state_dir = get_state_dir(self._meme_dir)
        cat_path = state_dir / CATEGORIES_FILENAME

        lines = []
        lines.append(f'default_category = "{self._config.default_category}"')
        lines.append(f'non_meme_folder = "{self._config.non_meme_folder}"')
        lines.append("")

        if self._config.prompt_preamble:
            lines.append("[prompt_rules]")
            lines.append(f'preamble = """')
            lines.append(self._config.prompt_preamble)
            lines.append('"""')
            lines.append("")

        for name, cat in sorted(categories.items(), key=lambda x: (-x[1].priority, x[0])):
            # Quote the key if it contains spaces or special chars
            key = f'"{name}"' if " " in name or "#" in name else name
            lines.append(f"[categories.{key}]")
            # Escape description for TOML
            desc = cat.description.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'description = "{desc}"')
            lines.append(f"priority = {cat.priority}")
            lines.append("")

        cat_path.write_text("\n".join(lines))

    def _open_settings(self):
        if not self._config:
            return
        if self._worker.is_running():
            QMessageBox.warning(self, "Busy", "Cannot edit settings while sorting is in progress.")
            return

        dialog = SettingsDialog(self._config, parent=self)
        if dialog.exec() == QDialog.Accepted:
            settings = dialog.get_settings()
            self._apply_settings(settings)
            self._save_config_toml(settings)
            if self._config.backend == "claude":
                backend_str = f"Claude ({self._config.claude.model})"
            else:
                backend_str = f"Ollama ({self._config.ollama.model})"
            self._log_panel.log(
                f"Settings saved. Backend: {backend_str} | "
                f"Workers: {self._config.processing.workers}",
                "#88cc88",
            )

    def _apply_settings(self, settings: dict):
        """Apply settings dict to the live config."""
        ol = settings["ollama"]
        self._config.ollama.endpoint = ol["endpoint"]
        self._config.ollama.model = ol["model"]
        self._config.ollama.temperature = ol["temperature"]
        self._config.ollama.max_tokens = ol["max_tokens"]
        self._config.ollama.timeout = ol["timeout"]
        self._config.ollama.retries = ol["retries"]

        pr = settings["processing"]
        self._config.processing.workers = pr["workers"]
        self._config.processing.save_interval = pr["save_interval"]
        self._config.processing.image_extensions = pr["image_extensions"]
        self._config.processing.video_extensions = pr["video_extensions"]

        vd = settings["video"]
        self._config.video.num_frames = vd["num_frames"]
        self._config.video.thumbnail_size = vd["thumbnail_size"]

        pm = settings["prompt"]
        if pm["preamble"]:
            self._config.prompt_preamble = pm["preamble"]
        if pm["default_category"]:
            self._config.default_category = pm["default_category"]
        if pm["non_meme_folder"]:
            self._config.non_meme_folder = pm["non_meme_folder"]

    def _save_config_toml(self, settings: dict):
        """Save config settings back to config.toml."""
        from meme_sorter.config import CONFIG_FILENAME
        state_dir = get_state_dir(self._meme_dir)
        config_path = state_dir / CONFIG_FILENAME

        ol = settings["ollama"]
        pr = settings["processing"]
        vd = settings["video"]

        img_exts = ", ".join(f'"{e}"' for e in pr["image_extensions"])
        vid_exts = ", ".join(f'"{e}"' for e in pr["video_extensions"])

        content = f"""\
[ollama]
endpoint = "{ol['endpoint']}"
model = "{ol['model']}"
timeout = {ol['timeout']}
temperature = {ol['temperature']}
max_tokens = {ol['max_tokens']}
retries = {ol['retries']}

[processing]
workers = {pr['workers']}
save_interval = {pr['save_interval']}
image_extensions = [{img_exts}]
video_extensions = [{vid_exts}]

[video]
num_frames = {vd['num_frames']}
thumbnail_size = {vd['thumbnail_size']}
"""
        config_path.write_text(content)

        # Also save prompt settings to categories.toml
        pm = settings["prompt"]
        if pm["preamble"] or pm["default_category"] or pm["non_meme_folder"]:
            self._config.prompt_preamble = pm.get("preamble", self._config.prompt_preamble)
            self._config.default_category = pm.get("default_category", self._config.default_category)
            self._config.non_meme_folder = pm.get("non_meme_folder", self._config.non_meme_folder)
            self._save_categories(self._config.categories)

    def _on_file_processed(self, event: FileProcessed):
        # Update image preview - use dest_path if file was moved
        preview_path = event.dest_path or event.path
        suffix = preview_path.suffix.lower()
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
        if suffix in image_exts:
            self._image_preview.show_image(preview_path)
        else:
            self._image_preview.setText(f"[{suffix}]\n{preview_path.name}")

        self._image_name_label.setText(event.path.name[:60])

        if event.error:
            self._image_result_label.setText(f"ERROR: {event.error[:40]}")
            self._image_result_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #f04040;"
            )
            self._log_panel.log(f"ERROR [{event.path.name[:40]}]: {event.error}", "#f04040")
        elif event.moved:
            self._image_result_label.setText(
                f"{event.current_category} → {event.new_category}"
            )
            self._image_result_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #f0c040;"
            )
            self._log_panel.log(
                f"MOVE {event.path.name[:40]}  [{event.current_category}] → [{event.new_category}]",
                "#f0c040",
            )
        else:
            self._image_result_label.setText(f"✓ {event.new_category}")
            self._image_result_label.setStyleSheet(
                "font-size: 12px; font-weight: bold; color: #88cc88;"
            )

        # Update table
        self._category_table.update_count(event.new_category, event.moved)

    def _on_progress_update(self, event: ProgressUpdate):
        self._progress_bar.setMaximum(event.total)
        self._progress_bar.setValue(event.current)

        elapsed = event.elapsed
        rate = event.current / elapsed if elapsed > 0 else 0
        eta = (event.total - event.current) / rate if rate > 0 else 0

        self._stats_label.setText(
            f"Processing: {event.current}/{event.total}  |  "
            f"Speed: {rate:.1f}/s  |  "
            f"Elapsed: {elapsed / 60:.1f}m  |  "
            f"ETA: {eta / 60:.1f}m"
        )

    def _on_run_started(self, event: RunStarted):
        self._progress_bar.setMaximum(event.total)
        self._progress_bar.setValue(0)
        self._log_panel.log(
            f"{event.mode.upper()}: {event.total} files to process", "#88cc88"
        )

    def _on_run_complete(self, event: RunComplete):
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._undo_btn.setEnabled(True)

        self._stats_label.setText(
            f"Complete!  Processed: {event.processed}  |  "
            f"Moved: {event.moved}  |  Kept: {event.kept}  |  "
            f"Errors: {event.errors}  |  Duration: {event.duration / 60:.1f}m"
        )
        self._log_panel.log(
            f"DONE — {event.processed} processed, {event.moved} moved, "
            f"{event.errors} errors in {event.duration / 60:.1f}m",
            "#88cc88",
        )

    def _on_error(self, message: str):
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._undo_btn.setEnabled(True)
        self._log_panel.log(f"FATAL: {message}", "#f04040")
        self._stats_label.setText(f"Error: {message}")

    def closeEvent(self, event):
        if self._state:
            self._state.close()
        super().closeEvent(event)


def launch_gui(meme_dir: Path | None = None):
    """Launch the GUI application."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Phat Doinks")

    # Set application icon
    assets_dir = Path(__file__).parent.parent.parent / "assets"
    for icon_name in ("icon.png", "icon.svg"):
        icon_path = assets_dir / icon_name
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))
            break

    window = MainWindow()
    if meme_dir:
        window._load_folder(meme_dir)
    window.show()

    sys.exit(app.exec())
