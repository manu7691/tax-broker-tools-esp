"""JSON serialization helpers for engine dataclasses (Decimal, date, Enum)."""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi.responses import Response


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {to_jsonable(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(i) for i in obj]
    return obj


def tax_response(data: Any) -> Response:
    return Response(content=json.dumps(to_jsonable(data)), media_type="application/json")
