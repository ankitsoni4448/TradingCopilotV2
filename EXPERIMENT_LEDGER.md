# Trading Copilot V2 experiment ledger

## EXP-001 — Reproducible reference-feed baseline

- Date: 2026-09-27
- Code baseline: `c16dc1a45df890119580515101884524ea1f0164`
- Hypothesis: removing future-data backfill, incomplete candles, fake confidence and forced directional decisions creates a safer reproducible baseline.
- Data window: deterministic synthetic minute candles and a mocked Binance response.
- Target horizon: not evaluated in this experiment.
- Method: temporal-causality, closed-candle, NO_TRADE and probability-label tests.
- Metrics: functional correctness only; no accuracy or profitability claim.
- Result: initial run exposed flat-market RSI=0 and a false DOWN signal. RSI edge-case correction produced 4/4 passing tests.
- Decision: retain as engineering baseline; do not promote a predictive model.
- Next: collect timestamp-aligned Binance and Binomo/Crypto IDX observations, then measure directional agreement at 60/180/300-second horizons.

## EXP-002 — Append-only synchronization evidence layer

- Date: 2026-09-27
- Code baseline: local Phase-1 baseline; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: immutable timestamped observations with explicit matching tolerance prevent manual result rewriting and ambiguous expiry scoring.
- Data window: deterministic mocked Binomo/Binance observations.
- Target horizons: 60, 180 and 300 seconds, stored and evaluated separately.
- Method: SQLite uniqueness constraints, nearest-timestamp matching, missing-exit rejection, screenshot SHA-256 provenance and unit-stake payout metrics.
- Metrics: directional agreement, coverage, maximum timing offset, break-even win rate, expected value, net units, maximum drawdown and losing streak.
- Result: 9/9 total tests passed. Evidence-layer tests confirmed duplicate rejection, exact-horizon matching, missing-exit rejection, timing-offset reporting and reproducible screenshot hashing.
- Decision gate: no model training until real paired target/reference observations exist.
- Next: add a local capture endpoint/CLI that records screen-extracted target prices and public reference prices without storing screenshots by default.

## EXP-003 — Atomic target/reference capture coordinator

- Date: 2026-09-27
- Code baseline: local EXP-002 evidence layer; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: pairing a target-screen observation with a REST reference tick while retaining latency and clock-skew uncertainty prevents false timestamp precision.
- Data window: deterministic mocked capture requests and reference responses.
- Target horizons: capture layer only; downstream 60/180/300-second scoring remains separate.
- Method: REST request-midpoint timestamp, atomic two-row SQLite transaction, latency/skew quality gates and adversarial rollback test.
- Metrics: request latency, target/reference offset, client clock skew, capture quality and atomic persistence.
- Result: capture coordinator passed its atomicity, latency, clock and validation tests; total suite reached 14/14 before API tests.
- Decision gate: low-quality captures remain auditable but must be excluded from model training unless a later policy explicitly accepts them.
- Next: connect the capture coordinator to a local-only API and add scheduled expiry recapture without automated trading actions.

## EXP-004 — Local capture API contract

- Date: 2026-09-27
- Code baseline: local EXP-003 capture coordinator.
- Hypothesis: a narrow local endpoint can accept extracted target values while preventing raw screenshots and automated order actions from entering the backend.
- Data window: deterministic API requests with an injected fake coordinator.
- Target horizons: not applicable; capture only.
- Method: FastAPI contract tests for health disclosures, input validation and capture response.
- Metrics: HTTP status, accepted fields, rejected fields and privacy disclosure.
- Result: API contract tests passed; complete suite reached 17/17. One non-failing TestClient dependency deprecation warning was observed and is not promoted to production validation.
- Decision gate: bind locally only; do not add trading/click endpoints.
- Next: implement an expiry scheduler that captures outcomes at fixed horizons and records failures rather than fabricating missing prices.

## EXP-005 — Screenshot-informed context and durable expiry jobs

- Date: 2026-09-28
- Code baseline: local EXP-004 capture API.
- Hypothesis: storing payout, chart interval and expiry independently prevents label contamination, while durable jobs prevent missing outcomes from being silently converted into losses or wins.
- Data window: deterministic screen-context requests and scheduler timestamps based on observed Crypto IDX screenshots.
- Target horizons: 60, 180 and 300 seconds stored as independent jobs.
- Method: domain validation plus SQLite PENDING/RUNNING/COMPLETED/FAILED state transitions.
- Metrics: scheduled due time, early-claim rejection, duplicate rejection, failure provenance and complete test-suite status.
- Result: 23/23 tests passed. Scheduler tests confirmed exact due times, no early/double claim, duplicate rejection and explicit FAILED records with no fabricated outcome capture.
- Decision gate: chart interval must never substitute for trade horizon; failed captures remain missing data.
- Next: introduce versioned, configurable multi-asset reference baskets and compare them only after paired Crypto IDX observations exist.

