# Windows validation fix — 2026-10-04

This package closes SQLite connections explicitly so Windows can remove temporary test databases after each test. It also performs the stale-platform-clock gate before optional majority-opinion OCR, preventing an unavailable OCR executable from masking a stale-frame rejection. `pytest` is now declared in `requirements-dev.txt`.

Validation in the build environment: `108 passed`.

On the target Windows laptop, install dev requirements and run `python -m pytest -q`. Continue to calibration only if the Windows run is clean.
