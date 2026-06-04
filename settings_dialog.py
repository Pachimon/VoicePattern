from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from settings_manager import ACTION_LABELS, DEFAULT_ANKI, DEFAULT_KEYBINDINGS


class SettingsDialog(QDialog):
    def __init__(
        self,
        current_keybindings: dict[str, str],
        draw_sel_modifier: str = "Shift",
        auto_analyze: bool = True,
        anki: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings — VoicePattern")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)

        self._kb = dict(current_keybindings)
        self._editors: dict[str, QKeySequenceEdit] = {}
        anki = dict(anki or DEFAULT_ANKI)

        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Tabs ──────────────────────────────────────────────────────
        tabs = QTabWidget()
        root.addWidget(tabs)

        # ── Tab 1 : Shortcuts ─────────────────────────────────────────
        sc_widget = QWidget()
        sc_layout = QVBoxLayout(sc_widget)
        sc_layout.setSpacing(8)

        hint = QLabel("Click a Key Binding cell, then press the desired key combination.")
        hint.setStyleSheet("color: #aaa; font-size: 9pt;")
        sc_layout.addWidget(hint)

        self._table = QTableWidget(len(ACTION_LABELS), 2)
        self._table.setHorizontalHeaderLabels(["Action", "Key Binding"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 190)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, (action, label) in enumerate(ACTION_LABELS.items()):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, item)
            editor = QKeySequenceEdit(
                QKeySequence(self._kb.get(action, DEFAULT_KEYBINDINGS.get(action, "")))
            )
            editor.keySequenceChanged.connect(lambda seq, a=action: self._on_kb_changed(a, seq))
            self._table.setCellWidget(row, 1, editor)
            self._editors[action] = editor
        sc_layout.addWidget(self._table)

        self._conflict_label = QLabel("")
        self._conflict_label.setStyleSheet("color: #ff5252; font-size: 9pt;")
        sc_layout.addWidget(self._conflict_label)

        # Mouse / behaviour options row
        opt_row = QHBoxLayout()
        opt_row.setSpacing(16)
        mod_label = QLabel("Draw selection modifier:")
        mod_label.setStyleSheet("color: #bbb; font-size: 9pt;")
        self._mod_combo = QComboBox()
        self._mod_combo.addItems(["Shift", "Ctrl", "Alt"])
        self._mod_combo.setCurrentIndex(max(0, self._mod_combo.findText(draw_sel_modifier)))
        self._auto_check = QCheckBox("Auto-analyze pitch on selection change")
        self._auto_check.setChecked(auto_analyze)
        self._auto_check.setStyleSheet("color: #bbb; font-size: 9pt;")
        opt_row.addWidget(mod_label)
        opt_row.addWidget(self._mod_combo)
        opt_row.addSpacing(20)
        opt_row.addWidget(self._auto_check)
        opt_row.addStretch()
        sc_layout.addLayout(opt_row)

        tabs.addTab(sc_widget, "Shortcuts")

        # ── Tab 2 : Anki ──────────────────────────────────────────────
        anki_widget = QWidget()
        anki_layout = QVBoxLayout(anki_widget)
        anki_layout.setSpacing(10)

        # URL + Test
        url_form = QFormLayout()
        url_form.setSpacing(8)
        url_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        url_row = QHBoxLayout()
        self._anki_url = QLineEdit(anki.get("url", DEFAULT_ANKI["url"]))
        self._anki_url.setPlaceholderText("http://localhost:8765")
        self._test_btn = QPushButton("Test")
        self._test_btn.setFixedWidth(54)
        self._test_btn.clicked.connect(self._test_anki)
        url_row.addWidget(self._anki_url)
        url_row.addWidget(self._test_btn)
        url_form.addRow("AnkiConnect URL:", url_row)

        self._anki_status = QLabel("Click Test to connect and populate dropdowns.")
        self._anki_status.setStyleSheet("color: #aaa; font-size: 9pt;")
        url_form.addRow("", self._anki_status)
        anki_layout.addLayout(url_form)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #1a4a80;")
        anki_layout.addWidget(sep)

        # Deck + model dropdowns
        card_form = QFormLayout()
        card_form.setSpacing(8)
        card_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._anki_deck = QComboBox()
        self._anki_deck.setEditable(True)
        self._anki_deck.setCurrentText(anki.get("deck", DEFAULT_ANKI["deck"]))

        self._anki_model = QComboBox()
        self._anki_model.setEditable(True)
        self._anki_model.setCurrentText(anki.get("model", DEFAULT_ANKI["model"]))
        self._anki_model.currentTextChanged.connect(self._on_model_changed)

        card_form.addRow("Deck:", self._anki_deck)
        card_form.addRow("Note type (model):", self._anki_model)
        anki_layout.addLayout(card_form)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #1a4a80;")
        anki_layout.addWidget(sep2)

        # Field mapping dropdowns
        field_form = QFormLayout()
        field_form.setSpacing(8)
        field_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._anki_f_txt = QComboBox()
        self._anki_f_txt.setEditable(True)
        self._anki_f_txt.setCurrentText(anki.get("field_text", DEFAULT_ANKI["field_text"]))

        self._anki_f_aud = QComboBox()
        self._anki_f_aud.setEditable(True)
        self._anki_f_aud.setCurrentText(anki.get("field_audio", DEFAULT_ANKI["field_audio"]))

        self._anki_f_graph = QComboBox()
        self._anki_f_graph.setEditable(True)
        self._anki_f_graph.setCurrentText(anki.get("field_graph", DEFAULT_ANKI["field_graph"]))

        field_form.addRow("Subtitle text → field:", self._anki_f_txt)
        field_form.addRow("Audio clip → field:", self._anki_f_aud)
        field_form.addRow("Pitch graph → field:", self._anki_f_graph)
        anki_layout.addLayout(field_form)

        # Graph option
        self._include_graph = QCheckBox("Include pitch graph image in card")
        self._include_graph.setChecked(bool(anki.get("include_graph", True)))
        anki_layout.addWidget(self._include_graph)

        hint2 = QLabel(
            "Field names must match the note type exactly.\n"
            "Audio is stored as  [sound:file.wav]  and the graph as  <img src='file.png'>."
        )
        hint2.setStyleSheet("color: #666; font-size: 9pt;")
        anki_layout.addWidget(hint2)
        anki_layout.addStretch()

        tabs.addTab(anki_widget, "Anki")

        # ── Buttons (outside tabs) ────────────────────────────────────
        btn_row = QHBoxLayout()
        self._reset_btn = QPushButton("Reset Defaults")
        self._reset_btn.clicked.connect(self._reset_defaults)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        root.addLayout(btn_row)

        self._apply_style()

    # ── Getters ───────────────────────────────────────────────────────

    def get_keybindings(self) -> dict[str, str]:
        for action, editor in self._editors.items():
            self._kb[action] = editor.keySequence().toString()
        return dict(self._kb)

    def get_draw_modifier(self) -> str:
        return self._mod_combo.currentText()

    def get_auto_analyze(self) -> bool:
        return self._auto_check.isChecked()

    def get_anki(self) -> dict:
        return {
            "url":           self._anki_url.text().strip(),
            "deck":          self._anki_deck.currentText().strip(),
            "model":         self._anki_model.currentText().strip(),
            "field_text":    self._anki_f_txt.currentText().strip(),
            "field_audio":   self._anki_f_aud.currentText().strip(),
            "field_graph":   self._anki_f_graph.currentText().strip(),
            "include_graph": self._include_graph.isChecked(),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _on_kb_changed(self, action: str, seq: QKeySequence):
        self._kb[action] = seq.toString()
        self._check_conflicts()

    def _check_conflicts(self):
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
        self._conflict_label.setText("⚠ " + " | ".join(conflicts) if conflicts else "")

    def _reset_defaults(self):
        for action, key in DEFAULT_KEYBINDINGS.items():
            if action in self._editors:
                self._editors[action].setKeySequence(QKeySequence(key))
                self._kb[action] = key
        self._mod_combo.setCurrentIndex(0)
        self._auto_check.setChecked(True)
        self._conflict_label.setText("")
        self._anki_url.setText(DEFAULT_ANKI["url"])
        self._anki_deck.setCurrentText(DEFAULT_ANKI["deck"])
        self._anki_model.setCurrentText(DEFAULT_ANKI["model"])
        self._anki_f_txt.setCurrentText(DEFAULT_ANKI["field_text"])
        self._anki_f_aud.setCurrentText(DEFAULT_ANKI["field_audio"])
        self._anki_f_graph.setCurrentText(DEFAULT_ANKI["field_graph"])
        self._include_graph.setChecked(True)
        self._anki_status.setText("Click Test to connect and populate dropdowns.")
        self._anki_status.setStyleSheet("color: #aaa; font-size: 9pt;")

    def _test_anki(self):
        import anki_client
        url = self._anki_url.text().strip()
        self._anki_status.setText("Connecting…")
        self._anki_status.setStyleSheet("color: #aaa; font-size: 9pt;")
        try:
            msg = anki_client.test_connection(url)
            self._anki_status.setText(msg + "  — populating dropdowns…")
            self._anki_status.setStyleSheet("color: #00e676; font-size: 9pt;")
            self._populate_decks_models(url)
            self._anki_status.setText(msg)
        except Exception as e:
            self._anki_status.setText(f"Failed: {e}")
            self._anki_status.setStyleSheet("color: #ff5252; font-size: 9pt;")

    def _populate_decks_models(self, url: str):
        """Fetch deck and model lists from AnkiConnect and populate combos."""
        import anki_client
        try:
            cur = self._anki_deck.currentText()
            decks = anki_client.get_deck_names(url)
            self._anki_deck.blockSignals(True)
            self._anki_deck.clear()
            self._anki_deck.addItems(decks)
            self._anki_deck.setCurrentText(cur if cur in decks else (decks[0] if decks else cur))
            self._anki_deck.blockSignals(False)
        except Exception:
            pass
        try:
            cur = self._anki_model.currentText()
            models = anki_client.get_model_names(url)
            self._anki_model.blockSignals(True)
            self._anki_model.clear()
            self._anki_model.addItems(models)
            self._anki_model.setCurrentText(cur if cur in models else (models[0] if models else cur))
            self._anki_model.blockSignals(False)
        except Exception:
            pass
        self._fetch_fields()

    def _on_model_changed(self, _: str):
        self._fetch_fields()

    def _fetch_fields(self):
        """Populate field combos from the currently selected model."""
        import anki_client
        url   = self._anki_url.text().strip()
        model = self._anki_model.currentText().strip()
        if not url or not model:
            return
        try:
            fields = anki_client.get_model_fields(url, model)
        except Exception:
            return
        if not fields:
            return

        def _repopulate(combo: QComboBox):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(fields)
            combo.setCurrentText(cur if cur in fields else fields[0])
            combo.blockSignals(False)

        _repopulate(self._anki_f_txt)
        _repopulate(self._anki_f_aud)
        _repopulate(self._anki_f_graph)

    def _apply_style(self):
        self.setStyleSheet("""
            QDialog, QWidget  { background: #16213e; color: #e0e0e0; }
            QTabWidget::pane  { border: 1px solid #1a4a80; }
            QTabBar::tab      { background: #0f3460; color: #aaa;
                                padding: 6px 18px; border: 1px solid #1a4a80;
                                border-bottom: none; border-radius: 3px 3px 0 0; }
            QTabBar::tab:selected { background: #16213e; color: #e0e0e0; }
            QTableWidget      { background: #0d1b2a; gridline-color: #1a4a80;
                                border: 1px solid #1a4a80; }
            QHeaderView::section { background: #0f3460; color: #e0e0e0;
                                   border: 1px solid #1a4a80; padding: 4px; }
            QKeySequenceEdit  { background: #0f3460; color: #e0e0e0;
                                border: 1px solid #1a4a80; padding: 3px; }
            QKeySequenceEdit:focus { border-color: #ffd700; }
            QLineEdit         { background: #0f3460; color: #e0e0e0;
                                border: 1px solid #1a4a80; padding: 4px 6px;
                                border-radius: 3px; }
            QLineEdit:focus   { border-color: #ffd700; }
            QComboBox         { background: #0f3460; color: #e0e0e0;
                                border: 1px solid #1a4a80; padding: 3px 8px;
                                border-radius: 3px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #0f3460; color: #e0e0e0;
                                border: 1px solid #1a4a80;
                                selection-background-color: #1a4a80; }
            QPushButton { background: #0f3460; color: #e0e0e0;
                          border: 1px solid #1a4a80; padding: 5px 14px;
                          border-radius: 5px; font-size: 10pt; }
            QPushButton:hover   { background: #1a4a80; }
            QPushButton:default { border-color: #4a9eff; }
            QLabel { color: #bbb; }
            QCheckBox { color: #bbb; }
            QCheckBox::indicator { width: 14px; height: 14px; }
        """)