## EXP-006 — Canonical display intervals and versioned basket hypotheses

- Date: 2026-09-28
- Code baseline: local EXP-005; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: representing the screen's chart interval as a canonical unit-bearing value and fingerprinting each candidate reference basket prevents horizon confusion and silent basket-definition drift.
- Data window: deterministic screenshot-derived display contexts (`1h`, `3h`, `1d`) and synthetic synchronized BTC/ETH price pairs; no final holdout or forward-demo outcomes were used.
- Target horizons: unchanged at 60, 180 and 300 seconds; chart display interval is context only.
- Method: API/domain validation, metadata round-trip tests, immutable basket specifications, canonical SHA-256 fingerprints and weighted log returns.
- Metrics: schema acceptance/rejection, evidence completeness, basket identity stability and complete test-suite status. Predictive accuracy, calibration, coverage and EV remain unavailable because no real paired outcome set exists.
- Result: fixed the schema that rejected 1h/3h/1d screenshots; added BTC-only, ETH-only and equal-weight BTC/ETH candidate baselines without claiming a Crypto IDX replica. All 29 tests passed; compilation passed. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the schema and research scaffolding. Do not promote any reference basket or model before chronological paired observations beat simple baselines out of sample.
- Next: build a fixed-region local OCR extractor and capture Crypto IDX plus multiple reference symbols at the same timestamp, then compare candidates independently at 60/180/300 seconds on validation data while preserving chronological holdout and forward-demo sets.

## EXP-007 — Validation-first screen extraction contract

- Date: 2026-09-28
- Code baseline: local EXP-006; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: normalized privacy-limited regions plus conservative OCR validation can prevent ambiguous screenshots from becoming false labels.
- Data window: three user-provided 688×1536 Crypto IDX screenshots showing 1h, 3h and 1d display contexts around 08:44 local device time; parser behavior tested with deterministic OCR outputs derived from visible fields.
- Target horizons: extraction only; no predictive horizon was trained or scored. Existing 60/180/300-second jobs remain independent.
- Method: provisional normalized regions, dynamic current-price tag contract, minimum OCR confidence, unique-price parsing and countdown cross-field validation.
- Metrics: parser acceptance/rejection correctness and complete test-suite status; no accuracy, calibration, coverage or EV claim.
- Result: all three screenshots show a 30-second difference between `until expiration` and `time to buy`; encoded as a configurable consistency check with ±2-second tolerance. A real Tesseract full-screen probe read the instrument, payout, countdowns, amount and expiry, but returned many axis prices, missed the current-price tag in the 1h image, and missed all three display intervals. A targeted interval crop recovered `1d` and `3h` but only `h` for `1h`; inference to `1h` was deliberately rejected. This confirms that generic OCR must not write training evidence. The parser rejects ambiguous multi-price and low-confidence readings. The complete suite passed 35/35 tests and compilation passed; one environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain as a provisional extraction boundary, not as a production OCR engine. Do not admit automatically extracted observations to training until device-specific ROI calibration and labeled OCR accuracy tests pass.
- Next: implement and benchmark the local image-processing/OCR adapter on labeled screenshots, including dynamic price-tag detection and field-level precision/recall, before connecting it to the capture API.

## EXP-008 — Desktop dynamic price-tag detector

- Date: 2026-09-29
- Code baseline: local EXP-007; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: the desktop price label can be isolated from chart-axis prices using stable fill color plus geometric constraints, allowing narrow OCR without storing the full screen.
- Data window: six user-provided 1731×800 desktop screenshots spanning approximately 22:22–22:32; these are extraction-validation images, not predictive training or final holdout data.
- Target horizons: extraction only; no forecast horizon was trained or scored.
- Method: search a restricted right-chart band for the observed neutral tag fill, validate width/height/right-anchor/fill coverage, crop with minimal padding and run local Tesseract with a numeric whitelist.
- Metrics: price-tag localization coverage and exact OCR agreement against manually read displayed prices; complete unit-test status.
- Result: an initial ±2 RGB tolerance contaminated the tag mask with the horizontal price line and failed the real integration run after 3 images; the exact-fill default fixed this rather than weakening geometry checks. The corrected detector localized the tag in 6/6 screenshots and narrow OCR returned all six manually visible prices exactly: `641.8674000850`, `641.8673994907`, `641.8673997580`, `641.8674006355`, `641.8674005848`, `641.8674023236`. Full-screen OCR had previously failed because it mixed axis labels with the target. The complete suite passed 39/39 tests and compilation passed; one environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the narrow desktop detector as the primary screen-observation path, but do not call it production-ready from six correlated screenshots. Reject screenshots with unexpected theme, geometry or ambiguous OCR.
- Next: add desktop extraction for timestamp, payout, 5s interval, expiry/countdown and majority opinion, then benchmark field-level precision/recall on timestamped frames before wiring accepted observations into the capture API.

