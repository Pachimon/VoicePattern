from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from pyqtgraph import Point
from pyqtgraph.Qt import QtCore as _pgQtCore

from analysis import compare_pitch


class _WideHitLine(pg.InfiniteLine):
    """
    InfiniteLine with a wide invisible mouse target while keeping the
    visual line thin.  pyqtgraph sizes the hit area from pen widths;
    we override _computeBoundingRect to use a fixed pixel half-width
    instead, so the click zone is always 10 px on each side regardless
    of how thin the drawn line is.
    """

    _HIT_HALF_PX = 22

    def _computeBoundingRect(self):
        vr = self.viewRect()
        if vr is None:
            return _pgQtCore.QRectF()
        _, ortho = self.pixelVectors(direction=Point(1, 0))
        px = 0 if ortho is None else ortho.y()
        w = (self._maxMarkerSize + self._HIT_HALF_PX + 1) * px
        br = _pgQtCore.QRectF(vr)
        br.setBottom(-w)
        br.setTop(w)
        length = br.width()
        left = br.left() + length * self.span[0]
        right = br.left() + length * self.span[1]
        br.setLeft(left)
        br.setRight(right)
        br = br.normalized()
        vs = self.getViewBox().size()
        if self._bounds != br or self._lastViewSize != vs:
            self._bounds = br
            self._lastViewSize = vs
            self.prepareGeometryChange()
        self._endPoints = (left, right)
        self._lastViewRect = vr
        return self._bounds


