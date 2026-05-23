"""Persistent settings for VoicePattern (keybindings, future prefs)."""

import json
from pathlib import Path

SETTINGS_FILE = Path.home() / ".config" / "voicepattern" / "settings.json"

# Keys are stable action IDs.  Values are Qt key-sequence strings.
DEFAULT_KEYBINDINGS: dict[str, str] = {
    "play_stop":       "Space",
    "play_recording":  "P",
    "record":          "R",
    "loop":            "L",
    "analyze":         "A",
    "open_file":       "Ctrl+O",
    "shift_left_01":   "Left",
    "shift_right_01":  "Right",
    "shift_left_1":    "Ctrl+Left",
    "shift_right_1":   "Ctrl+Right",
    "shrink_sel":      "[",
    "expand_sel":      "]",
}

# Human-readable labels shown in the settings dialog table.
ACTION_LABELS: dict[str, str] = {
    "play_stop":       "Play / Stop  (reference)",
    "play_recording":  "Play / Stop  (your recording)",
    "record":          "Start / Stop recording",
    "loop":            "Toggle loop",
    "analyze":         "Analyze pitch",
    "open_file":       "Open audio file",
    "shift_left_01":   "Shift selection ← 0.1 s",
    "shift_right_01":  "Shift selection → 0.1 s",
    "shift_left_1":    "Shift selection ← 1 s",
    "shift_right_1":   "Shift selection → 1 s",
    "shrink_sel":      "Shrink selection end",
    "expand_sel":      "Expand selection end",
}


def load_settings() -> dict:
    """Return settings dict, merging saved values over defaults."""
    result: dict = {"keybindings": dict(DEFAULT_KEYBINDINGS)}
    try:
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text())
            result["keybindings"].update(saved.get("keybindings", {}))
    except Exception:
        pass
    return result


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except Exception:
        pass