## EXP-009 — Expiry-gated trade-result verification

- Date: 2026-09-29
- Code baseline: local EXP-008; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: requiring post-expiry price evidence prevents an active trade's displayed potential return from being mislabeled as a completed win and enables future demo/platform consistency audits.
- Data window: three user-provided 1731×800 screenshots from one manually executed demo UP trade; extraction-validation evidence only, not model training or holdout data.
- Target horizon: the displayed sequence approaches expiry, but the latest supplied frame still shows `00:07` remaining; therefore no completed horizon is scored.
- Method: independently derive WIN/LOSS/TIE from direction and entry/exit prices only when the exit timestamp is at or after expiry; separately compare platform-reported outcome and total return using the observed payout ratio.
- Metrics: state-gate correctness, outcome agreement, payout agreement and complete test-suite status.
- Result: the price reader extracted `641.8673915259`, `641.8673912912`, and `641.8673915950` from the three new screenshots. The last screenshot is still pre-expiry, so its yellow `₹100 → ₹182` display is classified as potential return and the trade remains `PENDING`, not a verified win. The first test run exposed a floating-point money defect (`181.99999999999997` instead of ₹182.00); explicit currency rounding fixed it. Unit tests cover pending, consistent win, contradictory outcome and payout mismatch. The corrected complete suite passed 43/43 tests and compilation passed; one environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the expiry gate. These images neither prove nor disprove preferential demo outcomes. A confirmed after-expiry result frame or automatically captured post-expiry observation is required.
- Next: extract desktop platform timestamp/expiry automatically and connect post-expiry observations to this verifier, then accumulate demo consistency statistics without placing or automating trades.

## EXP-010 — Platform-clock and expiry synchronization gate

- Date: 2026-09-29
- Code baseline: local EXP-009; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: extracting the displayed platform clock and selected expiry independently can create an auditable expiry timestamp without substituting the host clock or silently rolling stale observations to the next day.
- Data window: nine saved 1731×800 desktop screenshots; extraction-validation evidence only, never model training or final holdout data.
- Target horizon: displayed short-duration expiry, capped at 3,600 seconds; no predictive horizon trained or scored.
- Method: two fixed normalized privacy-limited crops, local Tesseract, strict full clock plus GMT-offset parsing, unique HH:MM expiry parsing, future-time validation, and rollover only across an explicit 23:xx→00/01:xx boundary.
- Metrics: field-level joint acceptance coverage, rejection behavior, timestamp resolution correctness and complete test-suite status.
- Result: the extractor accepted a complete platform-clock plus expiry pair in 8/9 frames (88.9% joint coverage). All eight frames where the clock was visible were read successfully; the ninth had the clock physically covered by the Top-20 overlay and was rejected rather than dated from the host clock. Expiry alone was readable in 9/9, but incomplete evidence is not admitted. Resolved future intervals ranged from 41 to 86 seconds. Seven new adversarial/unit tests brought the complete suite to 50/50 passing; compilation passed. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the extractor for this calibrated 1731×800 desktop layout with fail-closed handling. Do not generalize its 88.9% coverage to other resolutions/themes, and do not score the earlier active trade because no post-expiry frame exists.
- Next: connect accepted timestamps and prices to scheduled post-expiry recapture, then collect a forward demo consistency set without automated orders.

## EXP-011 — Atomic continuous target/multi-reference capture

- Date: 2026-09-30
- Code baseline: local EXP-010; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: one screen observation paired with BTCUSDT and ETHUSDT from a single REST latency window prevents cross-symbol timestamp drift and makes competing reference hypotheses directly comparable.
- Data window: one previously saved 1731×800 Binomo frame for real OCR integration plus deterministic BTC/ETH ticks for plumbing validation; no training, holdout or forward-demo observations were consumed.
- Target horizons: collection only. Existing 60/180/300-second evaluations remain separate; no result was scored.
- Method: fail-closed price/clock/expiry OCR, ten-second freshness gate, one-request reference batch, local request-midpoint timestamps, and an atomic three-row SQLite transaction. Unit tests cover shared timestamps, incomplete feeds, symbol-order mismatch, stale frames and continuous-loop behavior.
- Metrics: accepted/rejected collection state, row atomicity, reference latency, target/reference offset and complete test-suite status.
- Result: the real-frame integration extracted Crypto IDX `641.8673915259`, platform time `2026-09-29T09:51:45+05:00` and expiry `09:53`; it atomically stored three rows. Deterministic reference inputs produced a 50 ms offset and 100 ms latency with `ACCEPTABLE` quality. The first 58-test run passed, but adversarial review found that the collector labeled every frame `5s` from prior visual context rather than per-frame evidence. That assumption was removed; interval metadata now remains null until independently extracted. The corrected suite passed 58/58 and compilation passed. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the multi-reference atomic path and continuous-loop boundary. Do not interpret deterministic BTC/ETH values as live data or evidence that either asset tracks Crypto IDX. Do not admit unverified chart-interval labels.
- Next: add an explicitly bounded Windows screenshot provider and durable rejection counters, then run a live demo-only collection session to measure real Binance latency, screen freshness, acceptance coverage and BTC/ETH synchronization before any relationship model is trained.

