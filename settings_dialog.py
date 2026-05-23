from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from settings_manager import ACTION_LABELS, DEFAULT_KEYBINDINGS


class SettingsDialog(QDialog):
    """
    Modal dialog for editing keybindings.

    Each row shows the action name and a QKeySequenceEdit.
    The user can click a cell and press the new key combo.
    Changes are only written when Save is clicked.
    """

    def __init__(self, current_keybindings: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — VoicePattern")
        self.setMinimumWidth(520)
        self.setMinimumHeight(420)

        self._kb = dict(current_keybindings)
        self._editors: dict[str, QKeySequenceEdit] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        hint = QLabel(
            "Click a Key Binding cell, then press the desired key combination."
        )
        hint.setStyleSheet("color: #aaa; font-size: 9pt;")
        layout.addWidget(hint)

        # Table
        self._table = QTableWidget(len(ACTION_LABELS), 2)
        self._table.setHorizontalHeaderLabels(["Action", "Key Binding"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed
        )
        self._table.setColumnWidth(1, 190)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, (action, label) in enumerate(ACTION_LABELS.items()):
            name_item = QTableWidgetItem(label)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, name_item)

            editor = QKeySequenceEdit(
                QKeySequence(self._kb.get(action, DEFAULT_KEYBINDINGS.get(action, "")))
            )
            editor.keySequenceChanged.connect(
                lambda seq, a=action: self._on_changed(a, seq)
            )
            self._table.setCellWidget(row, 1, editor)
            self._editors[action] = editor

        layout.addWidget(self._table)

        # Conflict warning label
        self._conflict_label = QLabel("")
        self._conflict_label.setStyleSheet("color: #ff5252; font-size: 9pt;")
        layout.addWidget(self._conflict_label)

        # Buttons
        btn_row = QHBoxLayout()
        self._reset_btn = QPushButton("Reset Defaults")
        self._reset_btn.clicked.connect(self._reset_defaults)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self.reject)
        self._save_btn = QPushButton("Save")
        self._save_btn.setDefault(True)
        self._save_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._save_btn)
        layout.addLayout(btn_row)

        self._apply_style()

    # ------------------------------------------------------------------

    def get_keybindings(self) -> dict[str, str]:
        """Return the current (possibly edited) keybindings."""
        # Sync from editors in case keySequenceChanged didn't fire for the last edit
        for action, editor in self._editors.items():
            self._kb[action] = editor.keySequence().toString()
        return dict(self._kb)

    # ------------------------------------------------------------------

    def _on_changed(self, action: str, seq: QKeySequence):
        self._kb[action] = seq.toString()
        self._check_conflicts()

    def _check_conflicts(self):
        """Warn if two actions share the same non-empty key sequence."""
        seen: dict[str, str] = {}
        conflicts: list[str] = []
        for action, key in self._kb.items():
            if not key:
                continue
            if key in seen:
                conflicts.append(
                    f"'{key}' used by both '{ACTION_LABELS[seen[key]]}' "
                    f"and '{ACTION_LABELS[action]}'"
                )
            else:
                seen[key] = action
        self._conflict_label.setText(
            "⚠ " + " | ".join(conflicts) if conflicts else ""
        )

    def _reset_defaults(self):
        for action, default_key in DEFAULT_KEYBINDINGS.items():
            if action in self._editors:
                self._editors[action].setKeySequence(QKeySequence(default_key))
                self._kb[action] = default_key
        self._conflict_label.setText("")

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog, QWidget  { background: #16213e; color: #e0e0e0; }
            QTableWidget      { background: #0d1b2a; gridline-color: #1a4a80;
                                border: 1px solid #1a4a80; }
            QHeaderView::section { background: #0f3460; color: #e0e0e0;
                                   border: 1px solid #1a4a80; padding: 4px; }
            QKeySequenceEdit  { background: #0f3460; color: #e0e0e0;
                                border: 1px solid #1a4a80; padding: 3px; }
            QKeySequenceEdit:focus { border-color: #ffd700; }
            QPushButton {
                background: #0f3460; color: #e0e0e0;
                border: 1px solid #1a4a80; padding: 5px 14px;
                border-radius: 5px; font-size: 10pt;
            }
            QPushButton:hover   { background: #1a4a80; }
            QPushButton:default { border-color: #4a9eff; }
            QLabel { color: #bbb; }
        """)
