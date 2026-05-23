from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QWidget, QVBoxLayout


class WaveformWidget(QWidget):
    """
    Scrollable waveform display with a draggable selection region.

    Interaction
    -----------
    - Drag region handles  → resize selection
    - Drag region body     → move selection
    - Click outside region → snap region center to that position (width preserved)
    - Scroll wheel         → zoom in / out
    - Keyboard shortcuts handled in main.py
    """

    selection_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("#1a1a2e")
        self._plot.getPlotItem().hideAxis("left")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setMenuEnabled(False)
        self._plot.setMouseEnabled(x=True, y=False)
        layout.addWidget(self._plot)

        # Waveform envelope
        self._wave_item = pg.PlotDataItem(pen=pg.mkPen("#4a9eff", width=1))
        self._plot.addItem(self._wave_item)

        # Selection region — draggable handles + body
        self._region = pg.LinearRegionItem(
            values=[0.0, 5.0],
            brush=pg.mkBrush(255, 215, 0, 35),
            pen=pg.mkPen("#ffd700", width=2),
            movable=True,
        )
        self._region.sigRegionChanged.connect(self._on_region_changed)
        self._plot.addItem(self._region)

        # Playback cursor (red dashed vertical line)
        self._cursor = pg.InfiniteLine(
            pos=0.0,
            angle=90,
            pen=pg.mkPen("#ff4444", width=2, style=Qt.PenStyle.DashLine),
            movable=False,
        )
        self._cursor.setVisible(False)
        self._plot.addItem(self._cursor)

        # Click-outside-region → snap to click position
        self._plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_waveform(self, data: np.ndarray, sample_rate: int):
        """Downsample audio to a peak envelope and display it."""
        self._duration = len(data) / sample_rate

        mono = data.mean(axis=1).astype(np.float32) if data.ndim > 1 else data.astype(np.float32)

        # Peak envelope — pairs of (min, max) so the outline looks solid
        target = 60_000
        if len(mono) > target:
            chunk = max(1, len(mono) // target)
            n = len(mono) // chunk
            m = mono[: n * chunk].reshape(n, chunk)
            env_max = m.max(axis=1)
            env_min = m.min(axis=1)
            t = np.linspace(0, self._duration, n)
            xs = np.empty(n * 2, dtype=np.float32)
            ys = np.empty(n * 2, dtype=np.float32)
            xs[0::2] = t
            xs[1::2] = t
            ys[0::2] = env_min
            ys[1::2] = env_max
        else:
            xs = np.linspace(0, self._duration, len(mono), dtype=np.float32)
            ys = mono

        self._wave_item.setData(xs, ys)
        self._plot.setXRange(0, self._duration, padding=0.01)

        initial_end = min(5.0, self._duration)
        self._region.setRegion([0.0, initial_end])

    def get_selection(self) -> tuple[float, float]:
        return self._region.getRegion()

    def set_selection(self, start: float, end: float):
        if self._duration > 0:
            start = max(0.0, min(start, self._duration - 0.05))
            end = max(start + 0.05, min(end, self._duration))
        self._region.setRegion([start, end])

    def set_cursor(self, pos_sec: Optional[float]):
        if pos_sec is None:
            self._cursor.setVisible(False)
        else:
            self._cursor.setPos(pos_sec)
            self._cursor.setVisible(True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_region_changed(self):
        s, e = self._region.getRegion()
        s = max(0.0, s)
        if self._duration > 0:
            e = min(self._duration, e)
        if e <= s:
            e = s + 0.05
        self.selection_changed.emit(s, e)

    def _on_scene_clicked(self, event):
        """Snap the selection region when user clicks outside it."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        vb = self._plot.getPlotItem().getViewBox()
        x = float(vb.mapSceneToView(event.scenePos()).x())

        s, e = self._region.getRegion()
        if s <= x <= e:
            return  # click was inside the region — let LinearRegionItem handle it

        # Move region so its center is at x, preserving width
        width = e - s
        new_s = max(0.0, x - width / 2)
        new_e = new_s + width
        if self._duration > 0 and new_e > self._duration:
            new_e = self._duration
            new_s = max(0.0, new_e - width)
        self.set_selection(new_s, new_e)