## EXP-012 — Safe Windows launcher and calibration gate

- Date: 2026-09-30
- Code baseline: local EXP-011; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164` and has no root `requirements.txt`.
- Hypothesis: an explicit bounded screen region plus a side-effect-free calibration command can move collection toward Ankit's Windows laptop without risking full-screen capture, credential leakage or incorrectly labeled data.
- Data window: one previously saved 1731×800 Binomo frame for real OCR calibration; no training, holdout or forward-demo observations consumed.
- Target horizons: collection setup only; no forecast was trained or scored.
- Method: exact-size capture boundary, four-million-pixel limit, no full-screen fallback, JSON configuration allowlist, rejection of credential-like/unknown settings, one-frame calibration without network/database writes, sanitized append-only collection health, and stop-safe continuous/one-shot modes.
- Metrics: calibration status, extracted target fields, capture age, database side effects and complete test-suite status.
- Result: real-frame calibration extracted `641.8673915259`, platform time `2026-09-29T09:51:45+05:00` and expiry `09:53`, with a one-second freshness offset; `pixels_saved` was false and no database file was created. Adversarial tests reject full-screen dimensions, unexpected image sizes, unknown config fields, unsafe intervals and unsupported reference symbols. The complete suite passed 68/68 tests and compilation passed; one environment-specific Starlette TestClient warning remains non-failing.
- Decision: retain the launcher and calibration gate. This is code-level Windows readiness, not physical validation on Ankit's laptop; DPI scaling and the actual Binomo rectangle still need an on-device calibration run.
- Next: run `--calibrate` on the Windows laptop, correct the rectangle until price/time/expiry pass, then run `--once` with real Binance ticks and inspect latency/offset/quality before starting continuous demo collection.

## EXP-013 — Leakage-resistant lead/lag laboratory

- Date: 2026-10-01
- Code baseline: local EXP-012; GitHub remains at `c16dc1a45df890119580515101884524ea1f0164` (the repository's only commit, dated 2025-10-16).
- Hypothesis: strictly as-of BTC/ETH basket returns can be tested as predictors of later Crypto IDX direction without admitting post-prediction reference ticks, overlapping split outcomes or candidate-selection access to final holdout results.
- Data window: two deterministic 60-sample simulations—one with a deliberately injected lead and one independent-pattern null. No real market, model-training, final-holdout or forward-demo observations were consumed.
- Target horizons: laboratory supports separate 30/60/120/180/300-second outcomes. The plumbing simulation used a 15-second reference lookback and 30-second target horizon.
- Method: as-of reference joins only; acceptable-quality filter; explicit feature age and label-offset measurements; chronological 60/20/20 partition; horizon-sized embargo; metadata-only sealed holdout with value-sensitive SHA-256 fingerprint; three baskets × three preregistered lookbacks × five horizons = 45 hypotheses; validation-only selection and Bonferroni per-hypothesis alpha `0.0011111111111111111`.
- Metrics: sample/comparable count, coverage, accuracy, majority baseline accuracy, confusion matrix, Wilson 95% accuracy interval, maximum feature age, target-exit offset and overlapping-outcome warning. Brier score/log loss are explicitly unavailable because no probability model exists.
- Result: injected-lead validation achieved 12/12 correct, accuracy `1.0` versus majority baseline `0.5`, Wilson 95% interval `[0.7575, 1.0]`; the independent-pattern validation achieved 6/12, accuracy `0.5`, baseline `0.5`, interval `[0.2538, 0.7462]`. Both holdouts remained `SEALED_NOT_EVALUATED` with 12 samples. Adversarial review found the first holdout object retained private sample rows and fingerprinted timestamps but not values; both defects were fixed so only metadata is returned and value changes alter the fingerprint. The complete suite passed 74/74 tests and compilation passed; one environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the laboratory as experiment infrastructure only. The simulation proves that the code distinguishes an injected relationship from a null pattern; it provides zero evidence that BTC, ETH or their basket predicts Crypto IDX. No challenger exists and no model is promoted.
- Next: run on-device calibration and one real synchronized capture, then accumulate enough accepted 5-second observations to execute the fixed 45-hypothesis development grid. Select at most one candidate per horizon on validation, account for overlap/effective sample size, and leave the chronological holdout untouched until the analysis protocol is frozen.

## EXP-014 — Atomic five-market reference expansion

- Date: 2026-10-01
- Code baseline: local EXP-013; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: BTC, ETH, BNB, SOL and XRP can be collected in one public Binance request and one atomic target/reference transaction, increasing relationship-research breadth without assuming that any asset is a Crypto IDX constituent.
- Data window: deterministic five-symbol response used for integration validation; no live Crypto IDX, training, holdout or forward-demo observations consumed.
- Target horizons: collection only. Existing 30/60/120/180/300-second research horizons remain separate and were not scored.
- Method: use Binance's documented authentication-free market-data-only host and the multi-symbol `GET /api/v3/ticker/price` contract; preserve requested symbol order, one request-midpoint timestamp and one latency window; fail closed on missing/unexpected symbols; commit Crypto IDX plus all five references atomically. The V1 45-hypothesis registry remains frozen; a five-asset equal-weight basket is marked exploratory rather than silently added to the search.
- Metrics: HTTP request count, returned-symbol completeness/order, distinct reference timestamps, latency, maximum target/reference offset, atomic row count, quality status and complete test-suite result.
- Result: deterministic integration used one request for five symbols, retained one shared reference timestamp, measured 120 ms latency and 60 ms maximum target/reference offset, atomically stored six rows and returned `ACCEPTABLE`. The first full test exposed one stale BTC/ETH-only expectation in the continuous-collector test; the production output was correct, the assertion was updated, and the corrected suite passed 78/78 tests with compilation passing. Production credential-literal scan remained zero. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the five-symbol collector. Do not infer Crypto IDX composition, lead/lag, accuracy or profitability from successful transport plumbing. Do not add the exploratory five-asset basket to V1 candidate selection before its registry is deliberately versioned.
- Next: on Ankit's Windows laptop, run calibration and then one real `--once` capture using the five-symbol configuration. Verify that all six rows share the expected capture ID, measure actual latency/offset and rejection behavior, and only then start continuous five-second demo collection.

## EXP-015 — Causal multi-market feature datasets and execution-time labels

- Date: 2026-10-01
- Code baseline: local EXP-014; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164`.
- Hypothesis: a deterministic 27-feature schema can transform synchronized Crypto IDX plus five-market captures into separate horizon datasets without using observations unavailable at signal time or scoring an outcome that began before the signal could be acted upon.
- Data window: one deterministic one-hour, five-second synthetic session with 721 Crypto IDX observations and 3,605 reference observations. This is plumbing/performance validation only; no real market, training, final holdout or forward-demo data was consumed.
- Target horizons: 30/60/120/180/300 seconds, built and split independently.
- Method: three past lookbacks (15/30/60 seconds); target log return; BTC/ETH/BNB/SOL/XRP log returns; equal-market log return; directional breadth; cross-market dispersion; strict quality and completeness gates; same-capture current reference batch; actual receipt/server availability timestamps; decision time equal to the latest required-data availability; first post-decision target observation as the simulated entry; explicit entry delay; horizon label measured from entry; six bulk chronological reads with in-memory binary-search joins; 60/20/20 chronological split with horizon-sized embargo and metadata-only sealed holdout.
- Metrics: dataset coverage, feature count/schema fingerprint, dataset fingerprint, label counts, maximum feature age, entry delay, target-exit offset, split sizes and build time. Accuracy, calibration, Brier score, log loss, payout EV and promotion status remain unavailable because no model was trained.
- Result: every horizon produced the fixed 27-feature schema. Rows/coverage were 702/97.36% (30s), 696/96.53% (60s), 684/94.87% (120s), 672/93.20% (180s) and 648/89.88% (300s). Synthetic UP/DOWN labels were broadly balanced. Maximum feature age was 100 ms, simulated post-decision entry delay 4,800 ms and exit offset 0 ms. Build time per horizon ranged from 73.96 to 101.36 ms. The initial implementation passed feature tests but adversarial review found two false-result risks: repeated SQLite connections would not scale, and request-midpoint timestamps do not represent when data became usable. Both were corrected with indexed bulk reads and receipt-time/next-entry label semantics. The complete corrected suite passed 84/84 tests and compilation passed; production credential-literal scan remained zero. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the causal feature engine. Reject midpoint-as-availability labels and any backtest that begins its outcome before a manual signal could exist. Do not interpret the synthetic coverage or balanced labels as predictive evidence, and do not train/promote a model until real synchronized data passes these gates.
- Next: execute a real Windows `--once` capture and inspect capture IDs plus server/receipt timestamps. Then collect at least a one-hour continuous demo session, run the feature readiness summaries for all five horizons, inspect rejection causes and actual entry delays, and only afterward begin validation-only baseline training.

