# Project: Spanish Tax Engine

Python tool for calculating IRPF capital gains (stocks, crypto) using Spanish FIFO rules.

## Python environment

Use `/usr/local/bin/python3.11`. The repo `.venv` is broken (Python 3.9, missing deps). No `uv` installed — use `pip` directly.

Run scripts as: `/usr/local/bin/python3.11 -m tax_engine.<module>` or `tax-<command>` if the package is installed.

## Web app frontend

Frontend rules live in `src/tax_engine/web_app/frontend/CLAUDE.md` (React + Vite + Tailwind).
