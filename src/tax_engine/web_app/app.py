"""FastAPI application for the tax-web local server."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .engine_runner import EngineRunOutput, generate_crypto_html, generate_stock_html, run_engines
from .manual_store import load_manual_data, merge_aeat_import, save_manual_data
from .serialization import tax_response

app = FastAPI(title="Tax Engine", docs_url=None, redoc_url=None)

_input_dir: Path = Path("input")
_engine_output: EngineRunOutput | None = None
_DIST = Path(__file__).parent / "dist"


def set_input_dir(path: Path) -> None:
    global _input_dir
    _input_dir = path.resolve()


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    dist_html = _DIST / "index.html"
    if dist_html.exists():
        return HTMLResponse(content=dist_html.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<p>Build the frontend: <code>cd src/tax_engine/web_app/frontend && npm run build</code></p>"
    )


@app.post("/api/run")
async def run_engine() -> Response:
    global _engine_output
    try:
        _engine_output = await asyncio.to_thread(run_engines, _input_dir)
        return tax_response(_engine_output.result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/result")
async def get_result() -> Response:
    if _engine_output is None:
        return tax_response(None)
    return tax_response(_engine_output.result)


@app.get("/api/manual")
async def get_manual() -> Response:
    return tax_response(load_manual_data(_input_dir))


@app.post("/api/manual")
async def save_manual(request: Request) -> dict[str, str]:
    data = await request.json()
    save_manual_data(_input_dir, data)
    return {"status": "saved"}


@app.post("/api/aeat/parse")
async def parse_aeat(file: UploadFile = File(...)) -> Response:
    from .aeat_parser import parse_aeat_xml

    content = await file.read()
    try:
        parsed = parse_aeat_xml(content)
        return tax_response(parsed)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"XML parse error: {exc}") from exc


@app.post("/api/aeat/import")
async def import_aeat(request: Request) -> dict[str, Any]:
    payload = await request.json()
    year = int(payload["year"])
    extracted: dict[str, Any] = payload["extracted"]
    manual = load_manual_data(_input_dir)
    merge_aeat_import(manual, extracted, year)
    save_manual_data(_input_dir, manual)
    return {"status": "imported", "year": year}


@app.get("/api/comparison")
async def get_comparison() -> Response:
    manual = load_manual_data(_input_dir)
    if _engine_output is None:
        return tax_response({"rows": [], "error": "Run engine first"})
    rows = _build_comparison(_engine_output.result, manual)
    return tax_response({"rows": rows})


@app.get("/api/report/stocks", response_class=HTMLResponse)
async def report_stocks(lang: str = "en") -> HTMLResponse:
    if _engine_output is None:
        raise HTTPException(status_code=404, detail="Run engine first")
    html = await asyncio.to_thread(generate_stock_html, _engine_output, lang)
    if html is None:
        raise HTTPException(status_code=404, detail="No stock data available")
    return HTMLResponse(content=html)


@app.get("/api/report/crypto", response_class=HTMLResponse)
async def report_crypto(lang: str = "en") -> HTMLResponse:
    if _engine_output is None:
        raise HTTPException(status_code=404, detail="Run engine first")
    html = await asyncio.to_thread(generate_crypto_html, _engine_output, lang)
    if html is None:
        raise HTTPException(status_code=404, detail="No crypto data available")
    return HTMLResponse(content=html)


def _build_comparison(
    result: Any, manual: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build comparison rows: computed vs AEAT filed, one row per category per year."""
    aeat_filed: dict[str, dict[str, Any]] = manual.get("aeat_filed", {})
    rows: list[dict[str, Any]] = []

    # Collect all years from hacienda rows and filed data
    years: set[int] = {row["year"] for row in result.hacienda_years}
    years |= {int(y) for y in aeat_filed}

    hacienda_by_year = {row["year"]: row for row in result.hacienda_years}
    crypto_by_year = {r.year: r for r in result.crypto_years}

    categories = [
        ("Capital Gains (Stocks)", "saldo_neto", "capital_gains_net"),
        ("Crypto Capital Gains", "crypto_net", "crypto_net"),
        ("Base del Ahorro", "base_ahorro", "base_ahorro"),
        ("Dividends (0027)", "dividends", "dividends"),
        ("Interest (0029)", "interest", "interest"),
        ("Foreign Tax (0588)", "foreign_tax", "foreign_tax_deduction"),
    ]

    for year in sorted(years):
        h = hacienda_by_year.get(year, {})
        filed = aeat_filed.get(str(year), {})

        for label, computed_key, filed_key in categories:
            # Special case: crypto might only be in crypto_years, not hacienda_years
            if computed_key == "crypto_net":
                cr = crypto_by_year.get(year)
                computed = cr.net if cr else None
            else:
                computed = h.get(computed_key)

            filed_val = filed.get(filed_key)

            diff: float | None = None
            if computed is not None and filed_val is not None:
                diff = round(computed - filed_val, 2)

            if computed is None and filed_val is None:
                continue  # skip rows with no data

            rows.append(
                {
                    "year": year,
                    "category": label,
                    "computed": round(computed, 2) if computed is not None else None,
                    "filed": round(filed_val, 2) if filed_val is not None else None,
                    "diff": diff,
                }
            )

    return rows


# Mount built frontend assets — must come after all API route definitions
_assets_dir = _DIST / "assets"
if _assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="static_assets")