## EXP-016 — Holdout-blind directional baseline laboratory

- Date: 2026-10-02
- Code baseline: local EXP-015; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16).
- Hypothesis: fixed, transparent directional rules can establish the minimum validation benchmark that any future ML challenger must beat while making final-holdout access structurally unavailable.
- Data window: deterministic 240-row simulations with 310-second spacing—one deliberately injected equal-market relationship and one null-pattern market feature. No real market, final-holdout or forward-demo observations were evaluated.
- Target horizons: plumbing validation used 300 seconds; the CLI evaluates each supported 30/60/120/180/300-second dataset independently.
- Method: preregistered seven-rule registry (train majority; target momentum at 15/30/60 seconds; equal-market momentum at 15/30/60 seconds); validation-only selection; minimum validation-signal gate; supplied net payout assumption; opaque holdout metadata only; no probability output and no promotion.
- Metrics: binary-label coverage, accuracy, Wilson 95% interval, whether its lower bound exceeds payout break-even, confusion matrix, no-trade/flat counts, payout break-even rate, expected value per unit stake, net units, maximum drawdown and maximum losing streak. The seven-rule family discloses Bonferroni alpha `0.007142857142857143`; this is not treated as a significance claim.
- Result: the injected market feature produced 48/48 correct validation signals, Wilson 95% interval `[0.9259, 1.0]`, and was selected as a development candidate under the explicit synthetic 0.82 payout assumption. The null market feature achieved 24/48 (50%), equal to the majority baseline; the simplicity tie-break therefore retained train-majority, with expected value `-0.09` unit per signal, maximum drawdown `5.14` units and Wilson interval `[0.3639, 0.6361]`. Both are synthetic logic checks, not Crypto IDX evidence. The complete suite passed 89/89 tests, compilation passed and the production credential-literal scan remained zero. One environment-specific Starlette TestClient deprecation warning remains non-failing.
- Decision: retain the baseline laboratory as the required comparison layer. Exact metric ties prefer the earlier, simpler preregistered rule rather than selecting by name. A validation winner is explicitly `NOT_PROMOTED_VALIDATION_ONLY`; Brier score and log loss remain inapplicable because these rules do not emit probabilities. Reject fitting an ML challenger before a real synchronized session satisfies feature-readiness gates.
- Next: run the physical Windows calibration/`--once` gate, collect at least one continuous hour, evaluate all five horizons with the session's displayed payout, and inspect whether any fixed baseline has enough signals and a Wilson interval compatible with positive payout-adjusted performance. Keep each final holdout sealed.