class PitchWidget(QWidget):
    """
    Pitch contour display.

    Blue curve  = reference audio.
    Orange curve = your recorded voice.

    Two drag handles on the recording curve:
      ↔  Vertical orange dashed line  — drag left/right to shift in time
      ↕  Horizontal orange dashed line — drag up/down to shift in Hz

    Both handles update the display live; score recalculates on release.
    Score is always voice-register independent (normalised by each curve's
    geometric mean), so the Hz shift is purely for visual alignment.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Info / score bar
        info_row = QHBoxLayout()
        self._ref_label = QLabel("Reference: —")
        self._ref_label.setStyleSheet("color: #4a9eff; font-weight: bold;")
        self._rec_label = QLabel("Recording: —")
        self._rec_label.setStyleSheet("color: #ff8c42; font-weight: bold;")
        self._offset_label = QLabel("")
        self._offset_label.setStyleSheet("color: #aaa; font-size: 9pt;")
        self._score_label = QLabel("Score: —")
        self._score_label.setStyleSheet(
            "color: #ffd700; font-size: 14pt; font-weight: bold;"
        )
        info_row.addWidget(self._ref_label)
        info_row.addWidget(self._rec_label)
        info_row.addWidget(self._offset_label)
        info_row.addStretch()
        info_row.addWidget(self._score_label)
        layout.addLayout(info_row)

        # Plot
        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1a1a2e")
        self._plot.setLabel("left", "Pitch", units="Hz")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        layout.addWidget(self._plot)

        # Reference pitch curve (blue)
        self._ref_curve = pg.PlotDataItem(
            pen=pg.mkPen("#4a9eff", width=2.5),
            symbol="o",
            symbolSize=4,
            symbolBrush="#4a9eff",
            symbolPen=None,
            connect="finite",
        )
        # Recording pitch curve (orange)
        self._rec_curve = pg.PlotDataItem(
            pen=pg.mkPen("#ff8c42", width=2.5),
            symbol="o",
            symbolSize=4,
            symbolBrush="#ff8c42",
            symbolPen=None,
            connect="finite",
        )
        self._plot.addItem(self._ref_curve)
        self._plot.addItem(self._rec_curve)

        # Orange dashed — same width in both states, colour shifts to gold on hover.
        # _WideHitLine keeps the click/drag target ~10 px wide on each side.
        _handle_pen = pg.mkPen("#ff8c42", width=2, style=Qt.PenStyle.DashLine)
        _handle_hover_pen = pg.mkPen("#ffd700", width=2, style=Qt.PenStyle.DashLine)

        # Time handle — vertical dashed line, drag left/right
        self._time_handle = _WideHitLine(
            pos=0.0,
            angle=90,
            movable=True,
            pen=_handle_pen,
        )
        self._time_handle.setHoverPen(_handle_hover_pen)
        self._time_handle.setVisible(False)
        self._time_handle.sigPositionChanged.connect(self._on_time_dragging)
        self._time_handle.sigPositionChangeFinished.connect(
            self._on_any_handle_released
        )
        self._plot.addItem(self._time_handle)

        # Hz handle — horizontal dashed line, drag up/down
        self._hz_handle = _WideHitLine(
            pos=200.0,
            angle=0,
            movable=True,
            pen=_handle_pen,
        )
        self._hz_handle.setHoverPen(_handle_hover_pen)
        self._hz_handle.setVisible(False)
        self._hz_handle.sigPositionChanged.connect(self._on_hz_dragging)
        self._hz_handle.sigPositionChangeFinished.connect(self._on_any_handle_released)
        self._plot.addItem(self._hz_handle)

        # Playback cursor — moves with whatever is currently playing
        self._cursor = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            movable=False,
            pen=pg.mkPen("#ff4444", width=2, style=Qt.PenStyle.DashLine),
        )
        self._cursor.setVisible(False)
        self._plot.addItem(self._cursor)

        legend = pg.LegendItem(offset=(10, 10))
        legend.setParentItem(self._plot.graphicsItem())
        legend.addItem(self._ref_curve, "Reference")
        legend.addItem(self._rec_curve, "Your Voice  (drag ↔↕ handles to align)")

        # Raw data and offsets
        self._ref_times: Optional[np.ndarray] = None
        self._ref_freqs: Optional[np.ndarray] = None
        self._rec_times_raw: Optional[np.ndarray] = None
        self._rec_freqs_raw: Optional[np.ndarray] = None
        self._hz_handle_base: float = 200.0  # geometric mean of recording at load time
        self._hz_offset: float = 0.0  # current vertical offset in Hz
        self._time_offset: float = 0.0  # current horizontal offset in seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def time_offset(self) -> float:
        """Current horizontal offset of the recording curve in seconds."""
        return self._time_offset

    def set_cursor(self, pos_sec: Optional[float]):
        """Show/hide the playback cursor at `pos_sec` on the plot's time axis."""
        if pos_sec is None:
            self._cursor.setVisible(False)
        else:
            self._cursor.setPos(pos_sec)
            self._cursor.setVisible(True)

    def set_reference_pitch(self, times: np.ndarray, freqs: np.ndarray):
        self._ref_times = times
        self._ref_freqs = freqs
        self._ref_curve.setData(times, freqs)

        voiced = freqs[~np.isnan(freqs)]
        if len(voiced):
            self._ref_label.setText(
                f"Reference: {np.nanmin(voiced):.0f}–{np.nanmax(voiced):.0f} Hz"
            )
        else:
            self._ref_label.setText("Reference: no pitch detected")

        self._rec_curve.setData([], [])
        self._time_handle.setVisible(False)
        self._hz_handle.setVisible(False)
        self._score_label.setText("Score: —")
        self._rec_label.setText("Recording: —")
        self._offset_label.setText("")

    def set_recording_pitch(
        self,
        times: np.ndarray,
        freqs: np.ndarray,
        score_info: dict,
    ):
        self._rec_times_raw = times
        self._rec_freqs_raw = freqs
        self._time_offset = 0.0
        self._hz_offset = 0.0

        # Place time handle at 0
        self._time_handle.setValue(0.0)
        self._time_handle.setVisible(True)

        # Place Hz handle at the geometric mean of the recording's voiced frames
        voiced_hz = freqs[~np.isnan(freqs) & (freqs > 0)]
        if len(voiced_hz):
            self._hz_handle_base = float(np.exp(np.mean(np.log(voiced_hz))))
        else:
            self._hz_handle_base = 200.0
        self._hz_handle.setValue(self._hz_handle_base)
        self._hz_handle.setVisible(True)

        self._apply_offsets(0.0, 0.0)

        if len(voiced_hz):
            self._rec_label.setText(
                f"Recording: {voiced_hz.min():.0f}–{voiced_hz.max():.0f} Hz"
            )
        self._update_score_display(score_info)

    def clear_recording(self):
        self._rec_times_raw = None
        self._rec_freqs_raw = None
        self._time_offset = 0.0
        self._hz_offset = 0.0
        self._rec_curve.setData([], [])
        self._time_handle.setVisible(False)
        self._hz_handle.setVisible(False)
        self._cursor.setVisible(False)
        self._rec_label.setText("Recording: —")
        self._score_label.setText("Score: —")
        self._offset_label.setText("")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _apply_offsets(self, time_offset: float, hz_offset: float):
        if self._rec_times_raw is None or len(self._rec_times_raw) == 0:
            return
        t = self._rec_times_raw - self._rec_times_raw[0] + time_offset
        # Shift Hz — NaN stays NaN; clip anything that drops below 50 Hz
        f = np.where(
            np.isnan(self._rec_freqs_raw),
            np.nan,
            np.maximum(50.0, self._rec_freqs_raw + hz_offset),
        )
        self._rec_curve.setData(t, f)

    def _on_time_dragging(self):
        self._time_offset = float(self._time_handle.value())
        self._apply_offsets(self._time_offset, self._hz_offset)
        self._update_offset_label()

    def _on_hz_dragging(self):
        self._hz_offset = float(self._hz_handle.value()) - self._hz_handle_base
        self._apply_offsets(self._time_offset, self._hz_offset)
        self._update_offset_label()

    def _update_offset_label(self):
        parts = []
        if abs(self._time_offset) > 0.001:
            parts.append(f"time: {self._time_offset:+.3f} s")
        if abs(self._hz_offset) > 0.5:
            parts.append(f"Hz: {self._hz_offset:+.0f}")
        self._offset_label.setText("  ".join(parts))

    def _on_any_handle_released(self):
        """Recalculate score when either handle is released."""
        if (
            self._ref_times is None
            or self._ref_freqs is None
            or self._rec_times_raw is None
            or self._rec_freqs_raw is None
        ):
            return
        # Score uses the time offset only — Hz offset is purely visual because
        # scoring normalises each curve by its own geometric mean.
        score_info = compare_pitch(
            self._ref_times,
            self._ref_freqs,
            self._rec_times_raw,
            self._rec_freqs_raw,
            rec_offset=self._time_offset,
        )
        self._update_score_display(score_info)

    def _update_score_display(self, score_info: dict):
        if not score_info:
            self._score_label.setText("Score: —")
            return
        score = score_info.get("score", 0)
        corr = score_info.get("correlation", 0)
        color = "#00e676" if score >= 70 else ("#ffd700" if score >= 40 else "#ff5252")
        self._score_label.setText(
            f"<span style='color:{color}'>{score}</span>/100 &nbsp; corr: {corr:.2f}"
        )
