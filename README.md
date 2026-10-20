# Trading Copilot V2

This branch is an evidence-first demo research baseline. It does **not** place trades and does not claim a calibrated probability, accuracy, or profit.

## Current scope

- fetches public Binance reference data without API credentials;
- exposes the selected timeframe consistently through `GET /predict?symbol=ETHUSDT&tf=1m`;
- labels Binance explicitly as an unvalidated reference feed for Binomo/Crypto IDX;
- returns `UP`, `DOWN`, or `NO_TRADE` from a transparent indicator baseline;
- reports heuristic `signal_strength`, never fake model confidence;
- excludes open candles and avoids backward filling future information.
- stores target/reference observations in an append-only SQLite evidence log;
- measures 60/180/300-second directional agreement with explicit timing offsets.
- atomically pairs target-screen observations with latency-measured reference ticks.
- builds leakage-resistant lead/lag samples for 30/60/120/180/300-second
  horizons using strictly as-of reference features.

## Run locally

```bash
python -m venv .venv
# activate the environment, then:
pip install -r requirements.txt
python app.py
```

Optional UI:

```bash
streamlit run frontend_service.py
```

Tests (standard library runner):

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The evidence layer stores screenshot hashes for provenance; raw screenshots are not stored by default.

For a clean Windows-laptop installation, use `WINDOWS_SETUP.md` and
`setup_windows.ps1`. The release archive deliberately excludes screenshots,
databases, virtual environments, credentials and the obsolete upstream copy.

Local capture endpoint: `POST /observations/capture`.

It accepts extracted target price/timestamp data and an optional screenshot hash. It does not accept image uploads or place trades.

Screen context stores payout, canonical chart display interval (`1s` through `1d`) and expiry independently. Display intervals are never treated as forecast horizons. Durable outcome jobs are scheduled separately for 60, 180 and 300 seconds; missing screen captures are recorded as failures rather than fabricated outcomes.

Candidate reference baskets are immutable, versioned hypotheses with reproducible fingerprints. BTC-only, ETH-only and equal-weight BTC/ETH baselines exist for comparison; none is claimed to reproduce Crypto IDX.

The provisional phone-screen extractor defines normalized privacy-limited regions and a dynamic current-price tag locator. OCR evidence is rejected when required fields are missing, confidence is low, more than one current-price candidate is present, or the countdown relationship is inconsistent. It does not generate trading signals and the layout must be calibrated on the local device before automatic collection.

The desktop observer is now the primary path. It detects the moving highlighted price tag using validated color, size, anchor and fill-coverage constraints, then sends only that crop to local Tesseract OCR. Separate narrow crops read the displayed Crypto IDX payout and the up/down majority-opinion percentages. The full screen is not persisted. Tesseract must be installed separately on the Windows machine; an OCR-engine failure, ambiguous price, wrong instrument or ambiguous payout is rejected. Majority opinion is stored only as optional exploratory metadata: its failure is disclosed but does not discard otherwise valid synchronized evidence, and it is not automatically admitted to any model.

Trade verification requires an exit observation at or after the recorded expiry. An active-trade potential-return banner is never treated as a completed result. Once complete, the verifier independently checks direction and payout against entry/expiry prices and flags platform-report mismatches.

Desktop timestamp extraction OCRs the platform clock and selected expiry from two separate narrow regions. It requires an explicit GMT offset, rejects stale or ambiguous times, limits the accepted future horizon to one hour, and permits next-day rollover only at the visible midnight boundary. Host time is not substituted for missing platform time.

The continuous collector connects one supplied desktop frame to the validated
price/time readers and then captures BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and
XRPUSDT in one public REST request. The target and all five references are
committed atomically, with one shared request-midpoint timestamp, latency,
offsets and quality reasons. Stale screen frames are rejected before any
reference request. Chart interval remains null until independently read from
each frame; the collector does not assume `5s` from prior screenshots.

The five-asset equal-weight basket is exploratory and is not part of the frozen
45-hypothesis V1 relationship search. Collection breadth must not be mistaken
for evidence about Crypto IDX composition or predictive value.

