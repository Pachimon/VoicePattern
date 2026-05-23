import time
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np
import sounddevice as sd
import soundfile as sf
from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class AudioFile:
    data: np.ndarray      # (samples, channels) float32
    sample_rate: int
    duration: float
    channels: int
    path: str


def _load_audio(path: str) -> tuple[np.ndarray, int]:
    """Load audio file, falling back to pydub for MP3/M4A."""
    try:
        data, sr = sf.read(path, dtype="float32", always_2d=True)
        return data, sr
    except Exception:
        pass

    from pydub import AudioSegment  # type: ignore
    seg = AudioSegment.from_file(path)
    sr = seg.frame_rate
    raw = np.frombuffer(seg.raw_data, dtype=np.int16).astype(np.float32)
    raw /= 32768.0
    if seg.channels == 2:
        raw = raw.reshape(-1, 2)
    else:
        raw = raw.reshape(-1, 1)
    return raw, sr


class AudioEngine(QObject):
    playback_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._audio: Optional[AudioFile] = None
        self._stop_event = threading.Event()
        self._playback_thread: Optional[threading.Thread] = None

        self._play_start_sec: float = 0.0
        self._play_end_sec: float = 0.0
        self._is_playing = False

        # Updated by the audio callback for accurate cursor tracking.
        # Written from PortAudio's C thread (with GIL acquired by sounddevice),
        # read from the Qt main thread — safe under CPython's GIL.
        self._current_frame: int = 0
        self._current_sr: int = 1          # avoid div-by-zero before first play
        self._output_latency_frames: int = 0  # subtracted in get_play_position

        self._recording = False
        self._recorded_chunks: list[np.ndarray] = []
        self._recording_sr = 44100
        self._input_stream: Optional[sd.InputStream] = None
        self._last_recording: Optional[tuple[np.ndarray, int]] = None

    @property
    def audio(self) -> Optional[AudioFile]:
        return self._audio

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def has_recording(self) -> bool:
        return self._last_recording is not None

    def load_file(self, path: str) -> AudioFile:
        data, sr = _load_audio(path)
        self._audio = AudioFile(
            data=data,
            sample_rate=sr,
            duration=len(data) / sr,
            channels=data.shape[1],
            path=path,
        )
        return self._audio

    def play_region(self, start_sec: float, end_sec: float, loop: bool = False):
        if self._audio is None:
            return
        sr = self._audio.sample_rate
        s = max(0, int(start_sec * sr))
        e = min(len(self._audio.data), int(end_sec * sr))
        self._start_playback(self._audio.data[s:e].copy(), sr, loop, start_sec, end_sec)

    def play_last_recording(self):
        if self._last_recording is None:
            return
        data, sr = self._last_recording
        self._start_playback(data, sr, False, 0.0, len(data) / sr)

    # ------------------------------------------------------------------
    # Core playback — OutputStream approach
    # ------------------------------------------------------------------

    def _start_playback(
        self,
        data: np.ndarray,
        sr: int,
        loop: bool,
        start_sec: float,
        end_sec: float,
    ):
        # Stop any existing playback first, waiting for the old thread to exit.
        # The old thread owns the previous OutputStream; we must not overlap.
        self.stop()
        self._stop_event.clear()

        self._play_start_sec = start_sec
        self._play_end_sec = end_sec
        self._is_playing = True
        self._current_frame = 0
        self._current_sr = sr

        # Ensure buffer is (N, channels) for sd.OutputStream
        buf: np.ndarray = data if data.ndim == 2 else data.reshape(-1, 1)
        channels = buf.shape[1]
        n_frames = len(buf)

        # Mutable position shared between callback and thread
        frame_pos = [0]
        finished_evt = threading.Event()
        stop_evt = self._stop_event   # local alias avoids attribute lookup in hot path

        def _callback(outdata: np.ndarray, frames: int, t, status):
            remaining = n_frames - frame_pos[0]
            take = min(frames, remaining)
            outdata[:take] = buf[frame_pos[0]: frame_pos[0] + take]
            if take < frames:
                outdata[take:] = 0
            frame_pos[0] += take
            self._current_frame = frame_pos[0]

            if take < frames:                       # reached end of buffer
                if loop and not stop_evt.is_set():  # seamless loop restart
                    frame_pos[0] = 0
                    self._current_frame = 0
                else:
                    finished_evt.set()
                    raise sd.CallbackStop()

        def _run():
            try:
                with sd.OutputStream(
                    samplerate=sr,
                    channels=channels,
                    dtype="float32",
                    callback=_callback,
                ) as stream:
                    # Store actual latency (in frames) so get_play_position() can
                    # subtract it and report what's *heard*, not what's been queued.
                    self._output_latency_frames = int(stream.latency * sr)
                    # Block until either the user stops or playback ends naturally.
                    # Exiting the `with` block calls stream.close() from THIS thread,
                    # which is the only safe way to stop a PortAudio stream — calling
                    # sd.stop() from a different thread was the cause of the double-free.
                    while not stop_evt.is_set() and not finished_evt.is_set():
                        time.sleep(0.015)
            except Exception as _e:
                import sys
                print(f"AudioEngine stream error: {_e}", file=sys.stderr)
            finally:
                self._is_playing = False
                if finished_evt.is_set() and not stop_evt.is_set():
                    self.playback_finished.emit()

        self._playback_thread = threading.Thread(target=_run, daemon=True)
        self._playback_thread.start()

    def stop(self):
        """Signal the playback thread to stop and wait for it to finish cleanly."""
        self._stop_event.set()
        if self._playback_thread and self._playback_thread.is_alive():
            # Thread shuts down within ~15 ms (one sleep cycle) + stream.close() time.
            self._playback_thread.join(timeout=0.5)
        self._is_playing = False

    # ------------------------------------------------------------------
    # Cursor position
    # ------------------------------------------------------------------

    def get_play_position(self) -> Optional[float]:
        """Playback position of the audio *currently being heard* (latency-corrected)."""
        if not self._is_playing:
            return None
        # _current_frame is how many frames have been sent to the driver.
        # The driver still needs to play them through its hardware buffer, so
        # we subtract the output latency to get what's actually audible right now.
        heard_frame = max(0, self._current_frame - self._output_latency_frames)
        pos = self._play_start_sec + heard_frame / self._current_sr
        if pos >= self._play_end_sec:
            return None
        return pos

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def start_recording(self, sample_rate: int = 44100):
        self._recorded_chunks = []
        self._recording_sr = sample_rate
        self._recording = True

        def _callback(indata: np.ndarray, frames, ts, status):
            if self._recording:
                self._recorded_chunks.append(indata.copy())

        self._input_stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=_callback,
        )
        self._input_stream.start()

    def stop_recording(self) -> tuple[Optional[np.ndarray], int]:
        self._recording = False
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None

        if not self._recorded_chunks:
            return None, self._recording_sr
        result = np.concatenate(self._recorded_chunks, axis=0), self._recording_sr
        self._last_recording = result
        return result

    def get_mono_region(self, start_sec: float, end_sec: float) -> tuple[np.ndarray, int]:
        """Return a mono float64 slice suitable for parselmouth."""
        if self._audio is None:
            return np.array([]), 0
        sr = self._audio.sample_rate
        s = max(0, int(start_sec * sr))
        e = min(len(self._audio.data), int(end_sec * sr))
        region = self._audio.data[s:e]
        mono = region.mean(axis=1) if region.ndim > 1 else region
        return mono.astype(np.float64), sr
