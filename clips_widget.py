"""
ClipPanel — save/manage timestamped audio clips for Anki export.

Clips are stored in a JSON file next to the audio:
    /path/to/audio.mp3  →  /path/to/audio.clips.json

Each clip:  {"start": float, "end": float, "subtitle": str}
"""

import json
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _fmt(sec: float) -> str:
    """Format seconds as M:SS.f  e.g. 1:23.4"""
    m = int(sec // 60)
    s = sec - m * 60
    return f"{m}:{s:04.1f}"


class ClipPanel(QWidget):
    """
    Right-side panel that replaces the old subtitle text area.

    Signals
    -------
    clip_selected(start, end) — user clicked a clip; main window should
                                 set the waveform selection accordingly.
    """

    clip_selected = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._clips: list[dict] = []
        self._clips_file: Optional[Path] = None
        self._cur_start = 0.0
        self._cur_end = 0.0
        self._from_jump = False  # suppress feedback loop on programmatic selection

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 4, 4)
        layout.setSpacing(5)

        # ---- Header ----
        self._header = QLabel("Clips")
        self._header.setStyleSheet("color: #4a9eff; font-weight: bold; font-size: 10pt;")
        layout.addWidget(self._header)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #1a4a80;")
        layout.addWidget(sep)

        # ---- Current selection display ----
        self._sel_label = QLabel("No selection")
        self._sel_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        layout.addWidget(self._sel_label)

        # ---- Subtitle input ----
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText(
            "Enter subtitle / notes for the selected region…\n\n"
            "e.g.  橋 (hashi)  — pitch: LH  →  bridge\n"
            "      箸 (hashi)  — pitch: HL  →  chopsticks"
        )
        self._text.setMaximumHeight(80)
        layout.addWidget(self._text)

        # ---- Save / Clear ----
        input_btns = QHBoxLayout()
        self._save_btn = QPushButton("Save Clip")
        self._save_btn.setToolTip("Save current selection + subtitle to the clip list")
        self._save_btn.clicked.connect(self._save_clip)
        self._clear_btn = QPushButton("Clear Text")
        self._clear_btn.clicked.connect(self._text.clear)
        input_btns.addWidget(self._save_btn)
        input_btns.addWidget(self._clear_btn)
        input_btns.addStretch()
        layout.addLayout(input_btns)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #1a4a80;")
        layout.addWidget(sep2)

        # ---- Clip list ----
        list_hdr = QLabel("Saved clips  (click to jump)")
        list_hdr.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(list_hdr)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list)

        # ---- Delete ----
        self._del_btn = QPushButton("Delete Selected")
        self._del_btn.clicked.connect(self._delete_selected)
        layout.addWidget(self._del_btn)

    # ------------------------------------------------------------------
    # Public API called by main window
    # ------------------------------------------------------------------

    def load_for_audio(self, audio_path: str):
        """Load (or create) the clips file for this audio file."""
        p = Path(audio_path)
        self._clips_file = p.parent / (p.stem + ".clips.json")
        self._header.setText(f"Clips  ·  {p.name}")
        self._clips = []
        if self._clips_file.exists():
            try:
                loaded = json.loads(self._clips_file.read_text())
                if isinstance(loaded, list):
                    self._clips = loaded
            except Exception:
                pass
        self._refresh_list()
        self._text.clear()

    def update_selection(self, start: float, end: float):
        """Called whenever the waveform selection changes."""
        if self._from_jump:
            return  # we caused this change — ignore it
        self._cur_start = start
        self._cur_end = end
        self._sel_label.setText(
            f"{_fmt(start)} – {_fmt(end)}  ({end - start:.2f} s)"
        )
        self._list.clearSelection()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save_clip(self):
        text = self._text.toPlainText().strip()
        if not text:
            return

        # Update if a clip with the same timestamps already exists
        for i, clip in enumerate(self._clips):
            if (abs(clip["start"] - self._cur_start) < 0.01
                    and abs(clip["end"] - self._cur_end) < 0.01):
                self._clips[i]["subtitle"] = text
                self._refresh_list()
                self._save_file()
                return

        # New clip
        self._clips.append({
            "start": self._cur_start,
            "end":   self._cur_end,
            "subtitle": text,
        })
        self._clips.sort(key=lambda c: c["start"])
        self._refresh_list()
        self._save_file()

    def _on_item_clicked(self, item: QListWidgetItem):
        idx = self._list.row(item)
        if not (0 <= idx < len(self._clips)):
            return
        clip = self._clips[idx]
        self._from_jump = True
        self._cur_start = clip["start"]
        self._cur_end = clip["end"]
        self._sel_label.setText(
            f"{_fmt(clip['start'])} – {_fmt(clip['end'])}  "
            f"({clip['end'] - clip['start']:.2f} s)"
        )
        self._text.setPlainText(clip["subtitle"])
        self.clip_selected.emit(clip["start"], clip["end"])
        self._from_jump = False

    def _delete_selected(self):
        rows = sorted(
            {self._list.row(i) for i in self._list.selectedItems()},
            reverse=True,
        )
        for r in rows:
            if 0 <= r < len(self._clips):
                del self._clips[r]
        self._refresh_list()
        self._save_file()
        self._text.clear()

    def _refresh_list(self):
        self._list.clear()
        for clip in self._clips:
            label = (
                f"{_fmt(clip['start'])} – {_fmt(clip['end'])}"
                f"   {clip['subtitle']}"
            )
            self._list.addItem(label)

    def _save_file(self):
        if self._clips_file is None:
            return
        try:
            self._clips_file.write_text(json.dumps(self._clips, indent=2))
        except Exception:
            pass
