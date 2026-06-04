"""
Thin wrapper around the AnkiConnect HTTP API (version 6).

All functions raise RuntimeError on AnkiConnect-level errors.
Network errors propagate as urllib.error.URLError / OSError.
"""

import base64
import json
import urllib.error
import urllib.request
from typing import Any


def _call(url: str, action: str, **params) -> Any:
    payload = json.dumps({"action": action, "version": 6, "params": params})
    req = urllib.request.Request(
        url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read())
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


def test_connection(url: str) -> str:
    """
    Return a success string, or raise an exception.
    Checks that AnkiConnect version >= 6.
    """
    version = _call(url, "version")
    if int(version) < 6:
        raise RuntimeError(f"AnkiConnect version {version} is too old (need ≥ 6)")
    return f"Connected  (AnkiConnect v{version})"


def get_deck_names(url: str) -> list[str]:
    return sorted(_call(url, "deckNames"))


def get_model_names(url: str) -> list[str]:
    return sorted(_call(url, "modelNames"))


def get_model_fields(url: str, model_name: str) -> list[str]:
    return _call(url, "modelFieldNames", modelName=model_name)


def store_media_file(url: str, filename: str, data: bytes) -> str:
    """Upload audio bytes to Anki's media collection. Returns the filename."""
    b64 = base64.b64encode(data).decode("ascii")
    _call(url, "storeMediaFile", filename=filename, data=b64)
    return filename


def add_note(
    url: str,
    deck: str,
    model: str,
    fields: dict[str, str],
    tags: list[str] | None = None,
) -> int:
    """Add a note and return its Anki note ID."""
    note = {
        "deckName": deck,
        "modelName": model,
        "fields": fields,
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags": tags or ["voicepattern"],
    }
    return _call(url, "addNote", note=note)