The causal feature engine builds separate datasets for each 30/60/120/180/300
second horizon. Its V1 schema contains target momentum plus five individual
market returns, equal-market return, directional breadth and cross-market
dispersion for 15/30/60-second lookbacks. It uses only as-of observations,
rejects incomplete or low-quality rows, fingerprints both schema and data, and
returns a metadata-only sealed chronological holdout after an outcome-sized
embargo. The decision timestamp is the latest actual data-availability time in
the same atomic capture, not the REST request midpoint. Labels start from the
first target observation after that decision and record the resulting entry
delay, so network/OCR latency cannot become hidden future information. It
produces labels, not probabilities or live trading signals.

The relationship laboratory separates past reference lookbacks from future
Crypto IDX labels. Reference ticks after the prediction timestamp are never
eligible, low-quality captures are excluded, outcome overlap is disclosed, and
forecast-horizon-sized embargoes separate chronological train, validation and
sealed holdout partitions. Development reports expose only the holdout window,
count and fingerprint—not holdout accuracy. Brier score and log loss remain
inapplicable until a genuine probability model exists.

The initial relationship search is preregistered as three immutable baskets ×
three lookbacks × five forecast horizons (45 hypotheses). Candidate selection
uses validation only and reports a Bonferroni family-wise error budget. Run one
read-only development report with `python relationship_cli.py trading_copilot.db
--basket BTC_ETH_EQUAL_V1 --lookback 30 --horizon 60` after enough synchronized
observations have accumulated.

The V1 baseline laboratory compares a train-majority rule with target-momentum
and equal-market-momentum rules at fixed 15/30/60-second lookbacks. Candidate
selection is validation-only, uses the supplied net payout assumption, and
reports coverage, accuracy, confusion matrix, Wilson uncertainty, losing
streak, drawdown and expected value. Exact ties prefer the simpler rule, and
the report discloses a seven-hypothesis Bonferroni budget plus whether the
Wilson lower bound exceeds payout break-even. It cannot inspect final holdout rows and
never promotes the validation winner. After sufficient real data exists, run
`python baseline_cli.py trading_copilot.db --horizon 60 --payout 0.82` using
the payout displayed for that observed session. These directional rules do not
produce calibrated probabilities.

Before outer validation selection, `evaluate_baseline_walk_forward` runs
expanding-window folds exclusively inside the training partition. Fold training
rows are purged using the actual label-observation time (post-decision entry +
horizon + exit offset), not just anchor + horizon. Reports expose fold accuracy,
EV, drawdown, losing streak and selection stability while leaving both outer
validation and final holdout unevaluated.

Invoke both reports with `python baseline_cli.py trading_copilot.db --horizon
60 --payout 0.82 --walk-forward`; the payout must come from the observed Binomo
session rather than a default assumption.

Before either report, run `python readiness_cli.py trading_copilot.db`. The audit
uses a single consistent payout OCR value observed across the session. An
optional `--payout 0.83` operator value acts as a cross-check and must match the
screen evidence; it is not a default. The audit
fails closed on missing payout, incomplete five-market capture IDs, low-quality
targets, payout changes/mismatches, fragmented sessions, weak feature coverage or insufficient purged
train/validation rows. It reports readiness separately for every horizon and
never trains, scores a holdout or permits promotion.

Windows capture requires an explicit rectangle inside the current display; the supplied config targets 1280×720 and the original 1731×800 layout remains supported. Copy `collector_config.example.json` to `collector_config.json`, adjust the rectangle and normalized price-tag settings as needed, then run `python collector_cli.py --calibrate`. Tesseract is resolved from PATH or standard Windows install locations, with an explicit config path supported. Calibration reads one in-memory frame without network or database writes. Continuous collection runs a successful calibration probe before creating or writing the database; no mode controls the mouse, keyboard, or trade buttons.

The floating panel is an opt-in full-desktop observer. Start it with
`python collector_panel.py`; screen frames are processed locally and are not
saved unless the user explicitly requests a debug snapshot. Observer market
prices enter memory only after repeatable same-asset readings. Export/evaluate
the separate observer dataset with
`python observer_ml.py trading_copilot.db --export observer_dataset.csv`.
Insufficient confirmed history reports `NOT_READY`; decisions remain
`NO_TRADE`, and no model is automatically promoted.

## Security

The original repository committed live-looking credentials. Removing `.env` from the current tree does not erase Git history. Those credentials must be revoked/rotated by their owners and the historical commit must later be purged.

## Next evidence gate

Before training a predictive model, collect simultaneous target-screen and reference-feed observations. Evaluate directional agreement separately for 60, 180, and 300 seconds using chronological holdouts.