## EXP-017 — Actual-label-time purging and inner walk-forward stability

- Date: 2026-10-02
- Code baseline: local EXP-016; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16).
- Hypothesis: expanding-window folds confined to the training partition can expose regime instability that a single validation score hides, but only if purging uses the actual outcome-observation time rather than nominal anchor plus horizon.
- Data window: two deterministic 240-row synthetic sequences with 310-second spacing: one stable injected equal-market relationship and one relationship reversed after row 100. No real market, outer-validation, final-holdout or forward-demo observations were scored.
- Target horizon: 300 seconds for the plumbing/adversarial simulation. The implementation remains horizon-separated and supports 30/60/120/180/300 seconds.
- Method: four expanding-window folds inside the outer training partition only; minimum 60 purged training rows and 20 test signals; training rows are eligible only when both their configured embargo and actual label observation (post-decision entry + horizon + exit offset) precede the fold test boundary. Outer validation and final holdout remain unevaluated.
- Metrics: fold selection, accuracy, payout EV, positive-EV folds, Wilson-support folds, minimum/maximum fold accuracy, worst-fold drawdown and maximum losing streak.
- Result: the stable injected market rule won 4/4 folds with 84/84 signals correct, mean/minimum accuracy `1.0`, mean/minimum EV `+0.82`, four Wilson-support folds, zero drawdown and zero losing streak. Under the regime reversal, its fold accuracies were `1.0`, `0.9048`, `0.0`, `0.0`; mean accuracy fell to `0.4762`, mean EV to `-0.1333`, positive-EV folds to 2/4, worst-fold drawdown to `21.0` units and maximum losing streak to 21. The last two folds selected train-majority instead. This is an intentional synthetic stress test, not market evidence. The complete suite passed 93/93 tests, compilation passed, CLI startup/help passed and the production credential-literal scan remained zero; one non-failing Starlette deprecation warning remains.
- Discrepancy/fix: the previous feature split purged with `anchor + horizon`, which can be too early when the executable entry occurs after the decision. The split and walk-forward engine now purge using the later of configured embargo end and actual label-observation time; an adversarial delayed-entry regression test covers the failure.
- Decision: retain inner walk-forward as a mandatory stability screen before outer validation. Reject any candidate justified only by aggregate accuracy or one favorable regime. No candidate is promoted, and no probability/calibration metric is claimed.
- Next: perform physical Windows calibration and one real synchronized capture, collect at least one continuous hour, then run readiness, inner walk-forward and outer validation separately for each horizon using the observed payout. Keep final holdouts sealed.

