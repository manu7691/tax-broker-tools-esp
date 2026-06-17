"""CLI entry point for tax-web — launches the local FastAPI server."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spanish Tax Engine — local web interface (tax-web)."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("input"),
        help="Directory holding espp/, rsu/, orders/, crypto/ data (default: input).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="TCP port to listen on (default: 8080).",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1 — local only).",
    )
    args = parser.parse_args()

    import uvicorn

    from tax_engine.web_app.app import app, set_input_dir

    set_input_dir(args.input_dir)
    print(f"tax-web  →  http://{args.host}:{args.port}/")
    print(f"Input dir: {args.input_dir.resolve()}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
