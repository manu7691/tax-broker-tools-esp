"""Load/save manual income entries and imported AEAT casillas to manual_data.json."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

_FILENAME = "manual_data.json"


def _default() -> dict[str, Any]:
    return {"work_income": {}, "real_estate": {}, "aeat_filed": {}}


def load_manual_data(input_dir: Path) -> dict[str, Any]:
    path = input_dir / _FILENAME
    if not path.exists():
        return _default()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Ensure all top-level keys exist
        for k in ("work_income", "real_estate", "aeat_filed"):
            data.setdefault(k, {})
        return data
    except (json.JSONDecodeError, OSError):
        return _default()


def save_manual_data(input_dir: Path, data: dict[str, Any]) -> None:
    path = input_dir / _FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp file
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        tmp = Path(fh.name)
    tmp.replace(path)


def merge_aeat_import(manual: dict[str, Any], extracted: dict[str, Any], year: int) -> None:
    """Merge parsed AEAT casillas into manual["aeat_filed"][year]."""
    filed: dict[str, Any] = dict(extracted)
    filed["source"] = "xml_upload"
    manual["aeat_filed"][str(year)] = filed