## EXP-018 — Fail-closed synchronized-session readiness audit

- Date: 2026-10-02
- Code baseline: local EXP-017; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16).
- Hypothesis: a pre-training audit can prevent premature modeling by requiring a complete, continuous, quality-screened five-market session, an explicit observed payout and enough purged rows for every horizon.
- Data window: deterministic five-second synchronized sessions plus the current local database. The passing simulation used 721 Crypto IDX observations and 3,605 reference observations; fragmented, empty and missing-payout variants were tested. The local database contained zero observations. No final holdout or forward-demo results were evaluated.
- Target horizons: readiness evaluated independently for 30/60/120/180/300 seconds.
- Method: verify unique target capture IDs, complete BTC/ETH/BNB/SOL/XRP membership, acceptable target quality, optional collection-attempt acceptance rate, one-hour continuity, cadence gaps, causal feature coverage, minimum feature rows, actual-label-time-purged train/validation sizes, sealed holdout metadata and an operator-supplied payout visibly observed for that session. The CLI is read-only and refuses a nonexistent database.
- Metrics: target/capture counts, capture completeness, acceptable-target rate, session duration, longest continuous span, median/p95/maximum gap, attempt acceptance/rejections, per-horizon rows/coverage/rejection causes, purged split sizes and blocking reasons.
- Result: the 721-capture continuous simulation passed all five horizon gates with 100% five-market capture completeness, 100% acceptable targets, exactly 3,600 seconds of continuous coverage and five-second median/p95/maximum gaps. Rows and purged train/validation/holdout counts were: 30s `702 / 415 / 134 / 141`; 60s `696 / 405 / 127 / 140`; 120s `684 / 386 / 113 / 137`; 180s `672 / 367 / 98 / 135`; 300s `648 / 328 / 70 / 130`. Missing payout, a split session and an empty database failed closed. The first supposedly sufficient 400-capture simulation was correctly rejected for the 300-second horizon: only five validation rows survived the 300-second embargo, below the 30-row gate. This supports retaining the full-hour requirement. The current local database reported `NOT_READY` with zero observations; it is not real collected evidence. The complete suite passed 97/97 tests, compilation passed and the production credential-literal scan remained zero; one non-failing Starlette deprecation warning remains.
- Decision: require readiness status `READY_FOR_BASELINE_RESEARCH` before baseline or challenger evaluation. The gate never authorizes model training or promotion by itself. Reject silent 0.82 payout defaults; payout must be supplied from the visible session. News, calibration and ML remain downstream.
- Next: run Windows calibration and `--once`, then collect one uninterrupted hour while noting the displayed payout. Run `readiness_cli.py`; only if all five horizons pass should inner walk-forward and outer validation begin.

## EXP-019 — Clean Windows V2 deployment package

- Date: 2026-10-03
- Code baseline: local EXP-018; the public GitHub repository remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16) and is not the V2 source of truth.
- Hypothesis: a whitelist-built, self-testing Windows package can move the current evidence collector onto Ankit's present laptop without importing obsolete code, credentials, screen images, databases or execution automation.
- Data window: packaging and deterministic regression tests only. Five user-provided demo screenshots informed the observed 83% session-payout example and desktop workflow, but no image is included in the archive and no screenshot was used as a model sample. No real synchronized observations, final holdout or forward-demo outcomes were evaluated.
- Target horizons: deployment supports separate 30/60/120/180/300-second research horizons; none was scored in this experiment.
- Method: add a PowerShell bootstrap, fail-closed prerequisite checks, explicit bounded-region calibration instructions, a one-capture gate, one-hour collection/readiness procedure, payout break-even calculation, privacy guidance and a release whitelist. Extract the finished ZIP into a clean directory, compile it, start each CLI help command and rerun the complete test suite from the extracted copy.
- Metrics: archive membership, forbidden-file count, credential-literal scan, compile status, CLI startup status and complete extracted-package test result.
- Result: the clean staged copy contained 52 source/config/documentation/test files and zero forbidden image, database, environment, private-key or credential-bearing production files. Compilation passed; `collector_cli.py`, `readiness_cli.py`, `baseline_cli.py` and `relationship_cli.py` all started and returned help; the complete suite passed 97/97 tests from the isolated staged copy in 1.916 seconds. One known non-failing Starlette TestClient deprecation warning remains. The release ZIP was then rebuilt from the verified whitelist and checked again after extraction.
- Decision: release the package for physical Windows demo calibration. This is deployment readiness only: no real synchronized dataset, calibrated probability, champion, forward accuracy or profitability evidence exists, and model state is unchanged.
- Next: perform physical calibration on the current Windows laptop, run one synchronized `--once` capture, inspect latency/offset/quality, and only then collect the first uninterrupted one-hour demo session with the observed payout.

