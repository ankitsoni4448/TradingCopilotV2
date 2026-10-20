# Trading Copilot V2 — Windows demo setup

This package is a research collector, not a trading robot. It does not click
Binomo controls, place orders, or claim that Binance predicts Crypto IDX. Keep
Binomo in **demo** mode throughout setup and collection.

## 1. Install prerequisites

1. Install 64-bit Python 3.12 from <https://www.python.org/downloads/windows/>.
   Enable the Python launcher during installation.
2. Install Tesseract OCR 5 for Windows using the Windows guidance linked from
   the official Tesseract documentation:
   <https://github.com/tesseract-ocr/tessdoc/blob/main/Installation.md>.
3. Open a new PowerShell window and confirm Python is available:

   ```powershell
   py -3.12 --version
   ```

The collector searches `PATH` and standard Tesseract install locations. If it
cannot find OCR, set `tesseract_executable` in `collector_config.json` to the
full path, commonly `C:\Program Files\Tesseract-OCR\tesseract.exe`.

## 2. Create the isolated environment

Extract the ZIP to a normal folder such as `C:\TradingCopilotV2`. Do not merge
it into the old bot folder. In File Explorer, open that folder, type
`powershell` in the address bar, and press Enter.

The supplied setup script creates an isolated environment, installs pinned
dependency ranges, runs all tests, and creates the local config:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
```

It must finish with all tests passing. The script does not connect to Binomo,
capture the screen, create a database, or make a trade.

## 3. Position and calibrate the Binomo demo window

1. Log in manually and select the **demo** account.
2. Open Crypto IDX with the same desktop chart layout used during calibration.
3. Use the actual display dimensions and keep the Binomo demo interface visible.
   The supplied config is set for 1280×720; `left`, `top`, `width`, and `height`
   must describe a rectangle fully inside the current display.
4. If the interface layout or zoom differs, adjust `price_tag_roi` and
   `price_tag_color_tolerance` conservatively. Multiple plausible live labels
   are rejected; chart/history labels are never substituted for the live tag.
5. Run:

   ```powershell
   .\.venv\Scripts\python.exe collector_cli.py --calibrate
   ```

Calibration is successful only if it returns JSON with
`"status": "CALIBRATED"`, a plausible current price, payout ratio, platform time, expiry,
and `"pixels_saved": false`. It performs no network or database write.
The majority-opinion block may report available exploratory up/down ratios or
`UNAVAILABLE_EXPLORATORY_ONLY`; this optional field does not control calibration.

Do not continue if OCR is wrong or calibration reports an error. Adjust only
the capture rectangle/visible layout and retry; do not weaken the validators.

Continuous collection performs a fresh calibration probe before creating or
writing the database, and stops if calibration fails. Calibration is also
required before the one-capture check below.

## 4. Run the one-capture evidence gate

With the demo chart still visible, note the payout shown by Binomo and run:

```powershell
.\.venv\Scripts\python.exe collector_cli.py --once
```

Expected behavior is one `ACCEPTED` or `REJECTED` JSON attempt followed by a
session summary. `ACCEPTED` should include a capture ID, quality, Binance REST
latency, and maximum target/reference timing offset. The program observes only;
it never controls the mouse or keyboard.

Retain the terminal output. A rejection is useful diagnostic evidence and must
not be relabeled as a valid sample.

## 5. Collect one uninterrupted demo hour

Only after calibration and the one-capture gate pass:

```powershell
.\.venv\Scripts\python.exe collector_cli.py
```

Leave the demo chart visible and unchanged. Stop with `Ctrl+C`. The database
stores extracted observations, timing, quality, and audit metadata; raw screen
pixels are not persisted by the collector.

Audit the session. The collector now reads the displayed payout from every
accepted frame and refuses inconsistent values:

```powershell
.\.venv\Scripts\python.exe readiness_cli.py trading_copilot.db
```

You may add `--payout 0.83` as an operator cross-check; the audit fails if it
does not match the consistent screen-OCR evidence.

At 83% payout the mechanical break-even win rate is
`1 / (1 + 0.83) = 54.64%`, before uncertainty or execution mismatch. Readiness
is not proof of predictive value; it only permits baseline research if every
horizon passes. Do not inspect or tune on sealed holdout rows.

## Security and privacy

- No exchange or Binomo API key is required. Binance reference prices use a
  public market-data endpoint.
- Never copy `.env` files or keys from the old repository. Previously committed
  credentials must be treated as compromised and rotated by their owners.
- Do not send the SQLite database publicly; it contains timestamped behavioral
  research data even though it contains no screenshot pixels.
- Keep live execution manual. This package contains no order-click automation.

## What to return for the next review

Return the calibration JSON, the `--once` output, the displayed payout, Windows
display scaling, browser zoom, and the readiness report after the first hour.
Do not send passwords, cookies, API keys, or full-screen screenshots.
