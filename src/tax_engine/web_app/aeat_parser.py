"""Parse AEAT Modelo 100 XML borrador to extract key casillas for comparison."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any


_CASILLA_MAP = {
    # Work income
    "0001": "work_income_gross",
    "0014": "work_income_net",
    # Dividends / interest (RCM)
    "0027": "dividends",
    "0029": "interest",
    "0031": "other_rcm",
    # Capital gains / losses (transmisiones)
    "0328": "transm_value",
    "0330": "acq_value",
    "0334": "capital_gains_positive",   # net positive (gains > losses)
    "0344": "capital_gains_negative",   # net negative (losses > gains) — stored positive
    # Savings base
    "0460": "base_ahorro",
    # Double taxation deduction
    "0588": "foreign_tax_deduction",
    "0589": "foreign_tax_excess",
}


def parse_aeat_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Parse an AEAT IRPF XML and return year + extracted casilla values."""
    text = xml_bytes.decode("utf-8", errors="replace")
    # Strip XML namespace declarations so tag names are unqualified
    text = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", text)
    root = ET.fromstring(text)

    # Extract filing year from common root attribute names
    year: int | None = None
    for attr in ("ejer", "ejercicio", "anyo", "year", "ejercicioFiscal", "Ejercicio"):
        val = root.get(attr)
        if val and val.strip().isdigit():
            year = int(val.strip())
            break

    # Walk all descendants collecting casilla numbers and their values
    raw: dict[str, float] = {}
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()  # strip namespace if any slipped through
        if tag not in ("casilla", "c"):
            continue
        # Try common attribute names for the casilla number
        num_str: str | None = None
        for attr in ("num", "n", "id", "numero"):
            v = elem.get(attr, "")
            if v.strip().lstrip("0").isdigit() or v.strip() == "0":
                num_str = v.strip()
                break
        if num_str is None:
            continue
        try:
            key = str(int(num_str)).zfill(4)
            val_text = (elem.text or "").strip().replace(",", ".")
            if val_text:
                raw[key] = float(val_text)
        except (ValueError, OverflowError):
            continue

    # Map raw casillas to named fields
    extracted: dict[str, float] = {}
    for casilla, name in _CASILLA_MAP.items():
        if casilla in raw:
            extracted[name] = raw[casilla]

    # Derive signed capital gains net (positive = gain, negative = loss)
    gains = raw.get("0334", 0.0)
    losses = raw.get("0344", 0.0)
    extracted["capital_gains_net"] = gains - losses

    return {"year": year, "raw_casillas": raw, "extracted": extracted}
