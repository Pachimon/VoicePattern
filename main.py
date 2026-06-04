"""
VoicePattern — Pitch Accent Trainer
====================================
Load a long audio file, select a region visually, analyze pitch with Praat,
record your voice, and get a contour-match score.

Keyboard shortcuts
------------------
  Space       Play / Stop reference
  P           Play / Stop your recording
  R           Record / Stop recording
  L           Toggle loop
  A           Analyze pitch for current selection
  ← / →       Shift selection left / right by 0.1 s
  Ctrl+← / →  Shift by 1 s
  [ / ]       Shrink / expand selection end by 0.1 s
  Ctrl+O      Open audio file
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_RECENTS_FILE = Path.home() / ".config" / "voicepattern" / "recents.json"
_MAX_RECENTS = 8

from analysis import compare_pitch, extract_pitch
from audio_engine import AudioEngine
from clips_widget import ClipPanel
from pitch_widget import PitchWidget
from settings_manager import load_settings, save_settings
from waveform_widget import WaveformWidget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VoicePattern — Pitch Accent Trainer")
        self.resize(1280, 820)

        self._engine = AudioEngine(self)
        self._engine.playback_finished.connect(self._on_playback_finished)

        self._looping = False
        self._recording = False
        self._playing_recording = False
        self._ref_pitch: tuple | None = None  # (times, freqs)

        self._settings = load_settings()
        self._shortcuts: dict[str, QShortcut] = {}
        self._auto_analyze: bool = self._settings.get("auto_analyze", True)

        self._build_ui()
        self._build_shortcuts()
        self._apply_style()

        # Playback cursor timer — 40 ms ≈ 25 fps
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(40)
        self._cursor_timer.timeout.connect(self._update_cursor)

        # Auto-analyze debounce — fires 400 ms after selection stops moving
        self._analyze_timer = QTimer(self)
        self._analyze_timer.setInterval(400)
        self._analyze_timer.setSingleShot(True)
        self._analyze_timer.timeout.connect(self._analyze_reference)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ---- Toolbar ----
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # Open / Recent files dropdown
        self._open_menu = QMenu()
        self._open_menu.aboutToShow.connect(self._rebuild_open_menu)
        self._load_tool = QToolButton()
        self._load_tool.setText("Open Audio  ▾")
        self._load_tool.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._load_tool.setMenu(self._open_menu)

        self._play_btn = self._btn(f"▶  Play{self._kl('play_stop')}", self._toggle_play, enabled=False)
        self._loop_btn = self._btn(f"↻  Loop: Off{self._kl('loop')}", self._toggle_loop, checkable=True, enabled=False)
        self._analyze_btn = self._btn(f"Analyze Pitch{self._kl('analyze')}", self._analyze_reference, enabled=False)
        self._rec_btn = self._btn(f"🎙  Record{self._kl('record')}", self._toggle_recording, checkable=True, enabled=False)
        self._play_rec_btn = self._btn(f"▶  My Voice{self._kl('play_recording')}", self._toggle_play_recording, enabled=False)
        self._clear_btn = self._btn("✕ Clear", self._clear_recording)
        self._settings_btn = self._btn("⚙ Settings", self._open_settings)

        self._sel_label = QLabel("No file loaded")
        self._sel_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._sel_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        for w in [
            self._load_tool, _sep(),
            self._play_btn, self._loop_btn, _sep(),
            self._analyze_btn, _sep(),
            self._rec_btn, self._play_rec_btn, self._clear_btn, _sep(),
            self._sel_label, _sep(),
            self._settings_btn,
        ]:
            if isinstance(w, QWidget):
                toolbar.addWidget(w)
        root.addLayout(toolbar)

        # ---- Main splitter (waveform top, pitch+subtitle bottom) ----
        vsplit = QSplitter(Qt.Orientation.Vertical)

        self._waveform = WaveformWidget()
        self._waveform.selection_changed.connect(self._on_selection_changed)
        self._waveform.setMinimumHeight(140)
        vsplit.addWidget(self._waveform)

        # ---- Bottom: pitch graph | subtitle panel ----
        hsplit = QSplitter(Qt.Orientation.Horizontal)

        self._pitch_widget = PitchWidget()
        hsplit.addWidget(self._pitch_widget)

        self._clip_panel = ClipPanel()
        self._clip_panel.clip_selected.connect(self._on_clip_selected)
        self._clip_panel.anki_export.connect(self._on_anki_export)
        hsplit.addWidget(self._clip_panel)

        hsplit.setSizes([820, 340])
        vsplit.addWidget(hsplit)
        vsplit.setSizes([220, 520])

        root.addWidget(vsplit)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Load an audio file to begin  (Ctrl+O)")

    def _btn(self, text, slot, *, checkable=False, enabled=True) -> QPushButton:
        b = QPushButton(text)
        b.setCheckable(checkable)
        b.setEnabled(enabled)
        b.clicked.connect(slot)
        return b

    # ------------------------------------------------------------------
    # Keyboard shortcuts  (rebindable)
    # ------------------------------------------------------------------

    def _build_shortcuts(self):
        self._apply_keybindings(self._settings["keybindings"])
        self._waveform.set_draw_modifier(self._settings.get("draw_sel_modifier", "Shift"))
        self._auto_analyze = self._settings.get("auto_analyze", True)

    def _action_map(self) -> dict:
        return {
            "play_stop":      self._toggle_play,
            "play_recording": self._toggle_play_recording,
            "record":         self._toggle_recording,
            "loop":           self._loop_btn.toggle,
            "analyze":        self._analyze_reference,
            "open_file":      self._load_audio_dialog,
            "shift_left_01":  lambda: self._shift_selection(-0.1),
            "shift_right_01": lambda: self._shift_selection(0.1),
            "shift_left_1":   lambda: self._shift_selection(-1.0),
            "shift_right_1":  lambda: self._shift_selection(1.0),
            "shrink_sel":     lambda: self._resize_selection(-0.1),
            "expand_sel":     lambda: self._resize_selection(0.1),
        }

    def _apply_keybindings(self, kb: dict):
        for action, fn in self._action_map().items():
            key_str = kb.get(action, "")
            seq = QKeySequence(key_str)
            if action in self._shortcuts:
                self._shortcuts[action].setKey(seq)
            else:
                sc = QShortcut(seq, self)
                sc.activated.connect(fn)
                self._shortcuts[action] = sc
        self._refresh_btn_labels()

    def _kl(self, action: str) -> str:
        """Return '  [Key]' suffix for button text using the current keybinding."""
        key = self._settings["keybindings"].get(action, "")
        return f"  [{key}]" if key else ""

    def _refresh_btn_labels(self):
        """Update the [Key] hint on every toolbar button to match current keybindings."""
        def _update(btn, action):
            text = re.sub(r"\s*\[.*?\]$", "", btn.text())
            btn.setText(text + self._kl(action))

        _update(self._play_btn,     "play_stop")
        _update(self._play_rec_btn, "play_recording")
        _update(self._rec_btn,      "record")
        _update(self._loop_btn,     "loop")
        _update(self._analyze_btn,  "analyze")

    def _open_settings(self):
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog(
            self._settings["keybindings"],
            self._settings.get("draw_sel_modifier", "Shift"),
            self._settings.get("auto_analyze", True),
            self._settings.get("anki"),
            self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_kb = dlg.get_keybindings()
            new_mod = dlg.get_draw_modifier()
            new_aa = dlg.get_auto_analyze()
            self._settings["keybindings"] = new_kb
            self._settings["draw_sel_modifier"] = new_mod
            self._settings["auto_analyze"] = new_aa
            self._settings["anki"] = dlg.get_anki()
            save_settings(self._settings)
            self._apply_keybindings(new_kb)
            self._waveform.set_draw_modifier(new_mod)
            self._auto_analyze = new_aa
            self.statusBar().showMessage("Settings saved.")

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------

    def _apply_style(self):
        btn = """
            background: #0f3460;
            color: #e0e0e0;
            border: 1px solid #1a4a80;
            padding: 5px 14px;
            border-radius: 5px;
            font-size: 10pt;
        """
        self.setStyleSheet(f"""
            QMainWindow, QWidget  {{ background: #16213e; color: #e0e0e0; }}
            QPushButton           {{ {btn} }}
            QToolButton           {{ {btn} }}
            QPushButton:hover, QToolButton:hover   {{ background: #1a4a80; }}
            QPushButton:checked {{ background: #c0392b; border-color: #e74c3c; color: #fff; }}
            QPushButton:disabled, QToolButton:disabled {{ color: #555; border-color: #333; background: #111; }}
            QMenu {{
                background: #0f3460; color: #e0e0e0;
                border: 1px solid #1a4a80;
            }}
            QMenu::item:selected {{ background: #1a4a80; }}
            QMenu::separator {{ height: 1px; background: #1a4a80; margin: 3px 8px; }}
            QPlainTextEdit {{
                background: #0d1b2a; color: #cce;
                border: 1px solid #1a4a80;
                font-size: 11pt; padding: 6px;
            }}
            QListWidget {{
                background: #0d1b2a; color: #e0e0e0;
                border: 1px solid #1a4a80;
                alternate-background-color: #0f2235;
                font-size: 9pt;
            }}
            QListWidget::item {{ padding: 5px 6px; }}
            QListWidget::item:selected {{ background: #1a4a80; color: #fff; }}
            QListWidget::item:hover {{ background: #0f3460; }}
            QLabel     {{ color: #bbb; }}
            QSplitter::handle {{ background: #1a4a80; }}
            QStatusBar {{ background: #0d1b2a; color: #888; font-size: 9pt; }}
        """)

    # ------------------------------------------------------------------
    # File loading + recent files
    # ------------------------------------------------------------------

    def _rebuild_open_menu(self):
        self._open_menu.clear()
        open_act = QAction("Open New File…  Ctrl+O", self)
        open_act.triggered.connect(self._load_audio_dialog)
        self._open_menu.addAction(open_act)

        recents = self._get_recents()
        if recents:
            self._open_menu.addSeparator()
            for p in recents:
                label = Path(p).name
                act = QAction(label, self)
                act.setToolTip(p)
                act.triggered.connect(lambda _checked, path=p: self._load_audio_path(path))
                self._open_menu.addAction(act)

    def _get_recents(self) -> list[str]:
        try:
            if _RECENTS_FILE.exists():
                return json.loads(_RECENTS_FILE.read_text())
        except Exception:
            pass
        return []

    def _add_recent(self, path: str):
        recents = [p for p in self._get_recents() if p != path]
        recents.insert(0, path)
        try:
            _RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            _RECENTS_FILE.write_text(json.dumps(recents[:_MAX_RECENTS], indent=2))
        except Exception:
            pass

    def _load_audio_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Audio File",
            "",
            "Audio Files (*.wav *.mp3 *.flac *.ogg *.m4a *.aiff);;All Files (*)",
        )
        if path:
            self._load_audio_path(path)

    def _load_audio_path(self, path: str):
        self.statusBar().showMessage(f"Loading {path} …")
        QApplication.processEvents()

        try:
            audio = self._engine.load_file(path)
        except Exception as exc:
            self.statusBar().showMessage(f"Error loading file: {exc}")
            return

        self._add_recent(path)
        self._waveform.load_waveform(audio.data, audio.sample_rate)
        self._clip_panel.load_for_audio(path)

        for btn in [self._play_btn, self._loop_btn, self._analyze_btn, self._rec_btn]:
            btn.setEnabled(True)

        name = Path(path).name
        self.statusBar().showMessage(
            f"{name}  ·  {audio.duration:.1f} s  ·  {audio.sample_rate} Hz  ·  "
            f"{audio.channels} ch"
        )

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------

    def _toggle_play(self):
        if not self._engine.audio:
            return
        if self._play_btn.text().startswith("▶"):
            start, end = self._waveform.get_selection()
            self._play_btn.setText(f"■  Stop{self._kl('play_stop')}")
            self._engine.play_region(start, end, loop=self._looping)
            self._cursor_timer.start()
        else:
            self._stop_playback()

    def _stop_playback(self):
        self._engine.stop()
        self._play_btn.setText(f"▶  Play{self._kl('play_stop')}")
        self._play_rec_btn.setText(f"▶  My Voice{self._kl('play_recording')}")
        self._playing_recording = False
        self._cursor_timer.stop()
        self._waveform.set_cursor(None)
        self._pitch_widget.set_cursor(None)

    def _toggle_play_recording(self):
        if not self._engine.has_recording:
            return
        if self._play_rec_btn.text().startswith("▶"):
            # Stop reference playback if running
            self._engine.stop()
            self._play_btn.setText(f"▶  Play{self._kl('play_stop')}")
            self._cursor_timer.stop()
            self._waveform.set_cursor(None)
            # Start recording playback
            self._play_rec_btn.setText(f"■  Stop{self._kl('play_recording')}")
            self._playing_recording = True
            self._engine.play_last_recording()
            self._cursor_timer.start()
        else:
            self._engine.stop()
            self._play_rec_btn.setText(f"▶  My Voice{self._kl('play_recording')}")
            self._playing_recording = False
            self._pitch_widget.set_cursor(None)

    def _on_playback_finished(self):
        self._play_btn.setText(f"▶  Play{self._kl('play_stop')}")
        self._play_rec_btn.setText(f"▶  My Voice{self._kl('play_recording')}")
        self._playing_recording = False
        self._cursor_timer.stop()
        self._waveform.set_cursor(None)
        self._pitch_widget.set_cursor(None)

    def _toggle_loop(self, checked: bool):
        self._looping = checked
        self._loop_btn.setText(f"↻  Loop: {'On' if checked else 'Off'}{self._kl('loop')}")

    def _update_cursor(self):
        pos = self._engine.get_play_position()

        if pos is None:
            self._waveform.set_cursor(None)
            self._pitch_widget.set_cursor(None)
            return

        if self._playing_recording:
            # Recording plays from t=0; pitch graph shows it offset by time_offset.
            # Don't show waveform cursor (pos=0..rec_duration is meaningless on the file).
            self._waveform.set_cursor(None)
            self._pitch_widget.set_cursor(pos + self._pitch_widget.time_offset)
        else:
            # Reference playback: waveform cursor is absolute file position.
            # Pitch graph is 0-based (starts at selection start), so subtract it.
            self._waveform.set_cursor(pos)
            start, _ = self._waveform.get_selection()
            self._pitch_widget.set_cursor(pos - start)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self, start: float, end: float):
        self._sel_label.setText(f"  {start:.3f} s  —  {end:.3f} s  ({end - start:.3f} s)")
        self._clip_panel.update_selection(start, end)
        if self._auto_analyze and self._engine.audio:
            self._analyze_timer.start()  # restarts the 400 ms countdown

    def _on_clip_selected(self, start: float, end: float):
        """Jump the waveform selection to a saved clip."""
        self._waveform.set_selection(start, end)

    def _render_pitch_graph(self, start: float, end: float) -> bytes | None:
        """
        Render just the pitch contour line — transparent background, no axes,
        no grid, no labels, no score — and return PNG bytes.
        """
        import os, tempfile
        import pyqtgraph as pg
        from pyqtgraph.exporters import ImageExporter

        mono, sr = self._engine.get_mono_region(start, end)
        if len(mono) == 0:
            return None
        try:
            times, freqs = extract_pitch(mono, sr)
        except Exception:
            return None

        # Off-screen plot — strip everything except the curve
        pw = pg.PlotWidget()
        pi = pw.getPlotItem()
        pi.hideAxis("left")
        pi.hideAxis("bottom")
        pi.hideAxis("right")
        pi.hideAxis("top")
        pi.hideButtons()
        pi.setMenuEnabled(False)
        pw.setBackground(None)   # transparent

        pw.addItem(pg.PlotDataItem(
            times, freqs,
            pen=pg.mkPen("#4a9eff", width=3),
            symbol="o", symbolSize=4,
            symbolBrush="#4a9eff", symbolPen=None,
            connect="finite",
        ))

        exporter = ImageExporter(pi)
        exporter.parameters()["width"]  = 800
        exporter.parameters()["height"] = 250
        exporter.parameters()["background"] = pg.mkColor(0, 0, 0, 0)  # transparent

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        try:
            exporter.export(fileName=tmp.name)
            with open(tmp.name, "rb") as fh:
                return fh.read()
        except Exception:
            return None
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _on_anki_export(self, clips: list):
        """Export one or more clips to Anki via AnkiConnect."""
        import io
        import soundfile as sf
        import anki_client

        anki = self._settings.get("anki", {})
        url           = anki.get("url", "http://localhost:8765")
        deck          = anki.get("deck", "")
        model         = anki.get("model", "")
        f_txt         = anki.get("field_text", "")
        f_aud         = anki.get("field_audio", "")
        f_graph       = anki.get("field_graph", "")
        include_graph = anki.get("include_graph", True)

        if not deck or not model or not f_txt or not f_aud:
            self.statusBar().showMessage(
                "Anki not fully configured — open Settings → Anki and fill in all fields."
            )
            return
        if not self._engine.audio:
            self.statusBar().showMessage("No audio file loaded.")
            return

        audio_stem = Path(self._engine.audio.path).stem
        ok = err = 0

        for clip in clips:
            try:
                base = f"vp_{audio_stem}_{int(clip['start']*1000)}_{int(clip['end']*1000)}"

                # ── Audio ──────────────────────────────────────────
                data, sr = self._engine.get_mono_region(clip["start"], clip["end"])
                if len(data) == 0:
                    err += 1
                    continue
                buf = io.BytesIO()
                sf.write(buf, data.astype("float32"), sr, format="WAV", subtype="PCM_16")
                wav_name = base + ".wav"
                anki_client.store_media_file(url, wav_name, buf.getvalue())

                # ── Pitch graph image ──────────────────────────────
                fields = {
                    f_txt: clip["subtitle"],
                    f_aud: f"[sound:{wav_name}]",
                }
                if include_graph and f_graph:
                    png_bytes = self._render_pitch_graph(clip["start"], clip["end"])
                    if png_bytes:
                        png_name = base + ".png"
                        anki_client.store_media_file(url, png_name, png_bytes)
                        img_tag = f'<img src="{png_name}">'
                        if f_graph == f_aud:
                            # Same field — append image after the sound tag
                            fields[f_aud] += img_tag
                        else:
                            fields[f_graph] = img_tag

                # ── Add note ───────────────────────────────────────
                anki_client.add_note(url, deck, model, fields)
                ok += 1

            except Exception as e:
                err += 1
                self.statusBar().showMessage(f"Anki export error: {e}")

        if ok:
            word = "clip" if ok == 1 else "clips"
            msg = f"Sent {ok} {word} to Anki deck '{deck}'."
            if err:
                msg += f"  ({err} failed)"
            self.statusBar().showMessage(msg)

    def _shift_selection(self, delta: float):
        if not self._engine.audio:
            return
        s, e = self._waveform.get_selection()
        dur = self._engine.audio.duration
        width = e - s
        new_s = max(0.0, min(s + delta, dur - width))
        self._waveform.set_selection(new_s, new_s + width)

    def _resize_selection(self, delta: float):
        if not self._engine.audio:
            return
        s, e = self._waveform.get_selection()
        dur = self._engine.audio.duration
        new_e = max(s + 0.1, min(e + delta, dur))
        self._waveform.set_selection(s, new_e)

    # ------------------------------------------------------------------
    # Pitch analysis
    # ------------------------------------------------------------------

    def _analyze_reference(self):
        if not self._engine.audio:
            return
        start, end = self._waveform.get_selection()
        duration = end - start
        if duration < 0.1:
            self.statusBar().showMessage("Selection too short for pitch analysis.")
            return

        self.statusBar().showMessage("Analyzing pitch …")
        QApplication.processEvents()

        mono, sr = self._engine.get_mono_region(start, end)
        if len(mono) == 0:
            return

        try:
            times, freqs = extract_pitch(mono, sr)
            self._ref_pitch = (times, freqs)
            self._pitch_widget.set_reference_pitch(times, freqs)
            voiced = np.sum(~np.isnan(freqs))
            self.statusBar().showMessage(
                f"Pitch analyzed  [{start:.2f} s – {end:.2f} s]  "
                f"voiced frames: {voiced}/{len(freqs)}  "
                f"·  Now record your voice  [R]"
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Pitch analysis error: {exc}")

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _toggle_recording(self):
        if not self._recording:
            self._recording = True
            self._rec_btn.setText(f"⏹  Stop{self._kl('record')}")
            self._engine.start_recording()
            self.statusBar().showMessage("Recording …  press R or click to stop.")
        else:
            self._recording = False
            self._rec_btn.setText(f"🎙  Record{self._kl('record')}")
            self._rec_btn.setChecked(False)
            data, sr = self._engine.stop_recording()

            if data is None or len(data) == 0:
                self.statusBar().showMessage("No audio captured.")
                return

            self.statusBar().showMessage("Analyzing your recording …")
            QApplication.processEvents()

            mono = data[:, 0] if data.ndim > 1 else data
            try:
                times, freqs = extract_pitch(mono.astype(np.float64), sr)
                score_info: dict = {}
                if self._ref_pitch is not None:
                    score_info = compare_pitch(
                        self._ref_pitch[0],
                        self._ref_pitch[1],
                        times,
                        freqs,
                    )
                self._pitch_widget.set_recording_pitch(times, freqs, score_info)
                self._play_rec_btn.setEnabled(True)
                msg = "Recording analyzed.  Drag the orange handle on the pitch graph to align.  [P] plays it back."
                if score_info:
                    msg += (
                        f"  Score: {score_info['score']}/100"
                        f"  RMSE: {score_info['rmse']} semitones"
                        f"  Correlation: {score_info['correlation']}"
                    )
                self.statusBar().showMessage(msg)
            except Exception as exc:
                self.statusBar().showMessage(f"Recording analysis error: {exc}")

    def _clear_recording(self):
        self._pitch_widget.clear_recording()
        self._play_rec_btn.setEnabled(False)
        self._play_rec_btn.setText(f"▶  My Voice{self._kl('play_recording')}")
        self.statusBar().showMessage("Recording cleared.")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _sep() -> QFrame:
    """Thin vertical separator for the toolbar."""
    f = QFrame()
    f.setFrameShape(QFrame.Shape.VLine)
    f.setStyleSheet("color: #1a4a80;")
    f.setFixedWidth(2)
    return f


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("VoicePattern")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