## EXP-020 — Per-frame payout OCR and session-consistency gate

- Date: 2026-10-03
- Code baseline: local EXP-019; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16).
- Hypothesis: reading the displayed Crypto IDX payout from every target frame and requiring one consistent session value prevents payout-adjusted expected value from using a stale manual default or hiding an intraday payout change.
- Data window: nine previously retained 1731×800 desktop demo frames showing Crypto IDX at 82%, plus deterministic parser, collector and readiness simulations at 82%/83%. The newer user screenshots visually showed 83%, but were not added to the package or model data. No final holdout or forward-demo result was evaluated.
- Target horizons: payout consistency applies independently before 30/60/120/180/300-second baseline research; no horizon was scored.
- Method: OCR only the fixed top instrument-tab crop; require the `Crypto IDX` identity and exactly one 1–100% candidate; attach the ratio to each atomic target/reference capture; derive readiness payout only when all observed values are consistent; fail closed on missing, changing or operator-mismatched payout evidence.
- Metrics: real-frame OCR coverage and exactness, temporary-crop privacy, atomic metadata round trip, mismatch/change rejection and complete test-suite status.
- Result: all nine retained real desktop frames were read as exactly 82%, with `Crypto IDX` present in every OCR result (9/9 coverage, zero errors). Synthetic 83% parsing passed. Consistent screen payout was accepted without a manual default; payout changes and an 82%-versus-83% operator mismatch were rejected for all horizons. Compilation and all four CLI help checks passed; the complete suite passed 104/104 tests in 3.730 seconds. One known non-failing Starlette TestClient deprecation warning remains.
- Decision: retain per-frame payout evidence and the consistency gate. Reject a global 82%/83% default and reject averaging across payout regimes. No model, confidence or trading action is produced.
- Next: physically calibrate against the current laptop's 83% layout, confirm calibration and `--once` report `0.83`, and inspect timing quality before starting the one-hour demo session.

## EXP-021 — Optional majority-opinion evidence capture

- Date: 2026-10-03
- Code baseline: local EXP-020; GitHub remains at its sole commit `c16dc1a45df890119580515101884524ea1f0164` (2025-10-16).
- Hypothesis: preserving the visible Binomo up/down majority opinion at decision time may support a later incremental-value test, while treating it as optional metadata avoids sacrificing core synchronized market evidence when this nonessential OCR field is unavailable.
- Data window: nine retained 1731×800 desktop demo frames. Observed up/down values were 45/55, 45/55, 46/54, 45/55, 66/34, 67/33, 64/36, 74/26 and 46/54. No model dataset, final holdout or forward-demo outcome was used.
- Target horizons: metadata collection only; no 30/60/120/180/300-second score was computed.
- Method: OCR only the fixed majority-opinion panel; require the visible label, exactly two percentages in left/up then right/down order, bounded values and an exact 100% sum. Store valid ratios in target metadata. On OCR failure store null values plus `majority_opinion_unavailable`, without rejecting valid price/time/payout/reference evidence. Do not add the field to the frozen feature schema or candidate registry.
- Metrics: real-frame OCR coverage, value conservation, ordering, failure isolation, privacy behavior and complete test-suite status.
- Result: real-frame extraction was correct and internally consistent for all 9/9 frames, spanning up ratios from 45% to 74%. Parser tests rejected missing labels and 99% totals. The optional-failure test retained the core capture and recorded the explicit reason. Compilation and four CLI help checks passed; the complete suite passed 108/108 tests in 6.317 seconds. One known non-failing Starlette TestClient deprecation warning remains.
- Decision: retain as exploratory evidence only. Reject immediate use as a predictor because platform crowd composition, update timing and incremental value are unverified; news and ML remain deferred.
- Next: verify current-laptop calibration reads the visually observed 83% payout and a plausible majority split, then run one synchronized capture. After a real continuous session exists, preregister a separate challenger that tests whether majority opinion adds out-of-sample value beyond market-only baselines.
