# Treble Tracker

## A Complete Technical Specification for a Free, Open Institutional Finance Workstation

*Product proposal, July 2026. Every component named in this document is either free-tier, open source, public-domain data, or an open standard. No line item in this specification requires a commercial licence.*

---

## Table of Contents

1. [What Treble Tracker Is](#1-what-treble-tracker-is)
2. [The Zero-Cost Architecture](#2-the-zero-cost-architecture)
3. [The Input Layer](#3-the-input-layer)
4. [Access Surfaces](#4-access-surfaces)
5. [The Command Grammar](#5-the-command-grammar)
6. [Front-End Display Mechanics](#6-front-end-display-mechanics)
7. [The Function Library](#7-the-function-library)
8. [Data Architecture and Plumbing](#8-data-architecture-and-plumbing)
9. [Security Identifiers and the Security Master](#9-security-identifiers-and-the-security-master)
10. [Analytics Internals — Fixed Income](#10-analytics-internals--fixed-income)
11. [Analytics Internals — Curves and Volatility](#11-analytics-internals--curves-and-volatility)
12. [Analytics Internals — Derivatives and DLIB](#12-analytics-internals--derivatives-and-dlib)
13. [Analytics Internals — Credit](#13-analytics-internals--credit)
14. [Analytics Internals — Equities](#14-analytics-internals--equities)
15. [TVAL: Evaluated Pricing](#15-tval-evaluated-pricing)
16. [PORT and the TFM3 Risk Models](#16-port-and-the-tfm3-risk-models)
17. [News, Research and NLP](#17-news-research-and-nlp)
18. [Trading and Order Management](#18-trading-and-order-management)
19. [Communications and Compliance](#19-communications-and-compliance)
20. [The AI Layer](#20-the-ai-layer)
21. [Enterprise Components](#21-enterprise-components)
22. [Security and Operations](#22-security-and-operations)
23. [Design Tradeoffs and Roadmap](#23-design-tradeoffs-and-roadmap)
24. [Mnemonic Glossary](#24-mnemonic-glossary)

---

## 1. What Treble Tracker Is

Treble Tracker is a **vertically integrated financial workstation**, delivered free, in which every layer of the stack is owned, open, or public:

- the **input layer** (a keyboard remapping profile, optional open-hardware keyboard, passkey authentication)
- the **network** (public internet, WebSocket and Arrow Flight transports, self-hostable or community-hosted nodes)
- the **client** (a native desktop application with a panel-based, keyboard-first interface)
- the **data acquisition layer** (regulatory filings, central bank and statistical agency releases, exchange public feeds, free-tier vendor APIs, and a contributed-data network)
- the **normalisation and storage layer** (a security master, a real-time ticker plant, a columnar historical archive)
- the **analytics layer** (curve construction, bond math, option pricing, factor risk, evaluated pricing, backtesting)
- the **presentation layer** (a library of named function screens addressed by mnemonic)
- the **transaction layer** (order and execution management over FIX, RFQ workflow, transaction cost analysis)
- the **communications layer** (a federated, identity-verified messaging fabric with a professional directory)
- the **content layer** (aggregated news, generated research dashboards, macro forecasting)

The strategic point is the same as any integrated stack: **no single layer carries the value.** The value is that one identifier resolves across news, pricing, analytics, chat, and execution without an integration project. Treble Tracker's specific claim is that this coherence can be achieved on entirely free inputs — because the binding constraint was never the data, it was the engineering discipline to normalise it.

### Design philosophy

Three principles govern every decision in this specification.

**1. Optimise for the expert, but do not lock out the novice.** Density is a feature. A screen showing 200 numbers beats four screens showing 50, because a trained eye saccades faster than a mouse clicks. Treble Tracker does not hide information behind progressive disclosure. It does, however, ship a discoverable command palette and natural-language fallback so that a new user is never stuck — the expert path and the beginner path coexist rather than one replacing the other.

**2. Keyboard over mouse, always.** Every action has a keystroke path. The mouse is supported but never required. This is a latency argument. A user who must reach for a mouse loses hundreds of milliseconds per action and performs thousands of actions per day.

**3. Never break a workflow.** Once a function exists and users have built muscle memory around it, it does not get redesigned out from under them. New capability is added *beside* old capability. Deprecation happens on a published multi-year clock with a compatibility shim, never silently.

### Target scale

These are the design targets the architecture is sized for, not day-one figures.

| Dimension | Target |
|---|---|
| Instruments in the security master | 30m+ (equities, bonds, funds, derivatives, FX, commodities, indices, crypto) |
| Real-time / near-real-time instruments in the ticker plant | 5m+ |
| Public data sources ingested | 200+ (regulators, central banks, statistical agencies, exchanges, free-tier APIs) |
| Contributed sources | Open network — any participant may contribute quotes |
| Named functions | 800+ at v1, extensible by plugin |
| Data fields in the dictionary | 15,000+ |
| Securities with TVAL evaluated prices | 1m+ (fixed income focus) |
| TFM3 risk factors | 1,500+ |
| Documents in the research corpus | 50m+ (filings, transcripts, prospectuses, releases) |
| News items ingested per day | 100,000+ across all sources |

---

## 2. The Zero-Cost Architecture

Building this for free is a constraint that shapes the architecture, not a marketing claim bolted onto a normal design. Four decisions make it work.

### 2.1 Public data is the primary source, not the fallback

Regulators and central banks publish, for free and under open terms, the majority of what a financial workstation needs. The industry pays for *normalisation and convenience*, not for the underlying facts. Treble Tracker's position is that normalisation is a solvable engineering problem, done once, shared by everyone.

| Domain | Free primary source |
|---|---|
| US company fundamentals | SEC EDGAR — XBRL Company Facts API, Financial Statement Data Sets, full-text search |
| US corporate & agency bond trades | FINRA TRACE — daily and historical academic/enhanced files, plus the public dissemination feed |
| US municipal bonds | MSRB EMMA — trades, disclosures, official statements |
| US Treasuries | US Treasury Fiscal Data API — daily yield curve, auction results, debt outstanding |
| Money markets & repo | Federal Reserve Bank of New York — SOFR, EFFR, OBFR, TGCR, BGCR, primary dealer statistics |
| OTC derivatives | DTCC Swap Data Repository public price dissemination; CFTC-mandated real-time public reporting |
| Futures & options settlements | CME, ICE, Eurex, CBOE daily settlement and volume/OI files |
| Equity market data | IEX DEEP/TOPS (free real-time depth), exchange delayed feeds, free-tier APIs (Alpaca, Tiingo, Finnhub, Twelve Data, Polygon free tier) |
| Fund holdings | SEC N-PORT, N-CEN, 13F, Form 4/5 |
| Macro & economics | FRED, BLS, BEA, Census, Eurostat, ECB SDW, BoE, BoJ, BIS, IMF, World Bank, OECD |
| Energy & commodities | EIA, USDA NASS/WASDE, Baker Hughes rig counts, CFTC Commitments of Traders |
| Legal entities | GLEIF (LEI, full open data including relationship records), OpenCorporates, Companies House, SEC CIK |
| Instrument identifiers | OpenFIGI (ANSI X9.145, free and openly redistributable) |
| European instrument reference | ESMA FIRDS and FITRS — full reference and transparency data |
| Credit ratings | Rating agency public press releases and EDGAR NRSRO Form NRSRO exhibits; ESMA CEREP |
| Corporate events | EDGAR 8-K, exchange notices, company IR feeds |
| News | RSS/Atom from ~5,000 publishers, GDELT, regulator wires, company IR |
| Reference geography/industry | NAICS, SIC, ISIC, NACE (all public); Wikidata for entity enrichment |

### 2.2 The contributed-data network

The single hardest gap in free data is **OTC price discovery** — corporate bonds, loans, and structured products where no exchange exists and the only prices are dealer quotes. Treble Tracker solves this the way the OTC market itself solved it: a two-sided network.

- Any participant may **contribute** indicative or executable quotes, axes, and runs.
- Contributors receive **distribution reach** — their prices appear on every user's `ALLQ` screen, attributed.
- Contributors receive **flow intelligence** — anonymised, aggregated statistics on who viewed and acted on their levels.
- The cost of participation is zero; the incentive is the same one that has sustained dealer contribution networks for forty years.

Quotes are quality-scored (§15) so that a thin contributor network degrades gracefully rather than producing confidently wrong prices.

### 2.3 The open-source quant stack

Every model described in sections 10–16 is implemented on free libraries. Nothing here requires a commercial numerical library.

| Layer | Free implementation |
|---|---|
| Core derivatives & fixed income analytics | **QuantLib** (BSD-style) — curve bootstrapping, day counts, calendars, bond math, OAS lattices, swap pricing, option engines, credit curves. The single most important dependency. |
| Alternative/cross-check engine | **OpenGamma Strata** (Apache 2.0, Java) — independent implementation for validating QuantLib output |
| Numerics | NumPy, SciPy, `numba`, `mpmath` for high-precision root-finding |
| Dataframes & columnar compute | **Polars**, **DuckDB**, **Apache Arrow** |
| Optimisation | **CVXPY** + **OSQP**/**Clarabel**/**SCS** (convex), **HiGHS** (LP/MIP), **Ipopt** (nonlinear) |
| Portfolio construction | **PyPortfolioOpt**, **Riskfolio-Lib**, **skfolio** |
| Statistics & econometrics | **statsmodels**, **arch** (GARCH family), **linearmodels** (panel/Fama-MacBeth) |
| Machine learning | **scikit-learn**, **LightGBM**, **XGBoost**, **PyTorch** |
| Volatility surfaces | QuantLib SABR + **py_vollib** + open SVI implementations |
| Monte Carlo | QuantLib MC engines, SciPy `qmc` (Sobol, Halton) |
| Backtesting | **vectorbt**, **bt**, custom point-in-time engine |
| Time series storage | **TimescaleDB** (Postgres extension), **ClickHouse** (Apache 2.0), Parquet on object storage |
| Streaming | **Redpanda** (BSL/free) or **Apache Kafka**, **NATS JetStream** for low-latency fanout |
| Transport | **Apache Arrow Flight** (columnar, zero-copy) for bulk; WebSocket for streaming ticks |
| Search | **OpenSearch** or **Meilisearch** (lexical), **Qdrant** / **pgvector** / **LanceDB** (vector) |
| Orchestration | **Dagster** or **Apache Airflow** |
| Observability | **Prometheus** + **Grafana** + **OpenTelemetry** |
| Charting | **Lightweight Charts**, **Apache ECharts**, **uPlot**, **Perspective** (FINOS) |
| Interop | **FDC3** (FINOS) for cross-application window linking |
| Desktop shell | **Tauri** (Rust + system webview — small binary, low memory) |
| Terminal/TUI client | **Textual** for a pure-keyboard, SSH-able variant |
| Messaging | **Matrix** (Synapse or Conduit server, Element or custom client) |
| Identity | **Keycloak** (OIDC), **WebAuthn/FIDO2** passkeys |
| Compliance storage | **MinIO** with object-lock WORM, or S3 Object Lock |
| Notebooks | **JupyterLab** |
| Execution connectivity | **QuickFIX** / **quickfix-go** for FIX 4.2/4.4/5.0 sessions |
| Spreadsheet bridge | **xlwings** (BSD) for Excel; native for **LibreOffice Calc**; Office.js web add-in |

### 2.4 Federated hosting

Treble Tracker is designed to run in three modes, all free:

- **Self-hosted** — a firm runs the whole stack inside its own perimeter. Data never leaves. This is the mode regulated institutions will use.
- **Community-hosted** — shared public nodes, funded by donation and infrastructure grants, for individuals and small teams. Ingest and normalisation are done once and shared, which is where the economies of scale live.
- **Local-only** — a single-machine install using DuckDB and Parquet, no server. Fully functional for research and analysis; loses the real-time and messaging layers.

The federation protocol means a self-hosted node can subscribe to the community node's normalised reference data while keeping its own positions, orders, and communications private.

---

## 3. The Input Layer

### 3.1 The Treble Keymap

The workstation's speed comes from a semantic keyboard layout. Treble Tracker ships this as a **free remapping profile** rather than requiring hardware — it works on any keyboard, with optional printed keycap overlays and an open-hardware reference design (QMK/VIA firmware, published PCB and case files) for those who want the physical version.

**Colour-coded key groups (physical or on-screen legend):**

| Colour | Function group | Keys |
|---|---|---|
| **Red** | Cancel / exit / escape | `CANCEL`, `ESC` |
| **Green** | Action / execute | `GO`, `MENU`, `PRINT`, `PAGE FWD`, `PAGE BACK` |
| **Yellow** | Market sector / asset class | `GOVT`, `CORP`, `MTGE`, `MUNI`, `M-MKT`, `PFD`, `EQUITY`, `CMDTY`, `INDEX`, `CRNCY`, `CRYPTO`, `CLIENT` |
| **Blue** | Navigation and utility | `NEWS`, `HELP`, `LINK`, `PANEL` |

The **yellow keys are the semantic heart of the input model.** A ticker alone is ambiguous — `IBM` could be the equity, a bond, a CDS, or an option. The yellow key **selects the asset-class namespace**. `IBM <EQUITY> DES <GO>` and `IBM <CORP> DES <GO>` resolve to entirely different objects.

On a standard keyboard the yellow keys map to `F1`–`F12` with an on-screen legend strip; on the reference hardware they are physical, colour-moulded keys.

**Other input features:**

- `<HELP>` pressed **once** opens context-sensitive documentation for the current screen, generated from the same source of truth as the function's own metadata.
- `<HELP>` pressed **twice** opens support: a community help channel plus an AI assistant with full context on the current screen state. For self-hosted institutional deployments this routes to the firm's own support desk.
- Dedicated `<PANEL>` keys cycle among panels.
- `<LINK>` assigns the current panel to a colour link group (§5).

### 3.2 TKey — authentication

Authentication is two-factor and free, with three tiers of hardware:

- **Passkey (default).** WebAuthn/FIDO2 via the platform authenticator already on the user's device — Touch ID, Windows Hello, Android biometrics. Phishing-resistant, hardware-backed, zero cost, no enrolment friction.
- **TKey Mobile.** A free companion app implementing an **optical challenge/response**: the client modulates a flashing region on screen; the phone's camera reads the session challenge; the app combines it with a device secret and a local biometric check and returns a short one-time code. This requires no network connection on the phone, no pairing, and no synchronised clock — which means it works on a locked-down machine, an air-gapped terminal, or a borrowed laptop.
- **Hardware security key.** Any FIDO2 key (YubiKey, SoloKey, Nitrokey, or the open Treble reference design).

Because identity is bound to a person rather than a device, a user's entire environment — layouts, watchlists, alerts, saved screens, chat history — follows them to any machine. This is **Treble Anywhere**, and it is the default rather than an add-on.

### 3.3 Displays and the panel model

No proprietary monitors. The client targets:

- **Four-panel classic layout** — two panels per screen on a dual-monitor arrangement, each panel an independent session with its own command line, history, and state. Panels switch by keyboard.
- **Arbitrary window mode** — any number of floating windows across any number of displays, for Canvas layouts (§5).
- **Single-panel / laptop mode** — a compressed layout that keeps the command grammar intact on one screen.
- **TUI mode** — the same command grammar over SSH in a terminal emulator, for headless and low-bandwidth access.

The four-panel mental model persists as the default because it matches how the work is actually done: a description, a chart, a news stream, and a conversation, held simultaneously.

---

## 4. Access Surfaces

| Surface | Description | Notes |
|---|---|---|
| **Desktop client** | Tauri-based native app, Windows/macOS/Linux | Full function library, lowest latency, panels and Canvas |
| **Treble Anywhere** | Same client, any machine, TKey login | Identical entitlements; environment follows the person |
| **Mobile app** | iOS/Android | News, watchlists, `DES`, `GP`, `IM` chat, alerts, `PORT` summary views |
| **TUI** | Textual-based terminal client over SSH | Full command grammar, text-mode charts, no graphical panes |
| **Spreadsheet add-in** | Excel (xlwings/Office.js), LibreOffice Calc, Google Sheets | `=TDP()`, `=TDH()`, `=TDS()`, `=TQL()` |
| **TAPI** | Python, Rust, Java, C++, JavaScript, R clients | The programmatic surface (§8.3) |
| **Web** | Browser client, subset of functions | Same rendering engine compiled to WASM |
| **Notebook** | JupyterLab with `treble` and `tql` libraries pre-wired | `TQNT` (§4.2) |

### 4.1 The spreadsheet add-in

The spreadsheet bridge is where most data actually gets consumed, so it is a first-class surface, not an afterthought.

```
=TDP("IBM US Equity", "PX_LAST")
    Treble Data Point — a single current value.

=TDH("IBM US Equity", "PX_LAST", "1/1/2024", "12/31/2024", "Per=D", "Fill=P")
    Treble Data History — a time series, returns a spilled range.

=TDS("IBM US Equity", "DVD_HIST")
    Treble Data Set — bulk/multi-row reference data (dividend history,
    holders, capital structure, index members).

=TDS("SPX Index", "INDX_MWEIGHT_HIST", "END_DATE_OVERRIDE=20240630")
    With an override — the override mechanism parameterises how a
    field is computed.

=TQL("get(px_last) for(members('SPX Index')) with(dates=range(-1Y,0D))")
    Treble Query Language — pushes universe selection, aggregation and
    calculation server-side rather than pulling raw data down.
```

**Overrides** are the crucial concept. Almost every analytic field accepts overrides that change model inputs — requesting an option-adjusted spread while overriding the volatility assumption, the prepayment model, the settlement date, or the discount curve. Overrides are how a single field name (`OAS_SPREAD_MID`) is evaluated under arbitrary assumptions from a spreadsheet cell. **This is the mechanism by which the entire analytics library is exposed as data.**

Unlike closed workstations, Treble Tracker places **no redistribution restriction and no rate limit** on the spreadsheet or API surfaces. The data is public or contributed; there is nothing to meter. A firm may pull the entire universe into its own warehouse, and the architecture actively supports this (§8.5).

### 4.2 TQL and TQNT

- **TQL (Treble Query Language)** — a declarative language over the data graph, expressing universe selection, field retrieval, and computation in one server-side statement. It compiles to a DuckDB/ClickHouse execution plan plus analytics-engine calls, so a screen over 100,000 securities with computed fields runs where the data is rather than round-tripping.

```
get(
  px_last,
  oas_spread_mid(vol_override=0.20),
  dur_adj_oas
)
for(
  bonds(issuer_ticker='IBM', currency='USD', amt_outstanding > 250e6)
)
with(dates=range(-1Y, 0D), fill='prev')
```

- **TQNT** — a JupyterLab environment inside the client, with the `treble` and `tql` Python libraries pre-wired and identity-aware. Notebooks can be published as functions: a quant writes analysis in Python, tags it with a mnemonic, and colleagues invoke it from the command line like any built-in screen. This is the primary extensibility mechanism and the reason the function library can grow without central development.

---

## 5. The Command Grammar

The command line is a small formal language. Fluency in it is the difference between a novice and an expert user.

### 5.1 Canonical form

```
[SECURITY] [<YELLOW KEY>] [FUNCTION] [<GO>]
```

Examples:

```
IBM US <EQUITY> DES <GO>          Description of IBM common stock
IBM US <EQUITY> GP <GO>           Price graph
IBM 4.15 05/15/39 <CORP> YAS <GO> Yield and spread analysis of a bond
FNCL 5.5 <MTGE> <GO>              Agency conventional 5.5% TBA
EURUSD <CRNCY> FXFC <GO>          FX forecasts
CL1 <CMDTY> GP <GO>               Front WTI crude future
SPX <INDEX> MEMB <GO>             Index members
```

### 5.2 Grammar rules

- **`<GO>` is the execute token.** Nothing happens until `<GO>`. The command line is editable state until submitted — a deliberate commit step.
- **Function alone** — `WEI <GO>` with no security launches the world equity index monitor. Functions needing no security are "global".
- **Security alone** — `IBM US <EQUITY> <GO>` opens the default menu for that security, from which every applicable function is browsable.
- **`<MENU>`** steps back up one level, like a parent directory.
- **Numbered menus.** Every menu numbers its options; typing the number and `<GO>` selects it. Keyboard-only navigation of the entire tree.
- **Autocomplete.** Fuzzy matching as you type, showing candidate functions and securities with inline previews. The command line is a search box as much as a parser.
- **Chained arguments.** `HP IBM US Equity 1/1/24 12/31/24 <GO>`.
- **Plain-English fallback.** Any input the parser cannot resolve as a command is routed to `ASK` (§20), which interprets it and either executes the intended function or explains what it would do. A user never hits a dead end, and the response shows the mnemonic that would have been faster — the system teaches its own grammar.

### 5.3 Panels, tabs, and Canvas

**Panels** are the four classic windows. **Canvas (`CNVS <GO>`)** is the free-form alternative: a component workspace where the user places monitors, charts, news windows, and chat anywhere across any number of screens, and **links them by colour group**. Selecting a security in a red-linked monitor updates every other red-linked component instantly.

Canvas is built on **FDC3**, the open financial desktop interoperability standard. This has a consequence worth stating plainly: **third-party and in-house applications join the same link groups.** A firm's own proprietary pricing tool, dropped onto the Canvas, participates in context propagation exactly like a native component. No closed workstation offers this, because interop undermines lock-in — which is precisely why an open product should lead with it.

Canvas layouts are saved per user and follow them via Treble Anywhere.

### 5.4 Screen conventions

| Element | Meaning |
|---|---|
| Amber/orange text | Static label or non-editable reference data |
| White/bright field | Editable input — click or tab into it |
| Cyan or underlined text | Drillable link to another function or security |
| `N` prefix on a number | Net change |
| `<GO>` in a screen legend | That row is executable |
| Tabs across the top | Sub-views within one function |
| Actions toolbar | Export, print, chart settings, alert creation, notebook hand-off |
| `«` `»` | Page navigation within a paginated result set |
| Dotted underline | Value is model-derived; hover or `<HELP>` shows the inputs |

That last convention is a Treble-specific addition and a load-bearing one. **Every number on every screen is traceable.** Any model output can be expanded to show its inputs, the model used, and the parameters — down to the source document or tick. Open analytics are only credible if they are auditable, and auditability is designed in rather than retrofitted.

### 5.5 Alerts

`ALRT <GO>` manages alerts on:

- price levels, percentage moves, volume thresholds, spread levels
- news matching a topic code, ticker, or keyword
- economic releases relative to consensus
- credit rating actions and outlook changes
- filings (8-K, 10-Q, N-PORT, Form 4, prospectuses)
- arbitrary `TQL` expressions evaluated on a schedule or on tick

Delivery is to the client, mobile push, email, webhook, a Canvas component, or a Matrix room. Webhook delivery matters: it makes Treble Tracker a monitoring engine other systems can build on.

---

## 6. Front-End Display Mechanics

### 6.1 The rendering model

The client is not a general-purpose browser and does not use standard OS widget toolkits for its core screens. The rendering model is a **cell grid with graphical extensions**:

- Screens are laid out on a **grid of character cells** with attributes (foreground colour, background colour, intensity, blink, link target). This is why alignment is perfect across every screen, why layouts compose predictably, and why the same screen definition renders identically in the desktop client, the web client, and the TUI.
- A screen is defined declaratively — a layout specification plus field bindings — and the *same definition* drives all three renderers. Adding a function does not mean writing three UIs.
- **Blink is a real, used attribute.** A flashing field means the value updated within the last tick interval.
- **Graphical panes** (charts, heatmaps, treemaps, surfaces, network graphs) composite into rectangular regions of the same grid, rendered via WebGL/wgpu.
- The client maintains a **local screen buffer per panel** and applies deltas pushed from the server. Only changed cells repaint. This is a bandwidth optimisation that keeps the client fully usable on hotel Wi-Fi, tethered mobile, or a satellite link — conditions under which a conventional web app is unusable.

### 6.2 Update semantics and conflation

- **Display feeds are conflated.** If an instrument ticks 500 times a second and the screen refreshes at a few hertz, the user sees the latest value. Human eyes cannot consume 500 updates per second and pushing them wastes bandwidth.
- **TPIPE delivers unconflated full-tick** to machine consumers (§8.4). This is the human-facing / machine-facing split, and unlike closed products it is a technical distinction rather than a commercial one — both are free.
- **Conflation is adaptive.** On a degraded link the server reduces update frequency rather than queueing, so the user always sees current values instead of a growing backlog.
- **Tick history** (`TAQ`, `QR`) reconstructs the full unconflated record from the archive, so every tick is available on demand even though it is not streamed to the display.

### 6.3 Colour semantics

| Colour | Meaning |
|---|---|
| Amber / orange | Labels, static reference data, headers |
| White | Editable inputs; neutral values |
| Green | Positive change, bid side, buy |
| Red | Negative change, ask/offer side, sell, breaking news banner |
| Cyan / light blue | Hyperlink, drillable security, cross-reference |
| Yellow | Highlighted/selected row, warnings |
| Magenta | Secondary chart series, user annotations |
| Grey | Stale or unavailable data |

**Stale-data indication is mandatory.** Any value the system knows is not current is visually distinguished and carries a timestamp and source on hover. Functions consuming a stale or evaluated price footnote it. Given that Treble Tracker mixes real-time, delayed, end-of-day, and model-derived prices from many free sources, being unambiguous about provenance is not a nicety — it is the difference between a usable professional tool and a liability.

Colour schemes are themeable, including high-contrast and colour-blind-safe palettes (green/red replaced by blue/orange with shape encoding). The semantics are preserved across themes.

### 6.4 Charting engine

`GP` (Graph Price) and its family provide:

- **Study library**: SMA/EMA/WMA/VWMA, Bollinger bands, RSI, MACD, stochastics, ATR, Ichimoku, DMI/ADX, parabolic SAR, pivot points, Fibonacci retracements/extensions/fans/arcs, Elliott wave annotation, Gann angles, volume profile, market profile (TPO), and more. `TECH <GO>` and `STDY <GO>` list the catalogue. Users add studies as Python plugins.
- **Chart types**: line, OHLC bar, candlestick, Heikin-Ashi, point & figure, Renko, Kagi, three-line break, area, mountain, histogram, footprint.
- **Multi-security overlay and spread charting** — `G #` saved charts, `HS` (historical spread), `COMP` (comparative returns), `SPRD` (spread between arbitrary expressions).
- **Custom expression charting** — chart algebraic combinations of securities and fields: `(IBM US Equity / SPX Index) * 100`, or a bond's spread over an interpolated curve point. Expressions are TQL, so anything TQL can compute can be charted.
- **Event overlays** — earnings, dividends, splits, index adds/deletes, rating changes, filings, and news plotted on the price series.
- **Backtesting hooks** — `BT <GO>` runs rule-based strategy backtests directly off charted studies.

### 6.5 Export and interoperability

Every screen exports via the Actions menu: to Excel/Calc (live-linked or static), CSV, Parquet, JSON, PDF, clipboard, and to a TQNT notebook. "Live-linked" export writes `=TDH()`/`=TDS()` formulas rather than values, so the spreadsheet refreshes.

**Export to notebook** is the distinctive one: any screen can emit the TQL and Python that reproduces it. A user who hits the limits of a point-and-click screen gets the code that generated it and continues in a notebook. There is no cliff between the GUI and the API.

---

## 7. The Function Library

Functions are organised by a taxonomy that is systematic by design rather than accreted. `MENU`, `FCTN <GO>` (function finder), and `TPS <GO>` (product shortcuts) navigate it. Every function is a declarative screen definition plus a resolver, which is why the library is extensible by plugin.

### 7.1 Security-level fundamentals

| Mnemonic | Name | What it does |
|---|---|---|
| `DES` | Description | The canonical security page. Equities: business description, capital structure, key figures, management, ownership. Bonds: coupon, maturity, call schedule, covenants, ratings, issuer, collateral. **The most-used function in the system.** |
| `CN` | Company News | News filtered to the security |
| `FA` | Financial Analysis | Full statements, ratios, segments, common-size, as-reported vs. standardised, 20+ years |
| `FAM` | Financial Analysis Monitor | Cross-company fundamental comparison |
| `ERN` | Earnings | Earnings history, surprises, revisions |
| `EEO` | Earnings Estimates Overview | Consensus by line item |
| `EE` | Earnings Estimates | Estimate distribution, revisions, dispersion |
| `ANR` | Analyst Recommendations | Rating distribution, targets, historical accuracy |
| `EEB` | Estimate Breakdown | Detail by contributing analyst |
| `CACS` | Corporate Actions | Splits, dividends, spin-offs, M&A, rights |
| `DVD` | Dividends | History and forecasts |
| `HDS` | Holders | Institutional and insider ownership |
| `OWN` | Ownership | Detailed breakdown from 13F/N-PORT |
| `SPLC` | Supply Chain | Customers, suppliers, competitors, revenue exposure |
| `RELS` | Related Securities | Every instrument linked to the issuer |
| `DDIS` | Debt Distribution | Maturity ladder of an issuer's debt |
| `CRPR` | Credit Profile | Rating history across agencies |
| `DRSK` | Default Risk | Structural default probability model |
| `CAST` | Capital Structure | Full liability stack with seniority |
| `WACC` | Weighted Average Cost of Capital | Full decomposition, editable inputs |
| `EQRV` | Equity Relative Valuation | Multiples vs. peers, historical z-scores |
| `TI` | Treble Intelligence | Sector research dashboards |
| `ESG` | ESG Analysis | Scores, disclosure, controversies |
| `CO2` | Carbon | Emissions data and intensity |
| `FLDS` | Fields | **Field finder** — searches the data dictionary. Essential for API work. |
| `DOCS` | Documents | Filings, prospectuses, offering circulars, transcripts |
| `SPTR` | Source Trace | **Provenance viewer** — for any field, the exact source document, filing, or tick that produced it |

### 7.2 Pricing, quoting, market structure

| Mnemonic | Name | What it does |
|---|---|---|
| `Q` / `QM` | Quote / Quote Monitor | Live quote |
| `QR` | Quote Recap | Tick-by-tick quote history |
| `GIP` | Intraday Price | Intraday chart |
| `TAQ` | Trade & Quote | Full intraday tick record |
| `ALLQ` | All Quotes | **Every contributor's quote on a bond, side by side** — the core fixed income price discovery screen |
| `TCMP` | Treble Composite | Composite executable price from the contributed network |
| `TGN` | Treble Generic | Composite indicative price |
| `TDH` | Trade History | TRACE/EMMA/SDR reported-trade history |
| `MOST` | Most Active | Movers and volume leaders |
| `IMAP` | Intraday Market Map | Treemap of index constituents by sector |
| `WEI` | World Equity Indices | Global index monitor |
| `BTMM` | Treasury & Money Markets | Rates monitor |
| `MBTR` / `MBWD` | Mortgage monitors | TBA and agency MBS |
| `DEPO` | Deposits | Money market rates |
| `FXIP` | FX Information Portal | FX hub |
| `WCDS` | World CDS | Sovereign and corporate CDS monitor |
| `GC` | General Collateral | Repo rates |
| `TOP` | Top News | Main news screen |
| `NI` | News by topic | `NI FED`, `NI OIL`, `NI M&A` |

### 7.3 Screening and discovery

| Mnemonic | Name | What it does |
|---|---|---|
| `EQS` | Equity Screening | Multi-criteria screener over fundamental, technical, estimate, and ESG fields |
| `SRCH` | Fixed Income Search | Bond universe screener — issuer, rating, maturity, coupon type, currency, covenant, callable, green flag, and hundreds more |
| `FSRC` | Fund Screening | Mutual fund and ETF screener |
| `NIM` | New Issue Monitor | Primary market pipeline |
| `PGM` | Program Monitor | MTN and CP programmes |
| `LEAG` | League Tables | Underwriter and advisor rankings |
| `MA` | Mergers & Acquisitions | Deal database and screening |
| `SECF` | Security Finder | Universal instrument lookup |
| `PSCR` | Portfolio Screening | Screening within held positions |

### 7.4 Economics and macro

| Mnemonic | Name | What it does |
|---|---|---|
| `ECO` | Economic Calendar | Releases with consensus, prior, actual, surprise |
| `ECST` | Economic Statistics | Macro time series browser |
| `ECFC` | Economic Forecasts | Contributor forecast distributions |
| `ECWB` | Economic Workbench | Macro modelling and regression |
| `WIRP` | World Interest Rate Probability | **Market-implied central bank rate path** from OIS and futures |
| `FOMC` | Central bank monitor | Communications, projections, balance sheet |
| `EMOD` | Economic Model | Forecasting models |
| `TRADE` | Trade flows | Bilateral trade and supply chain macro |

Macro series are addressable as tickers: `USGG10YR Index`, `CPI YOY Index`, `SOFRRATE Index`.

### 7.5 Portfolio, risk, performance

| Mnemonic | Name | What it does |
|---|---|---|
| `PRTU` | Portfolio Upload | Create and maintain portfolios |
| `PORT` | Portfolio & Risk Analytics | **The flagship** — attribution, risk decomposition, scenarios, optimisation, characteristics |
| `PSCR` | Portfolio Screening | Screen holdings |
| `PMEN` | Portfolio Menu | Directory of portfolio functions |
| `TIDX` | Treble Indices | Open index construction and analytics |
| `RISK` | Risk System | Front-office risk, XVA, VaR, collateral |
| `SCEN` | Scenario Analysis | Custom shock definition |

### 7.6 Fixed income analytics

| Mnemonic | Name | What it does |
|---|---|---|
| `YAS` | Yield & Spread Analysis | **The canonical bond screen** — price↔yield, all spread measures, all risk measures |
| `YA` | Yield Analysis | Simplified yield calculation |
| `OAS1` | Option-Adjusted Spread | OAS for callable/putable bonds |
| `CSHF` | Cash Flow | Full projected cash flow schedule |
| `SWPM` | Swap Manager | Structure, price, and risk any swap |
| `ICVS` | Interest Rate Curves | Curve construction and display |
| `SWDF` | Swap Curve Defaults | **Curve construction configuration** |
| `FWCM` | Forward Curve Matrix | Implied forward rates |
| `CRVF` | Curve Finder | Catalogue of all curves |
| `TVAL` | Evaluated Pricing | Evaluated price with transparency detail |
| `MTCS` | Mortgage Cash Flows | |
| `MTSP` | Mortgage Spread | |
| `YT` | Yield Table | Yield across prepayment speeds |
| `CLC` | Collateral | Pool composition |
| `HZ` / `HR` | Horizon Analysis | Total return under forward scenarios |
| `FIW` | Fixed Income Worksheet | Multi-bond monitor with live analytics |
| `CDSW` | CDS Valuation | ISDA-standard CDS pricing and hazard bootstrapping |
| `CRVD` | Credit Curve | Issuer curve vs. peers |
| `ASW` | Asset Swap | Asset swap spread calculation |

### 7.7 Derivatives

| Mnemonic | Name | What it does |
|---|---|---|
| `OMON` | Option Monitor | Full option chain with greeks |
| `OSA` | Option Scenario Analysis | Position-level P&L surfaces |
| `OVDV` | Option Volatility Surface | Implied vol surface, term structure, skew |
| `OVME` | Option Valuation (equity) | Price any equity option or structure |
| `OVML` | Option Valuation (FX) | FX options and structures |
| `VCUB` | Volatility Cube | Swaption vol cube |
| `SWPM` | Swap Manager | IRS, basis, cross-currency, inflation, amortising, cancellable |
| `DLIB` | Derivatives Library | **Build arbitrary structured payoffs and price them** |
| `RISK` | Risk System | Portfolio-level derivative risk and XVA |
| `VCON` | Trade Confirmation | Confirmation of OTC trades agreed in chat |
| `SKEW` | Volatility skew | |
| `HVG` | Historical vs implied vol | |
| `FRD` | Forwards | FX forward curve |

### 7.8 Trading and execution

| Mnemonic | Name | What it does |
|---|---|---|
| `EMS` | Execution Management | Order routing to brokers and algos over FIX |
| `PMS` | Portfolio Management System | Buy-side OMS, compliance, IBOR |
| `DESK` | Dealer Book | Sell-side inventory, position, and risk |
| `FXT` | FX Trading | Multi-bank FX RFQ |
| `BOLT` | Order Ticket | Order entry |
| `RFQ` | Request for Quote | Bond RFQ workflow |
| `TCA` | Transaction Cost Analysis | Execution quality measurement |
| `BSKT` | Basket Trading | List and program execution |

### 7.9 Communication and workflow

| Mnemonic | Name | What it does |
|---|---|---|
| `IM` | Instant Message | **The chat system** — 1:1, group, and persistent rooms over Matrix |
| `MSG` | Message | Formal, archived, email-like messaging |
| `PEOP` | People | Directory of verified professionals |
| `DRQ` | Data Request | Report a data problem; creates a public, trackable issue |
| `NOTE` | Notes | Personal and shared research notes |
| `CALN` / `EVTS` | Calendar / Events | Corporate events, conferences, earnings calls |
| `TRAN` | Transcripts | Earnings and event transcripts |
| `ASK` | Ask Treble | Natural-language interface (§20) |
| `TU` | Treble University | Interactive training |
| `TPS` | Product shortcuts | Function directory |

### 7.10 Quant and extensibility

| Mnemonic | Name | What it does |
|---|---|---|
| `TQNT` | Treble Quant | JupyterLab notebook environment |
| `TQL` | Query Language | Interactive TQL console |
| `BT` | Backtesting | Point-in-time strategy backtester |
| `PLUG` | Plugins | Browse, install, and publish community functions |
| `API` | API Documentation | TAPI reference, live-executable examples |
| `MDL` | Model Registry | Every analytic model, its version, source code link, and validation report |

`MDL` has no equivalent in a closed workstation and is central to the product's credibility. Every pricing and risk model is listed with its implementation, its parameters, its test suite results, its benchmark comparisons against independent implementations, and a permalink to the source. Model risk management teams can audit the entire analytics library without a vendor questionnaire.

---

## 8. Data Architecture and Plumbing

### 8.1 Acquisition

Data arrives through five structurally different pipelines, and the differences matter for reliability, latency, and provenance.

**1. Regulatory and statistical sources.** SEC EDGAR, FINRA TRACE, MSRB EMMA, DTCC SDR, CFTC, ESMA FIRDS/FITRS, GLEIF, Companies House, SEDAR+, central banks, and statistical agencies. These are polled or streamed on published schedules. They are authoritative, free, and permanently archivable, which makes them the backbone.

Engineering notes: EDGAR's XBRL Company Facts API gives structured, tagged fundamentals for every US filer without HTML parsing. TRACE dissemination provides post-trade transparency on the majority of US corporate bond volume. The DTCC SDR public feed provides genuine OTC derivative transaction data — notional, tenor, rate, and timestamp — for free. These three sources alone cover more ground than most practitioners realise.

**2. Exchange and venue data.** Free real-time depth where offered (IEX DEEP/TOPS via native protocol), free delayed feeds elsewhere, and free daily settlement, volume, and open-interest files from the major derivatives exchanges. Free-tier vendor APIs fill gaps with rate limits managed by the ingest scheduler.

**3. The contributed network.** Participants push indicative and executable quotes, axes, and runs (§2.2). This is the OTC price discovery layer and the source behind `ALLQ` and `TCMP`.

**4. Community normalisation.** The layer that is genuinely expensive in a closed product — reading prospectuses, keying bond terms, mapping financial statements, coding corporate actions, maintaining the entity graph — is here done once, in public, by a combination of automated extraction and community contribution, with the output openly licensed.

The mechanism: automated extraction (document parsing plus LLM structured extraction, §20) proposes a record; validation rules check internal consistency and cross-source agreement; disagreements and low-confidence extractions are queued for human review; every record carries its provenance and confidence. Contributions are versioned and attributed, and corrections propagate through `DRQ`. This is the same model that produced open mapping and open encyclopedic data, applied to instrument reference data — and unlike a proprietary database, every user's correction improves everyone's data.

**5. Derived and computed data.** Composites, evaluated prices, factor returns, consensus estimates, and every analytic output. These are recomputed on schedule and stored with their model version so that any historical number can be reproduced exactly.

### 8.2 The ticker plant

The ticker plant is the real-time normalisation and distribution engine.

1. **Ingest** native venue protocols and vendor feeds via per-source adapters, each isolated so a misbehaving source cannot degrade the plant.
2. **Normalise** to a common internal message schema (Arrow-encoded, schema-versioned) so "last trade price" is the same field regardless of origin.
3. **Enrich** with the security master join, corporate action adjustments, and computed derived fields.
4. **Compute composites** — `TGN` indicative and `TCMP` executable composites derived by aggregating and quality-weighting contributors.
5. **Distribute** to client sessions, TPIPE subscribers, and internal analytics engines.

Architecturally:

- Built on **Redpanda/Kafka** for the durable log and **NATS JetStream** for low-latency fanout to clients.
- **Geographically distributed nodes** — a subscriber connects to the nearest. Community nodes in major regions; self-hosted nodes wherever the firm operates.
- **Replicated, not sharded by client** — every node carries the full universe, so failover is a reconnect rather than a data migration.
- **Full-tick unconflated** on TPIPE; conflated on the display path.
- Maintains a **current-state cache per instrument**, so a new subscriber receives an immediate image ("initial paint") followed by deltas, rather than waiting for the next tick.
- **Deterministic replay** — because the ingest log is durable and every transformation is versioned, any past moment can be replayed exactly. This is invaluable for debugging, backtesting, and dispute resolution, and it is difficult to retrofit.

### 8.3 TAPI — the programming interface

`TAPI` is the unified interface across desktop, server, TPIPE, and platform deployments. Clients in **Python, Rust, Java, C++, JavaScript/TypeScript, R, and Go**, generated from a single Protobuf schema so they never drift apart.

**Transport:** gRPC for request/response and streaming; **Apache Arrow Flight** for bulk columnar transfer (an order of magnitude faster than row-oriented serialisation for historical pulls); WebSocket for browser clients.

**Object model:**

```
Session          — a connection to a Treble endpoint
  Service        — a named capability, addressed like a path
  Request        — a query returning a stream of Events
  Subscription   — a standing interest in streaming data
  Event          — a batch of Messages
  Message        — a typed, schema'd payload
  Element        — the field tree within a Message
```

**Services:**

| Service | Purpose |
|---|---|
| `//trb/refdata` | Reference data, historical data, intraday bars, intraday ticks, portfolio data |
| `//trb/mktdata` | Real-time streaming subscriptions |
| `//trb/mktbar` | Streaming bar aggregation |
| `//trb/mktvwap` | VWAP subscriptions |
| `//trb/flds` | Field dictionary — search and describe |
| `//trb/instruments` | Instrument lookup and identifier resolution |
| `//trb/analytics` | Direct access to pricing and risk engines |
| `//trb/tql` | TQL execution |
| `//trb/ems` | Order routing |
| `//trb/contrib` | Contributed data submission |
| `//trb/docs` | Document retrieval and search |

**Request types on `//trb/refdata`:**

- `ReferenceDataRequest` — current values for securities × fields (`TDP` analogue)
- `HistoricalDataRequest` — daily and coarser time series (`TDH` analogue)
- `IntradayBarRequest` — OHLCV bars from 1 second upward
- `IntradayTickRequest` — individual trades and quotes, **full history, no lookback window**
- `ScreenRequest` — run a saved `EQS`/`SRCH` screen
- `PortfolioDataRequest` — retrieve holdings

**Design decisions that differ from closed workstations:**

- **No rate limits and no redistribution restrictions** on public and contributed data. A firm may pull the entire universe into its own warehouse; the architecture supports this explicitly (§8.5). Only fair-use protection against runaway clients applies on community nodes, and self-hosted nodes have none.
- **Full tick history** with no rolling window. Storage is cheap and the archive is compressed columnar Parquet.
- **Async by default with sync convenience wrappers** — the underlying model is an event queue, but the Python client offers a blocking interface because most analysis is sequential and forcing async on every user is hostile.
- **The API is the product.** Every screen in the client is implemented over TAPI. There is no privileged internal interface, which guarantees the public API is never a second-class citizen.

### 8.4 TPIPE — the enterprise feed

TPIPE is the machine-facing real-time feed:

- Full universe, all asset classes, all sources, plus composites and indices.
- **Managed entitlements** — where a source carries redistribution conditions (some free-tier vendor APIs do), TPIPE enforces them centrally per user and per application, and logs consumption for audit.
- **Deployment**: in-process library, sidecar container, on-premises cluster, or connection to a community node. Kubernetes manifests and Terraform modules published.
- **Latency**: sub-millisecond within a co-located deployment; typical wide-area latency is dominated by the upstream source, not the plant. TPIPE is not positioned as an ultra-low-latency HFT feed — a latency-sensitive strategy takes direct venue feeds. TPIPE's proposition is *breadth, provenance, and unconflated completeness*.
- **Snapshot + stream** semantics identical to the display path.

### 8.5 Bulk delivery and the warehouse pattern

For non-real-time bulk consumption:

- **Parquet datasets on object storage** — reference data, end-of-day pricing, corporate actions, historical time series, fundamentals, holdings. Partitioned by date and asset class, readable directly by DuckDB, Polars, Spark, Snowflake, BigQuery, Databricks, or anything that speaks Parquet.
- **Delta Lake / Apache Iceberg tables** for datasets requiring time travel and schema evolution.
- **Arrow Flight bulk endpoint** for direct high-throughput pulls.
- **Change data capture** — a subscribable stream of reference data changes, so a downstream warehouse stays current without full reloads.
- **`treble-sync`** — a single command that mirrors any subset of the corpus into a local or cloud warehouse and keeps it current.

The warehouse pattern is treated as a supported first-class workflow rather than a licensing violation. Many quantitative users want the data *in their own environment*; fighting that is pointless when the data is free.

### 8.6 Storage tiers

| Tier | Technology | Contents |
|---|---|---|
| **Hot** | Redis / in-memory state cache | Current quotes, positions, session state |
| **Warm** | ClickHouse / TimescaleDB | Recent ticks, intraday bars, last 2 years daily |
| **Cold** | Parquet on object storage (MinIO/S3) | Full tick history, full daily history, document corpus |
| **Reference** | PostgreSQL | Security master, entity graph, identifiers, user data, portfolios |
| **Search** | OpenSearch + Qdrant | Document full-text and vector indices |
| **Log** | Redpanda/Kafka | Durable ingest log for deterministic replay |

### 8.7 Network

- Public internet with TLS 1.3 throughout; optional WireGuard tunnels for institutional deployments.
- The delta-based screen buffer (§6.1) makes the client tolerant of poor connectivity — usable on a link that would make a conventional web app unusable.
- Self-hosted nodes may run fully air-gapped, syncing reference data by periodic bulk transfer.

---

## 9. Security Identifiers and the Security Master

### 9.1 The identifier problem

The hardest problem in financial data is **"is this the same instrument?"** A bond may be referenced by ISIN, CUSIP, SEDOL, a dealer's internal code, a ticker, or free text on a term sheet. Identifier resolution is the foundation everything else sits on, and getting it wrong corrupts every downstream number silently.

### 9.2 Treble Tracker's identifiers

| Identifier | Description |
|---|---|
| **Ticker + Exchange + Yellow Key** | The human-facing composite: `IBM US Equity`. `IBM` = ticker, `US` = composite US listing, `Equity` = asset class. `IBM UN Equity` is specifically the NYSE listing. |
| **FIGI** | The 12-character Financial Instrument Global Identifier, e.g. `BBG000BLNNH6`. **Free, openly redistributable, ANSI X9.145.** The primary instrument key. |
| **LEI** | Legal Entity Identifier from GLEIF — free, open, with full parent/child relationship records. The primary *entity* key. |
| **TUID** | Treble Unique Identifier — the internal surrogate key, stable across all reference changes |
| **Structured OTC tickers** | `USSW10 Curncy` (10y USD swap), `CT10 Govt` (current 10y Treasury), `FNCL 5.5 Mtge` |
| **TCLASS** | Treble fixed income classification taxonomy |
| **TICS** | Treble Industry Classification System — an open sector hierarchy derived from NAICS/ISIC with a finance-oriented sub-taxonomy, published under an open licence |

### 9.3 Why FIGI is the right foundation

- **Free and openly redistributable** — unlike CUSIP and ISIN, which are licensed. This is what makes an open workstation legally possible at all; an identifier you cannot redistribute cannot anchor an open data product.
- **Hierarchical** — identifies at three levels: the instrument at a specific venue, the country/composite level, and the global share class level. This cleanly solves "same company, fourteen listings".
- **Never reused, never changed.** A FIGI survives ticker changes, name changes, and mergers. This is critical for historical analysis and is precisely where ticker-based systems silently corrupt backtests.
- **Free mapping API** at OpenFIGI resolves from ISIN, CUSIP, SEDOL, ticker, and other identifiers.

**Where licensed identifiers appear:** ISIN and CUSIP are widely present in public regulatory data (EDGAR filings, TRACE, N-PORT, FIRDS all publish them), so the system stores and matches on them where they arrive in public sources, but never redistributes a licensed identifier database as such. Resolution and display work; bulk export of a CUSIP master does not. In practice this is invisible to users, because FIGI does the actual work.

### 9.4 The security master

Beneath the identifiers is the normalised record of every instrument's terms. For a corporate bond: issue date, maturity, coupon type and schedule, day count convention, payment frequency, business day convention, call/put/sink schedules, make-whole provisions, covenants, guarantors, collateral, ranking, governing law, minimum piece and increment, tax status, and the issuer link.

Extraction pipeline:

1. **Source documents** — prospectuses, offering circulars, and final terms from EDGAR, EMMA, and issuer sites.
2. **Structured extraction** — layout-aware document parsing plus an LLM extraction pass with a strict output schema (§20).
3. **Cross-validation** — extracted terms are checked against ESMA FIRDS, exchange reference files, and any contributed data. Agreement raises confidence; disagreement queues review.
4. **Consistency rules** — a call schedule must be monotonic in date, a coupon must be consistent with the payment frequency, an amortisation schedule must sum to par. Hundreds of such rules catch extraction errors mechanically.
5. **Human review** for low-confidence and high-impact records.
6. **Versioned publication** with full provenance — `SPTR <GO>` on any field shows the source document and the page it came from.

### 9.5 The entity hierarchy

Above the instruments sits the parent/subsidiary/issuer/guarantor graph, built from **GLEIF relationship records** (free, and the only comprehensive open source of legal entity ownership), **EDGAR Exhibit 21** subsidiary lists, filing relationships, and OpenCorporates.

This graph is what makes `RELS` and `DDIS` work: given an equity ticker, enumerate every debt instrument issued by any consolidated subsidiary. Maintaining it through M&A, restructurings, and name changes is continuous work, and it is a major beneficiary of the community-contribution model — a credit analyst who discovers a missing guarantor link fixes it for everyone.

### 9.6 Field dictionary

Roughly 15,000 named fields at v1 (`PX_LAST`, `CUR_MKT_CAP`, `OAS_SPREAD_MID`, `DUR_ADJ_OAS`, `BEST_EPS`, `IDX_MWEIGHT`), searchable via `FLDS <GO>`. Each field carries:

- a **mnemonic** and stable numeric ID
- a **data type** (price, yield, string, date, bulk table)
- an **override list** — parameters that change how it is computed
- **source and provenance metadata**
- **history availability** flags
- a **model reference** into `MDL` where the field is model-derived

The override mechanism deserves emphasis: `OAS_SPREAD_MID` on a callable bond is not a stored number, it is the output of a model run. Overrides (`OAS_VOL_OVERRIDE`, `OAS_MODEL_OVERRIDE`, `PX_OVERRIDE`, `SETTLE_DT_OVERRIDE`, `CURVE_OVERRIDE`) re-run that model with the user's own assumptions from a spreadsheet cell or an API call.

---

## 10. Analytics Internals — Fixed Income

Fixed income is the deepest part of the specification and the area where an open implementation has the strongest case: the mathematics is published, the conventions are documented in ISDA and ICMA standards, and the reference implementation (QuantLib) has been validated by two decades of production use across the industry.

### 10.1 `YAS` — Yield and Spread Analysis

`YAS` is the screen a credit or rates analyst lives on. Given a bond and any one of price, yield, or spread, it derives the others and the full risk set.

**Price/yield conversions**

- **Yield to maturity**, solved from the dirty price by Brent's method on the discounted cash flow equation, respecting the exact day count (`30/360`, `ACT/ACT ICMA`, `ACT/360`, `ACT/365F`, `30E/360`, `30E/360 ISDA`, and national variants), payment frequency, business day convention (Following, Modified Following, Preceding, Modified Preceding), holiday calendars per currency and market, ex-dividend rules, and first/last stub handling. Implemented on QuantLib's `DayCounter`, `Calendar`, and `Schedule` primitives, which encode these conventions correctly and are themselves widely validated.
- **Yield to call / yield to put / yield to worst** — the YTM solve is run against every call and put date in the schedule; YTW is the minimum across the call set for a callable, maximum across the put set for a putable.
- **Yield to average life / yield to sink** for amortising and sinking-fund structures.
- **Multiple yield conventions** — US corporate semi-annual street convention, true yield, Japanese simple yield, annual-pay conventions, and money-market yields on short bonds. Convention differs by country and instrument; exposing the choice rather than hiding it is the correct behaviour for a professional tool.
- **Accrued interest** on the exact day count and settlement calendar, with the accrued-to-settle vs. accrued-to-today distinction made explicit.

**Spread measures**

| Measure | Definition |
|---|---|
| **G-spread** | Yield minus interpolated government curve yield at the bond's maturity |
| **I-spread** | Yield minus interpolated swap curve yield at the bond's maturity |
| **Z-spread** | Constant parallel shift to the zero curve equating discounted cash flows to market price; solved iteratively |
| **ASW** | Spread on the floating leg of a par asset swap package. Both par-par and market-value asset swaps computed — they differ materially for off-par bonds, and conflating them is a common error |
| **OAS** | Spread over the stochastic curve equating model price to market price, stripping the embedded option value (§10.2) |
| **Discount margin** | For floaters — the margin over the index equating PV to price |
| **Spread to benchmark** | Yield minus a specific named benchmark bond — the market's actual quoting convention for corporates |
| **CDS basis** | Bond spread minus CDS spread for the same issuer and tenor |

**Risk measures**

- **Macaulay duration**, **modified duration**, **Fisher–Weil duration**
- **Effective (option-adjusted) duration** — the whole curve is bumped ±Δ, the full lattice valuation re-run, and a central difference taken. For a callable bond this differs materially from modified duration, and using the wrong one is a real hedging error.
- **Convexity** and **effective convexity** (second central difference)
- **DV01 / PV01 / BPV**
- **Spread duration** — sensitivity to spread rather than rates
- **Key rate durations (KRD)** — partial durations to bumps at 6m, 1y, 2y, 3y, 5y, 7y, 10y, 20y, 30y nodes. Each computed by bumping one node, re-bootstrapping the curve with the perturbed input, and revaluing. The workhorse for hedging and, later, the factor loadings in the risk model (§16.3).
- **Vega** for bonds with embedded options

### 10.2 OAS and the lattice engine

For a bond with embedded options, the OAS calculation runs as follows:

1. **Build the term structure** from the selected curve. `SWDF` settings determine whether this is the swap curve, the government curve, or a custom curve.
2. **Calibrate a short-rate model** to the curve and to the swaption volatility surface. The default is **Hull–White (extended Vasicek)** for its analytic tractability and exact fit to the initial term structure; **Black–Karasinski** is available where lognormal rate dynamics are preferred; **shifted/normal variants** handle negative-rate regimes. Calibration targets a user-selectable set of co-terminal ATM swaptions. All three are QuantLib `ShortRateModel` implementations with `CalibrationHelper` targets.
3. **Construct a trinomial lattice** over the short rate, time steps aligned to cash flow and call dates.
4. **Roll back** through the lattice: at each node compute the continuation value, compare against the exercise value (call price, put price), and apply the optimal exercise rule for whichever party holds the option.
5. **Solve for the constant spread** added to every node's discount rate that equates the lattice price to the observed market price. That spread is the OAS.

**Option cost = Z-spread − OAS.** Positive for a callable (the investor is short the option), negative for a putable.

For **path-dependent** instruments — MBS, some structured notes — the lattice is replaced by **Monte Carlo simulation** over rate paths, because prepayment depends on rate history, not merely the current rate.

**Model transparency.** `MDL <GO>` exposes which model produced any OAS, its calibration inputs, and the calibration residuals against the target swaptions. A user who does not trust the number can see exactly where it came from — including the case where calibration fit poorly, which is precisely when the number should be distrusted.

### 10.3 Mortgage analytics

Agency MBS is the most computationally demanding mainstream asset class.

**The prepayment model.** Treble Tracker ships an **open, published prepayment model** — a genuine structural advantage, because prepayment assumptions drive every MBS number and closed models cannot be independently validated. The model is fitted to freely available loan-level data: Fannie Mae and Freddie Mac single-family loan performance datasets, Ginnie Mae pool disclosures, and monthly agency factor files, all published without charge.

Projected monthly prepayment rates are a function of:

- **Refinancing incentive** — spread between pool WAC and prevailing mortgage rate (free from the weekly primary mortgage market survey and daily secondary market indications), entering through a non-linear S-curve
- **Burnout** — depletion of refi-sensitive borrowers after repeated waves; requires path memory, which is why Monte Carlo rather than a lattice
- **Seasoning** — the turnover ramp over a pool's early life
- **Seasonality** — housing turnover peaks in summer
- **Loan characteristics** — LTV, FICO, loan size, occupancy, geography, servicer, origination channel; all present in agency loan-level disclosure
- **Media effect and rate lock-in** — behavioural response to publicised rate moves, and in a high-rate regime the suppression of turnover by borrowers unwilling to surrender a low mortgage rate
- **Curtailments and defaults** for credit-sensitive collateral

Users may substitute their own model, or their counterparty's, via the model registry; the pricing engine takes the prepayment model as a pluggable interface.

Outputs:

| Measure | Meaning |
|---|---|
| **CPR** | Conditional prepayment rate, annualised |
| **SMM** | Single monthly mortality; `CPR = 1 − (1 − SMM)^12` |
| **PSA** | Speed as a multiple of the standard ramp |
| **WAL** | Weighted average life under a given speed |
| **OAS** | Monte Carlo option-adjusted spread |
| **ZV spread** | Zero-volatility spread |
| **Effective duration / convexity** | Bumped-curve revaluation with the prepayment model re-run — this is why MBS exhibits *negative convexity* |
| **Partial durations** | Key rate durations |
| **Prepay duration / refi elasticity** | Sensitivity to the prepayment model itself |
| **Spread duration, vega** | |

**CMO waterfalls** are modelled explicitly: tranche priorities, PAC/TAC bands, support tranches, IO/PO strips, floaters and inverse floaters, and residuals. Collateral cash flows are run through the legal waterfall. Deal structures are extracted from prospectuses via the §9.4 pipeline and, critically, are **openly published as structured data** — so that a deal's waterfall can be independently verified rather than trusted.

### 10.4 Municipals, loans, structured credit

- **Munis**: tax-equivalent yield (federal, state, AMT), de minimis rules, bank-qualified status, insurance wrappers, escrow-to-maturity and pre-refunding logic, with disclosure linked from EMMA. EMMA provides trades, official statements, and continuing disclosure free, which makes municipals unusually well-served by public data.
- **Leveraged loans**: floating rate, amortising, revolver and delayed-draw features. Priced from the contributed network and from fund holdings disclosure — N-PORT filings by loan funds reveal marks on individual facilities quarterly, which is a genuinely useful free signal on an otherwise opaque market.
- **ABS/CLO**: deal waterfalls, OC/IC tests, trigger logic, WARF/WAS/diversity score analytics. European deals benefit from the EU Securitisation Regulation's mandated loan-level reporting to securitisation repositories.

### 10.5 Horizon and scenario analysis

`HZ` / `HR` computes **total return over a holding period** under specified forward assumptions:

- Roll the bond to the horizon date, accruing coupons and reinvesting at a specified rate
- Apply a curve scenario — parallel shift, steepener/flattener, a user-drawn curve, or the forward-implied curve
- Reprice under the scenario, re-running the option and prepayment models
- Decompose total return into **carry, roll-down, curve, spread, and convexity**

This decomposition is the standard relative-value framework and is deliberately identical to the attribution logic in `PORT` (§16.4), so that ex-ante and ex-post analysis are internally consistent.

---

## 11. Analytics Internals — Curves and Volatility

### 11.1 Curve construction (`ICVS`, `SWDF`, `CRVF`)

The system maintains thousands of curves — per currency, per collateral type, per index tenor, plus government, agency, sector, muni, inflation, and credit curves. `CRVF <GO>` is the catalogue; each carries a stable ID and a full definition.

Inputs are free: overnight rates from central banks (SOFR and repo rates from the NY Fed, €STR from the ECB, SONIA from the BoE, TONA from the BoJ, SARON from SIX), futures settlements from exchange daily files, government yields from treasury and central bank publications, and swap rates from the DTCC SDR public feed and the contributed network.

**Multi-curve framework.** This is the correct post-2008 architecture and is implemented fully:

- **Discounting curve ≠ forecasting curve.** Collateralised derivatives discount at the collateral rate (OIS), while forward rates for a given index tenor project off a curve built from instruments referencing that index.
- **Separate forecast curves per tenor basis** — a 3M-index curve and a 6M-index curve are distinct objects, connected by tenor basis swaps.
- **Cross-currency basis** — discounting a foreign-currency cash flow under a USD-collateral CSA requires the cross-currency basis curve.
- **CSA-aware discounting** — `SWPM` accepts the collateral agreement (currency, rate, thresholds) and values accordingly. This is not cosmetic; it moves a long-dated swap's PV materially.

**Bootstrapping procedure:**

1. **Instrument selection.** `SWDF <GO>` is where the user chooses which instruments populate which part of the curve: overnight and deposit rates plus central bank meeting dates at the front, futures or FRAs in the middle, par swap rates at the long end. Every choice here changes every downstream number, so the configuration is explicit, saveable, shareable, and stamped onto every result computed with it.
2. **Futures convexity adjustment.** Futures are margined daily and therefore lack the convexity bias of FRAs; a Hull–White-consistent adjustment converts futures rates to forward rates. User-configurable, and the applied adjustment is displayed rather than buried.
3. **Turn-of-year and meeting-date effects.** The front end has calendar and policy-step effects that must be isolated so they do not contaminate the smooth section. Policy rate curves are modelled as meeting-date step functions.
4. **Interpolation method.** Selectable: linear on zero rates, linear on log-discount factors (equivalent to piecewise-constant forwards), cubic spline on zeros, tension spline, **monotone convex (Hagan–West)**, piecewise-constant forward. The choice is consequential — cubic spline on zeros can produce oscillating and even negative forward rates, while monotone convex is designed specifically to prevent that. The default is monotone convex; the choice is exposed rather than hidden.
5. **Solve.** Iteratively find the zero curve repricing every input instrument to market. Multi-curve setups require a simultaneous solve across discount and forecast curves. QuantLib's `PiecewiseYieldCurve` with global bootstrap handles the coupled case.
6. **Outputs.** Zero rates, discount factors, par rates, instantaneous and period forward rates. `FWCM <GO>` displays the forward matrix; `ICVS <GO>` shows shape, history, and cross-date comparison.

**Fallback rates.** Published IBOR fallback spread adjustments are static, public numbers; they are stored as reference data so legacy contracts value correctly.

### 11.2 Inflation curves

Real and breakeven curves are built from inflation-linked bonds and inflation swaps, with explicit handling of:

- **Indexation lag** (3 months for most linkers, 8 months for older UK issues)
- **Seasonality** — CPI has strong monthly seasonals which must be modelled or the front of the breakeven curve is meaningless. Seasonal factors are estimated from the published index history, which is free from every statistical agency.
- **Deflation floors** on principal
- Carry from known published fixings versus projected fixings

### 11.3 Volatility surfaces (`OVDV`, `VCUB`)

**Equity, FX, and commodity surfaces:**

- Implied vols backed out per listed option via **Black–Scholes** (European), **Black-76** (futures options), or a **binomial/trinomial American** engine (Cox–Ross–Rubinstein with discrete dividends, or Bjerksund–Stensland approximation) by exercise style.
- **Dividend treatment** is configurable and consequential: discrete cash, discrete proportional, continuous yield, or a forward-implied dividend curve backed out from put-call parity.
- **Borrow cost / repo rate** backed out from put-call parity on liquid strikes and used to build a forward curve. The implied forward, not spot, anchors the surface — getting this wrong tilts the whole skew.
- The surface is fitted in **(strike or delta) × expiry** space using **SVI** or spline parameterisations, subject to **no-arbitrage constraints**: calendar arbitrage (total variance non-decreasing in time) and butterfly arbitrage (risk-neutral density non-negative). Arbitrage-free SVI (SSVI) is the default parameterisation.
- Displayed as vol vs. strike (skew/smile), vol vs. expiry (term structure), and 3D surface. `SKEW`, `HVG` (historical vs. implied).

Free inputs: exchange end-of-day option settlement files carry full chains with settlement prices, open interest, and volume for equity index, single stock, futures, and FX options — enough to build daily surfaces across every listed market at no cost.

**Interest rate volatility — `VCUB`:**

The swaption cube has three axes: **option expiry × underlying swap tenor × strike**. The engine:

- Ingests ATM swaption vols in **normal (Bachelier)** or **lognormal (Black)** quotation, with conversion between them. Normal vols are the post-2014 market standard given negative rates; both are supported.
- Ingests smile information from swaption strike grids and CMS spread options, sourced from the DTCC SDR public feed and the contributed network.
- Fits **SABR** per expiry/tenor slice — parameters α (level), β (backbone, conventionally fixed at 0, 0.5, or 1), ρ (correlation, controls skew), ν (vol-of-vol, controls curvature). QuantLib's `SabrInterpolation` with Hagan's expansion, plus a no-arbitrage-corrected variant for low strikes where the standard expansion misbehaves.
- Handles negative rates via **shifted SABR** (adding a displacement to the forward) or **normal SABR**.
- **Strips caps into caplets** — caps are quoted as flat vols on the whole strip and must be bootstrapped into caplet-by-caplet vols, a non-trivial procedure with its own smoothness regularisation.

`VCUB <GO>` displays the cube and interpolates arbitrary points; the fitted surface feeds `SWPM`, `DLIB`, and every OAS calculation, so a single consistent volatility view underlies all rate analytics.

---

## 12. Analytics Internals — Derivatives and DLIB

### 12.1 `SWPM` — the Swap Manager

`SWPM` structures and values linear rates products and many non-linear ones:

- Vanilla fixed-float IRS, basis swaps, OIS, tenor basis
- Cross-currency swaps, with and without notional exchange, with mark-to-market resets
- Amortising, accreting, roller-coaster, and custom notional schedules
- Forward-starting, cancellable, extendible
- Inflation swaps, zero-coupon and year-on-year
- Asset swaps, par-par and market-value
- Swaptions, caps and floors, CMS and CMS spread products
- Total return swaps

For each: PV, par rate, DV01 and bucketed DV01s, cash flow schedules, and CSA-aware discounting. Trades book into `RISK` and `PMS`, and generate confirmations.

### 12.2 `DLIB` — the Derivatives Library

`DLIB` is a **structuring environment**: define an arbitrary payoff, and the engine prices it.

**How it works:**

1. **Payoff definition.** Pick from a library of pre-built structures (autocallables, reverse convertibles, range accruals, cliquets, worst-of baskets, TARFs, accumulators, snowballs, PRDCs) or write the payoff in **TPay**, a small declarative payoff scripting language supporting path dependence, barriers, memory features, and conditional coupons. TPay compiles to an evaluation graph the engine can differentiate — which is what makes adjoint greeks (below) possible.

```
# Autocallable note, TPay
observe dates = quarterly(start, start + 3Y)
for t in dates:
    if S(t) >= 1.00 * S(0):
        pay 1.00 + coupon * elapsed(t); terminate
pay if S(T) >= 0.70 * S(0) then 1.00
     else S(T) / S(0)
```

2. **Model selection.** The engine selects, or the user forces, a model appropriate to the payoff:
   - **Black–Scholes / local volatility (Dupire)** where the smile matters but stochastic vol dynamics do not
   - **Heston** and **Bates** (Heston plus jumps) for stochastic volatility
   - **Local-stochastic volatility (LSV)** — calibrated to fit the vanilla surface exactly while producing realistic forward smile dynamics; the right choice for exotic FX and equity
   - **Hull–White 1F/2F**, **Black–Karasinski**, **LIBOR/RFR Market Model** with SABR or displaced-diffusion volatility for rates exotics
   - **Cheyette / quasi-Gaussian** for Bermudan swaptions
   - **Multi-factor hybrid** models for cross-asset payoffs (equity-rate, FX-rate)

3. **Numerical method.**
   - **Closed form** where available
   - **PDE finite difference** (Crank–Nicolson, ADI in 2D) for low-dimensional early-exercise problems
   - **Trees and lattices** for simple American features
   - **Monte Carlo** for path dependence and high dimension, with **Sobol** and **Halton** quasi-random sequences, **Brownian bridge** path construction, and antithetic and control variates for variance reduction
   - **Longstaff–Schwartz least-squares Monte Carlo** for early exercise within Monte Carlo — the standard method for Bermudan and callable exotics
   - **Adjoint algorithmic differentiation (AAD)** for greeks. Because TPay compiles to a differentiable graph, a full greek set costs roughly the same as a few pricings rather than one pricing per sensitivity. For a portfolio with thousands of risk factors this is the difference between overnight and interactive.

4. **Outputs.** Price, full greek set (delta, gamma, vega, theta, rho, vanna, volga, cross-gammas), bucketed sensitivities, scenario grids, and the payoff distribution. Plus term sheet generation and hand-off to `RISK` for ongoing monitoring.

**Calibration** is where the real work sits: models calibrate to market-observed vanilla surfaces (`OVDV`, `VCUB`) so that the exotic price is consistent with its hedging instruments. `DLIB` displays calibration diagnostics — how well the model reproduces the vanilla surface — prominently rather than on a hidden tab, because a well-priced exotic on a badly calibrated model is worse than no price.

### 12.3 `RISK` — the risk system

| Module | Purpose |
|---|---|
| **Front Office** | Real-time P&L, greeks, and position risk across all asset classes |
| **Market Risk** | VaR (historical, parametric, Monte Carlo), expected shortfall, stress testing, FRTB measures including the sensitivities-based method and default risk charge |
| **XVA** | CVA, DVA, FVA, MVA, KVA — full portfolio Monte Carlo over exposure paths with netting sets, CSA terms, and collateral modelling |
| **Collateral** | Margin calculation including **ISDA SIMM** (the methodology is published; the implementation is open) |
| **Counterparty Risk** | PFE, EPE, and limit monitoring |
| **Valuation** | Independent price verification against TVAL and contributed marks |
| **Hedge Accounting** | ASC 815 / IFRS 9 effectiveness testing and documentation |
| **Climate** | NGFS scenario analysis on portfolios |

The XVA engine is the heaviest computation in the system: a CVA calculation simulates thousands of paths of every risk factor, revalues the entire netting set at dozens of future dates on each path, and applies netting, collateral, and default probability. It runs on **Ray** or **Dask** for horizontal scale-out, with AAD making the sensitivity computation tractable. A firm can run it on its own hardware or a spot-instance cluster; there is no per-calculation charge, which changes the economics of running XVA frequently rather than nightly.

---

## 13. Analytics Internals — Credit

### 13.1 `CDSW` — CDS valuation

The **ISDA Standard CDS Model** is implemented in full. The methodology and the original reference implementation are public, which makes this one of the cleanest open reproductions in the system.

1. **Build the risk-free discount curve** from the currency's money-market and swap rates per the standard's prescribed conventions.
2. **Bootstrap the hazard rate (default intensity) curve** from quoted CDS spreads across tenors (6m, 1y, 2y, 3y, 4y, 5y, 7y, 10y, 20y, 30y), assuming piecewise-constant hazard between quoted tenors and a fixed recovery rate — conventionally 40% for senior unsecured corporates, 20% for subordinated, 25% for emerging market sovereigns.
3. **Survival probability** at time *t*: `Q(t) = exp(−∫₀ᵗ λ(s) ds)`.
4. **Value the premium leg** — PV of the standardised coupon times survival probability at each payment date, plus the accrued-on-default term.
5. **Value the protection leg** — `(1 − R)` times the PV of the default probability density, integrated over the life.
6. **Upfront payment** = protection leg − premium leg. This is what changes hands, since coupons are standardised at 100bp or 500bp.
7. Compute **CS01**, **IR01**, **jump-to-default (JTD)**, and **recovery risk**.

Outputs also include par spread, flat spread, the implied cumulative default probability term structure, and the CDS-bond basis.

**Index CDS** (CDX, iTraxx): index versioning, factor adjustments for defaulted constituents, index-versus-intrinsic skew, and index option pricing.

**Free inputs.** Single-name and index CDS transactions are publicly disseminated by swap data repositories under CFTC and EU reporting rules — notional, spread, tenor, and timestamp, in near real time. This is a genuinely rich free source for a market usually described as opaque, and it anchors the hazard curves without any commercial feed.

### 13.2 `DRSK` — default risk

`DRSK` produces a **one-year default probability and an implied credit rating** for public and private companies.

- **Structural (Merton-type) core.** Equity is treated as a call option on firm assets struck at the default point (short-term debt plus a fraction of long-term debt). Given observable equity value and equity volatility, solve the two simultaneous equations for unobservable asset value and asset volatility. Then **distance to default** = (asset value − default point) / (asset value × asset volatility).
- **Empirical mapping.** Mapping distance-to-default to a probability via the normal distribution badly understates realised default rates. Instead an empirical mapping is fitted to a historical default database assembled from free sources: bankruptcy filings (PACER and EDGAR Item 1.03 8-Ks), exchange delisting notices, rating agency default press releases, and the agencies' own published annual default studies.
- **Fundamental overlay.** Accounting signals — interest coverage, leverage, liquidity, profitability, size, industry — blended in via a gradient-boosted model. This materially improves accuracy where equity is illiquid or its volatility uninformative.
- **Private company variant** uses fundamentals and comparable-company mapping, since there is no observable equity price.

Outputs: 1-year DP, a default probability term structure, a mapped implied rating on a published scale, and a driver decomposition showing what moved the number.

Because the model, the fitted parameters, and the training data are all open, `DRSK` can be independently validated and recalibrated by any user — which is exactly what a model risk function needs and rarely gets.

### 13.3 Credit relative value

`CRVD` plots an issuer's credit curve against peers. `SRCH` combined with `PORT` lets a credit PM screen the investable universe for spread-per-unit-of-risk outliers. Historical spread series, CDS-bond basis monitors, and rating-transition analytics complete the toolkit.

---

## 14. Analytics Internals — Equities

### 14.1 Fundamental data normalisation (`FA`)

`FA` maintains **two parallel views** of every company's financials:

- **As-reported** — exactly as filed, in the filing's own taxonomy and accounting standard (US GAAP, IFRS, local GAAP).
- **Treble-standardised** — remapped onto a single global chart of accounts so companies across countries and standards are comparable.

The standardisation pipeline is where most of the work sits. For US filers, EDGAR's XBRL provides machine-readable, tagged statements — but issuers use extension tags liberally, so a mapping layer resolves custom tags onto the standard taxonomy using tag definitions, statement position, calculation linkbase relationships, and a trained classifier, with human review for unmapped material items. For IFRS filers, ESEF filings provide equivalent tagged data in Europe. Non-XBRL jurisdictions fall back to layout-aware table extraction with LLM-assisted mapping and validation against reported subtotals — a statement that does not foot is rejected automatically.

`FA` provides: 20+ years of history, quarterly and annual, segment data (business and geographic), common-size statements, ratio analysis, cash flow reconciliation, pension and lease adjustments, restatement tracking, and currency translation with a selectable rate basis.

Every standardised line item links back via `SPTR` to the exact tag and filing it came from. Where a mapping decision was made, that decision is visible. This is a real advantage over closed normalisation, where analysts routinely find numbers they cannot reconcile and have no way to investigate.

### 14.2 Estimates (`EE`, `EEO`, `ANR`)

Consensus estimates are the one area where free data is structurally thinnest — sell-side estimates are proprietary to their publishers. Treble Tracker addresses this three ways, and the combination is stronger than it first appears:

**1. The contributed estimate network.** Analysts and firms contribute estimates directly, receiving attribution and a public, verifiable accuracy track record in return. For a junior analyst building a reputation, a scored public record has real career value — this is the same incentive structure that made open review and open benchmark leaderboards work in other fields.

**2. Extracted estimates.** Many estimates are public but unstructured — in published research summaries, press coverage, company-compiled consensus (issuers frequently publish the consensus they are measured against, a much-underused free source), and conference call commentary. Structured extraction pulls these into the consensus with source attribution.

**3. Model-generated estimates.** A published forecasting model produces estimates for every covered company from historical fundamentals, guidance, macro drivers, high-frequency indicators, and peer results. These are clearly labelled as model-derived and scored against actuals on the same public leaderboard as human contributors. For companies with thin analyst coverage — the large majority of listed companies globally — this is *better* coverage than a conventional consensus, not worse.

Across all three, the system computes **mean, median, high, low, standard deviation, and count** for every line item — not just EPS but revenue, EBITDA, EBIT, capex, free cash flow, margins, segment metrics, and industry KPIs (same-store sales, ARPU, RevPAR, production volumes). Plus:

- **Staleness rules** — estimates age out of consensus if not refreshed; both "all" and "recent" consensus are maintained
- **Revision tracking** — direction and magnitude of estimate changes, itself a heavily used quant signal
- **Surprise analysis** — actual versus consensus, with standardised unexpected earnings (SUE)
- **Contributor track records** — `EEB` shows who estimated what; `ANR` scores contributors on historical accuracy, with the scoring methodology published

### 14.3 Valuation (`EQRV`, `WACC`, `DDM`)

- **`EQRV`** — relative valuation against a peer set, auto-generated (TICS sector + size + geography) or user-defined. Current multiples (P/E, P/B, EV/EBITDA, EV/Sales, P/FCF, dividend yield, PEG) shown against their own history as **z-scores** — answering "cheap versus its own past, versus its peers' past, and versus its peers now?"
- **`WACC`** — full cost of capital decomposition. Cost of equity via CAPM with a selectable risk-free proxy, an equity risk premium estimated per market from free implied-ERP methodology, and a beta that is user-selectable (raw, Blume-adjusted, or fundamental). Cost of debt from the issuer's own bond curve or an implied rating-based curve. Tax rate and market-value capital weights. Everything editable, with history shown.
- **`DDM`** — multi-stage dividend discount model with growth phases and fade.
- **`CRP`** — country risk premium, derived from sovereign CDS and rating-based default spreads.

### 14.4 Quant equity: `EQS`, `BT`, factor analysis

- **`EQS`** screens a global universe of 100,000+ listed equities across thousands of fields, with composite and conditional criteria, ranked scoring, and back-testable saved screens. Executes as TQL over the columnar store, so a full-universe screen with computed fields returns in seconds.
- **`BT`** — build a rule-based strategy (screen + weighting + rebalance schedule), run it historically with transaction costs, and get full performance statistics, turnover analysis, and factor attribution.

  Two properties are non-negotiable and built into the engine rather than left to the user:
  - **Point-in-time data with correct reporting lags.** Fundamentals enter the backtest on the date they were *filed*, not the period they describe. Because EDGAR timestamps every filing to the second, the as-of view is reconstructible exactly — an area where free regulatory data is actually *better* than many commercial point-in-time products, which reconstruct lags by rule of thumb.
  - **Survivorship-bias-free universes.** Delisted, acquired, and bankrupt securities remain in the historical universe with their terminal values. Point-in-time index membership (`INDX_MWEIGHT_HIST`) makes index-relative backtesting valid.

  A backtest that violates either is not merely inaccurate; it is systematically optimistic, and the errors are large enough to make a losing strategy look profitable.
- **`PORT` factor attribution** applies the TFM3 model to any portfolio (§16).
- **`TQNT`** for anything the point-and-click tools cannot express.

### 14.5 Corporate actions and index arithmetic

- **Price adjustment** for splits, stock dividends, spin-offs, and rights issues, with adjusted/unadjusted exposed as an override (`CshAdjNormal`, `CshAdjAbnormal`, `CapChg`).
- **Total return series** with dividends reinvested. The price-return vs. total-return distinction is a common source of error and is made explicit everywhere, never inferred.
- **Index membership history** — point-in-time constituent lists with add/delete dates and historical weights.

Corporate actions are sourced from 8-K filings, exchange notices, and issuer announcements, extracted structurally and validated against observed price discontinuities — an unexplained overnight gap of exactly one half or one third is a strong signal of an unrecorded split, and the pipeline flags these automatically for review.

---

## 15. TVAL: Evaluated Pricing

For the roughly one million fixed income securities that do not trade on a given day, someone must produce a price. Funds must strike a NAV daily regardless. **TVAL** is the evaluated pricing engine, and it is the component where methodological transparency matters most.

### 15.1 The two-pronged methodology

**Prong 1 — Direct Observations.** Where the target bond has market activity, TVAL uses:

- Executed trades (TRACE, EMMA, SDR public dissemination)
- Executable quotes from the contributed network
- Indicative quotes — dealer runs and axes

Weighted by **recency, size, firmness, and source reliability**. An executable two-way quote in size from minutes ago dominates a stale indicative run from yesterday. The weighting function is published, not proprietary.

**Prong 2 — Observed Comparables.** Where the target has little or no direct activity, the **relative value algorithm** prices it from bonds that did trade:

1. **Identify a comparable set** — same issuer, then same sector/rating/seniority/currency/maturity bucket, with optionality matched. Comparables are ranked by a similarity metric over structural and risk characteristics.
2. **Estimate the spread relationship** — regress the target's historical spread on the comparables' spreads over a rolling window, with shrinkage toward the sector average where history is short.
3. **Apply today's comparable levels** through that relationship, adjusting for curve position, structural differences, and any idiosyncratic issuer news detected by the news pipeline (§17).
4. **Reconcile against the issuer's own curve** — a single bond's price cannot drift inconsistently with its siblings. The issuer curve is fitted jointly across all the issuer's outstanding debt, and individual bond prices are constrained to it with an allowance for genuine idiosyncrasy.
5. **Enforce structural relationships** — a senior bond cannot price through a subordinated bond of the same issuer and maturity; a secured bond cannot price wide of an unsecured one absent a specific reason. Violations are flagged and dampened.

### 15.2 The TVAL Score

Every price carries a **TVAL Score (1–10)** expressing confidence, driven by the quantity, quality, timeliness, and internal consistency of the market data used. A score of 9–10 means abundant, corroborating direct observations; a low score means the price is largely model-derived from thin comparables.

This converts "here is a price" into "here is a price and here is how much to trust it" — which is what auditors, fund boards, and regulators actually need. It feeds directly into **ASC 820 / IFRS 13 fair value hierarchy** classification (Level 1 / 2 / 3), and the mapping from score to level is documented.

### 15.3 Full transparency

Every TVAL price expands to show which inputs drove it, which comparables were used, how they were weighted, and what the model would have produced under alternative assumptions. Valuation committees must be able to defend a price; a price that cannot be explained is not defensible.

Because the algorithm is open, a user can also **run it themselves** with their own comparable set, their own weighting, or their own curve — producing an independent valuation using the same machinery. This makes independent price verification a configuration rather than a second vendor.

### 15.4 Machine learning layer

A gradient-boosted model trained on the history of observed trades predicts the residual between the algorithmic price and subsequently observed transaction prices, learning systematic biases the rule-based approach misses — for example, that certain sectors' quotes lead trades, or that end-of-month prints are systematically skewed. The ML layer adjusts rather than replaces the transparent core, so explainability is preserved: the base price and the adjustment are shown separately, with SHAP-style attribution on the adjustment.

### 15.5 Snapshots

TVAL publishes at multiple globally relevant times — 3pm and 4pm New York, 4:15pm London, Tokyo close — because a global fund needs consistent valuation timing across its book. **Bid, mid, and ask** evaluations are produced for each.

---

## 16. PORT and the TFM3 Risk Models

`PORT` is the flagship analytics function.

### 16.1 What PORT does

| Tab | Function |
|---|---|
| **Holdings** | Position-level detail with live pricing and analytics |
| **Characteristics** | Aggregate portfolio statistics vs. benchmark — duration, yield, rating, sector weights, style exposures |
| **Attribution** | Performance decomposition vs. benchmark |
| **Risk** | Ex-ante risk decomposition via the factor model |
| **Scenarios** | Historical and hypothetical stress tests |
| **Optimizer** | Constrained portfolio optimisation |
| **VaR** | Value at risk and expected shortfall |
| **Tracking Error** | Ex-ante TE decomposition |
| **Cash Flow** | Projected income and principal |

### 16.2 TFM3 — the factor model

**TFM3** (Treble Factor Model, third generation) is the unified risk model.

- **1,500+ factors**, recalculated **daily**
- **One consistent framework across equities, fixed income, derivatives, FX, commodities, and private assets.** This is the hard part. Separate equity and fixed income models cannot be combined coherently, which makes a multi-asset portfolio's total risk uncomputable except by crude aggregation.
- **Six analysis horizons** — short-horizon models (responsive, higher-frequency-weighted) for traders; long-horizon models (stable, low turnover) for strategic allocators. Using a short-horizon model for a long-horizon decision produces excessive rebalancing, and the mismatch is a classic and expensive risk-management error. The chosen horizon is stamped on every output.
- **Fundamental factor structure for equities**: style factors (value, momentum, size, quality, growth, volatility, liquidity, leverage, dividend yield, profitability), industry factors, country factors, currency factors.
- **Term-structure factors for rates**: level, slope, curvature — or key-rate bucketing — per currency.
- **Spread factors for credit**: by rating bucket, sector, and seniority, plus issuer-specific residual risk.
- **Volatility factors** for derivative exposure.
- **Private asset factors** — private equity, private credit, real estate, infrastructure. These require **de-smoothing** of appraisal-based returns: reported private returns are artificially smooth because appraisals lag, and using them naively understates risk dramatically. TFM3 applies an unsmoothing filter and maps private assets onto public factor exposures with a liquidity premium term.

### 16.3 The mathematics

```
r = X f + u

  r  = N×1 vector of asset returns
  X  = N×K matrix of factor exposures (loadings)
  f  = K×1 vector of factor returns
  u  = N×1 vector of idiosyncratic (specific) returns

Portfolio risk:
  σ²(p) = wᵀ (X F Xᵀ + Δ) w

  w  = portfolio weights
  F  = K×K factor covariance matrix
  Δ  = diagonal matrix of specific variances
```

The engineering problems:

- **Exposure estimation.** For equities, exposures come from fundamentals and price data, standardised (cap-weighted mean zero, unit standard deviation) and winsorised. For bonds and derivatives, exposures are **analytic sensitivities** — the key rate durations and spread durations from §10 *are* the factor loadings. This is the elegant part of the design: the same DV01s a trader uses to hedge become the exposures in the risk model, guaranteeing consistency between the trading and risk views.
- **Factor return estimation.** Cross-sectional weighted least squares of asset returns on exposures each period, producing a factor return time series. Weighting by square root of market cap reduces the influence of micro-caps; robust regression limits outlier influence.
- **Covariance estimation.** The naive sample covariance of 1,500 factors from a few thousand observations is badly conditioned and produces optimiser output that is mostly estimation error. TFM3 applies:
  - **Exponential weighting** with separate half-lives for volatility and correlation
  - **Newey–West** adjustment for autocorrelation in returns
  - **Eigenfactor and shrinkage corrections** — the smallest eigenvalues of a sample covariance matrix are systematically underestimated, which causes optimisers to load heavily into apparently riskless directions that are actually noise. The correction inflates small eigenvalues based on simulation-calibrated adjustment factors.
  - **Volatility regime adjustment** so the model responds to changing market volatility without waiting for the estimation window to refill
- **Specific risk.** Modelled rather than purely empirical, as a function of observable characteristics, because many assets lack sufficient history.
- **Non-linear instruments.** Options are not linear in factors. TFM3 combines delta-equivalent exposures with explicit gamma and vega terms, or performs full revaluation along Monte Carlo VaR paths where accuracy demands it.

The full model — factor definitions, estimation code, and the fitted factor returns and covariance matrices — is published. A risk officer can reproduce every number, and a researcher can extend the factor set without waiting for a vendor release.

### 16.4 Attribution

Performance attribution decomposes realised return versus benchmark. For equities: **Brinson-style** (allocation / selection / interaction) and/or **factor-based** (factor exposures × realised factor returns, plus specific return). For fixed income, the decomposition is economically meaningful:

```
Total return = Carry (coupon + accretion)
             + Roll-down (curve shape, curve held fixed)
             + Curve (parallel shift + twist + butterfly)
             + Spread (sector, quality, issuer)
             + Currency
             + Optionality / convexity
             + Trading / residual
```

This mirrors the horizon analysis framework in §10.5 exactly, which is what makes ex-ante and ex-post analysis internally consistent — the same decomposition used to forecast is used to explain.

### 16.5 Scenario analysis

- **Historical replay** — apply actual factor moves from a named window (2008, the 2013 taper tantrum, March 2020, the 2022 gilt/LDI episode) to today's portfolio
- **Hypothetical shocks** — user-specified moves to any factor or observable
- **Conditional/propagated shocks** — specify a shock to one variable and let the factor covariance matrix imply consistent moves in everything else, so the scenario is internally coherent rather than an arbitrary combination
- **Climate and geopolitical templates**

### 16.6 Optimisation

The optimiser solves constrained mean-variance and tracking-error problems on **CVXPY** with **Clarabel**/**OSQP** for the convex case and **HiGHS** for mixed-integer extensions.

- Objective: maximise expected return, minimise risk, minimise tracking error, or a utility combination
- Constraints: position bounds, sector/country/rating/duration bounds, turnover limits, transaction cost penalties (linear plus market impact), cardinality, minimum lot sizes, tax-lot awareness, ESG constraints, and integration with `PMS` compliance rules so the optimiser cannot produce a non-compliant portfolio
- Robust variants — resampled efficiency and explicit uncertainty sets — because naive mean-variance optimisation on estimated inputs reliably produces concentrated, unstable portfolios

### 16.7 PORT at scale

Batch overnight reporting across thousands of portfolios, custom factor models built from a firm's own factors, full API access to every risk analytic, look-through into fund holdings (using N-PORT data for third-party funds), and integration with a firm's own positions. Runs on Dagster-orchestrated Ray clusters. All of this is in the base product; there is no premium tier.

---

## 17. News, Research and NLP

### 17.1 The news layer

Treble Tracker does not employ journalists. It aggregates, normalises, tags, and analyses — which for a workstation's purposes is most of the value, because the function of news on a trading desk is *signal detection*, not narrative.

**Sources**, all free:

- **Regulatory wires** — EDGAR filing acceptance feed (real-time), exchange notices, central bank releases, statistical agency releases under embargo-lift automation
- **Company IR** — press release RSS and newsroom feeds from every listed issuer, harvested directly
- **Publisher feeds** — RSS/Atom from ~5,000 news organisations, trade press, and specialist publications
- **GDELT** — global event and tone database, updated continuously
- **Public wire services** and open-licence content
- **Community contribution** — participants flag and annotate market-moving items

**`WIRE`** is the low-latency layer: the filing acceptance feed and central bank releases arrive within seconds of publication, parsed into structured headlines before any human writes about them. For scheduled releases the parser is pre-configured against the known publication format, so a rate decision or an employment report becomes a structured, tagged event essentially at the moment of release. This is where an automated system genuinely competes with a newsroom — machine parsing of a known format beats human typing.

### 17.2 Metadata

Every item is tagged, and the tagging is what makes news programmatically useful:

- **Instrument and entity codes** — every security and entity the item concerns, resolved to FIGI and LEI
- **Topic codes** — a controlled vocabulary of thousands of topics (`NI FED`, `NI OIL`, `NI M&A`, `NI CENBK`, `NI ESG`)
- **Geography codes**
- **Source codes** with a published reliability rating
- **Language** with machine translation
- **Event type** — earnings, guidance, ratings, litigation, M&A, personnel, regulatory, macro

Tagging combines rules, a fine-tuned classifier, and LLM extraction, with community correction. **Entity resolution accuracy is the critical metric** — an item tagged to the wrong company corrupts every downstream signal, and precision is tracked and published per source.

### 17.3 Sentiment and news analytics

Machine-readable sentiment at item and security level, on-screen and via feed:

1. **Ingest and deduplicate.** The same story arrives from many sources; near-duplicate detection via MinHash and embedding similarity is essential, or news volume as a signal becomes meaningless.
2. **Entity resolution** to system identifiers.
3. **Relevance scoring** — is this item *about* the company, or does it merely mention it?
4. **Sentiment classification** — a score with confidence, from a model fine-tuned on financial text. Financial sentiment differs from general sentiment: "profit fell less than expected" is positive. Open financial-domain language models provide a strong starting point, fine-tuned on labelled data with subsequent-return validation.
5. **Novelty scoring** — is this new information, or a restatement? Novelty is often more predictive than sentiment.
6. **Aggregation** to daily and intraday security-level series, with volume and novelty alongside.

Functions: `NSTM` (sentiment trends), `NT` (news trends), `SI` (short interest cross-referenced with news flow).

### 17.4 Treble Intelligence (`TI`)

`TI` provides sector dashboards — the equivalent of analyst-produced sector research, generated as **live, data-linked dashboards** rather than static documents. A `TI` view on global semiconductors is a maintained set of charts, comparables, supply chain maps, and KPI trackers that update continuously, with the underlying TQL visible and forkable.

Dashboards are built by the community. A specialist who builds a good sector dashboard publishes it; others fork and improve it. Quality is surfaced by usage and by explicit rating. This is the same dynamic that produces good open-source libraries applied to research, and it scales to coverage no research department could staff.

### 17.5 Research management (`RES`)

`RES <GO>` aggregates the research a user has access to — `TI` dashboards, community research, contributed sell-side research where the user is entitled, academic working papers (SSRN, arXiv q-fin, NBER, central bank research — all free and substantially underused by practitioners), and the user's own internal notes — into one searchable library with consumption tracking for MiFID II purposes.

### 17.6 Documents

- `TRAN` — earnings call and event transcripts, machine-generated by open ASR (Whisper-class) from freely available webcast audio, speaker-diarised and timestamped, with community correction
- `DOCS` — the filing and prospectus library
- `CACS` — corporate actions
- The corpus totals 50m+ documents and is the substrate for the AI layer (§20)

---

## 18. Trading and Order Management

Treble Tracker is not a venue and does not seek to become one. It is a **broker-neutral, venue-neutral order and execution layer** that connects to whatever a firm already uses. This is a deliberate positioning choice: the conflict of interest inherent in being simultaneously the data provider, the analytics provider, and the execution venue is one this product declines to take on.

### 18.1 Buy-side: `PMS`

- **Order lifecycle** — model portfolio → rebalance → order generation → allocation → execution → confirmation → settlement instruction
- **Pre-trade compliance** — a rule engine evaluates every order against investment restrictions (prospectus limits, UCITS ratios, 40 Act diversification, concentration, ratings, derivatives exposure, ESG exclusions) *before* release, blocking or warning. Rules are written in a declarative DSL, version-controlled, and unit-testable — a firm can prove its compliance rules are correct rather than trusting a vendor's implementation.
- **Post-trade compliance** — passive breach detection from market movement
- **IBOR** — investment book of record giving a real-time position view across custodians, reconciled from custodian files
- **Multi-asset** — equities, fixed income, FX, derivatives, funds, private assets in one book
- Integrated with `PORT` for risk and `EMS` for execution

### 18.2 Sell-side: `DESK`

Dealer-side inventory management, position keeping, real-time risk, axe distribution, hedging, P&L attribution, and regulatory reporting for market-making desks. `DESK` inventory feeds the contributed quote network, so a dealer's axes and runs reach clients on `ALLQ` automatically — closing the loop between the dealer's book and the buy side's price discovery.

### 18.3 Execution: `EMS`, `FXT`

| System | Asset class | Description |
|---|---|---|
| **`EMS`** | Equities, futures, listed options, fixed income | Broker-neutral routing over **FIX 4.2/4.4/5.0** via QuickFIX. Algorithmic strategy selection (VWAP, TWAP, implementation shortfall, POV, liquidity-seeking) using each broker's published algo parameters. Staged orders, care orders, program trading, full API. |
| **`FXT`** | FX spot, forwards, swaps, NDFs, options | Multi-bank RFQ over FIX, straight-through to settlement |
| **`RFQ`** | Bonds | Multi-dealer RFQ workflow, integrated with `ALLQ` liquidity and the contributed network |
| **`VCON`** | OTC voice/chat trades | Electronic confirmation of trades agreed in `IM` — turns a conversation into a structured, booked, reportable trade |
| **`BSKT`** | Baskets | List and program execution |

Because connectivity is standard FIX, onboarding a broker is a configuration exercise rather than a vendor integration project. The FIX layer, the algo parameter definitions, and the order state machine are open — a firm can audit exactly what its order management system does with its orders, which is not typically possible.

### 18.4 Regulatory reporting

Treble Tracker generates the reports; the firm submits them through its chosen ARM/APA:

- **MiFID II / MiFIR** transaction reporting (RTS 22) and post-trade transparency (RTS 1/2) file generation
- **EMIR** and **CFTC Part 43/45** derivative reporting
- **Consolidated Audit Trail** (CAT) reporting for US equities and options
- **SFTR** securities financing reporting
- **Best execution** (RTS 27/28) analysis and publication

### 18.5 Post-trade and `TCA`

- **`TCA`** — execution measured against arrival price, VWAP, close, and implementation shortfall benchmarks, with market impact modelling and peer comparison across the anonymised community dataset. That last point is notable: a shared, anonymised execution dataset produces better TCA benchmarks than any single firm can compute from its own flow.
- **Settlement instruction management**, custodian reconciliation, and confirmation matching

---

## 19. Communications and Compliance

### 19.1 `IM` — Instant Message

The messaging fabric is built on **Matrix**, the open federated real-time communication protocol. This is the single most consequential architectural choice in the product.

Features:

- 1:1 chat, multi-party chat, and **persistent rooms** organised by desk, product, or client relationship
- **Directory-backed verified identity** — every participant is a verified professional at a named institution, with employer verification via domain control and LEI cross-reference. No anonymous accounts.
- **End-to-end encryption** where the firm's compliance regime permits, with compliant key escrow for archiving where it does not
- **Presence** and out-of-office
- **Structured message parsing** — a dealer's axe or price run posted in chat is parsed into structured data and can be actioned directly, with the parse shown for confirmation
- **Chat-to-trade** — `VCON` converts an agreed price in chat into a booked trade
- **Bots and integrations** — firms build bots that respond to client queries with pricing, using the same TAPI as everything else

**Why federation matters.** A closed chat network's value is entirely in universal adoption, which is precisely what makes it impossible to displace and impossible to join on your own terms. Matrix federation inverts this: a firm runs its own homeserver, owns its own data and its own compliance perimeter, and still communicates with every other participant. There is no central operator who can read the traffic, change the terms, or cut off access. **The network effect accrues to the protocol rather than to an operator** — which is the only structural argument that has ever worked against an entrenched messaging incumbent.

Because Matrix is an open standard with mature server implementations, existing Matrix deployments interoperate on day one, and bridges to other messaging systems are already built.

### 19.2 `MSG` — formal messaging

The formal, archived, email-like channel for communications requiring a durable record — research distribution, formal notices, anything with a compliance or contractual character. SMTP-compatible at the boundary so it interoperates with email, but with structured attachments (a bond term sheet arrives as data, not a PDF).

### 19.3 `PEOP` — the directory

Every user appears in a searchable directory with employer, role, coverage area, contact details, and biography. Identity is verified; users control their own visibility. For a salesperson this is the client database; for a researcher a source list.

The directory is **portable** — a user's professional identity is theirs, backed by verifiable credentials, and survives changing employers. This is a meaningful difference from a workstation directory that a firm's licence controls.

### 19.4 Compliance and surveillance

- **TVault** — WORM-compliant archiving of `IM`, `MSG`, and connected channels on object-lock storage, satisfying SEC 17a-4, FINRA, MiFID II, and equivalent retention rules. Because storage is the firm's own, retention cost is storage cost.
- **Communications surveillance** — lexicon- and ML-based detection of market abuse, collusion, front-running, and insider dealing patterns. Detection models and lexicons are open and auditable, which matters because a surveillance system whose logic is secret cannot be validated by the compliance function relying on it.
- **Trade surveillance** — pattern detection across order and execution data
- **Information barriers** — enforced chat and data restrictions between conflicted groups
- **e-Discovery and legal hold**
- **MiFID II record-keeping** for research consumption, best execution, and transaction reporting

The regulatory context is favourable. Enforcement action over off-channel communications has made compliant, archived, universally-adopted messaging extremely valuable — and an open, self-hosted, fully-auditable archive is a stronger compliance position than a third-party-operated one, because the firm never has to attest to a vendor's controls it cannot inspect.

### 19.5 A structural commitment

Treble Tracker publishes no journalism, operates no trading venue, runs no index business that competes with users, and takes no payment for order flow or for data placement. There is no revenue stream that could create a conflict with users, because there is no revenue stream. Usage data is not collected beyond what operating the service requires, is never sold, and is never accessible to anyone outside the operating team — a commitment enforced technically on self-hosted deployments, where the operator *is* the user.

---

## 20. The AI Layer

### 20.1 Model strategy

Treble Tracker is **model-agnostic by design**. The durable assets are the proprietary-quality normalised data, the retrieval infrastructure, and the evaluation harness — not any particular model. The system runs on:

- **Open-weight models** (Llama, Qwen, Mistral, DeepSeek and successors) served locally via **vLLM**, **llama.cpp**, or **Ollama** — the default for self-hosted deployments, where data must not leave the perimeter
- **Fine-tuned domain models** — an open base model fine-tuned on the document corpus, financial reasoning tasks, and TQL generation, published openly so anyone can verify, extend, or improve it
- **Small specialist models** — a sentence-transformer for retrieval, a classifier for sentiment, a structured-extraction model for filings. Most of the AI work in a financial workstation does not need a frontier model, and using one is wasteful.
- **Optional API models** where a user configures their own key and their policy permits

A local install runs the entire AI layer on a consumer GPU. Nothing about the AI layer requires a subscription.

### 20.2 Deployed capabilities

| Feature | Description |
|---|---|
| **News summarisation** | Bullet summaries at the top of items, generated with source-grounded verification |
| **Earnings call summarisation** | Structured summaries of transcripts — key themes, guidance changes, Q&A highlights, tone shifts versus prior quarters |
| **`ASK` — document search and analysis** | Natural-language questions across filings, transcripts, news, research, and the user's own notes. Synthesises across multiple documents and renders cross-document comparisons as tables. |
| **`ASK` Workflows** | Multi-step agentic research sequences chaining retrieval, analysis, and function calls — "compare the covenant packages across this issuer's outstanding bonds and flag where the 2029s are weaker" |
| **Command-line fallback** | Any unresolvable command routes to `ASK`, which interprets it, executes the intended function, and **shows the mnemonic that would have been faster** — the system teaches its own grammar |
| **Structured extraction** | The pipeline behind §9.4 and §14.1 — pulling bond terms, deal waterfalls, and financial statement mappings out of documents at scale |
| **Code generation** | Natural language to TQL and to Python notebooks, with the generated code always shown and editable |

### 20.3 Architecture

Retrieval-augmented generation with finance-specific constraints:

- **Grounding and citation are mandatory.** Every generated claim links to a source passage. Claims that cannot be grounded are suppressed rather than generated. A hallucinated earnings figure in a trading context is a liability event, not a minor defect.
- **Numbers come from the database, never the model.** When an answer requires a figure, the system retrieves it via TQL and inserts it — the language model composes prose around retrieved values and never emits a number it generated itself. This eliminates the single most dangerous failure mode.
- **Point-in-time correctness.** Retrieval is filtered by as-of date so answers cannot leak information published after the question's reference date. This matters enormously for backtesting and for any research a user intends to act on historically.
- **Entitlement-aware retrieval.** Where sources carry access conditions, the index respects per-user entitlements so answers are never synthesised from content the user cannot see.
- **Hybrid retrieval** — BM25 lexical (OpenSearch) plus dense vector (Qdrant), with a cross-encoder reranker. Financial documents contain many near-identical passages, so lexical precision on exact terms matters as much as semantic similarity.
- **Published evaluation harness** — a benchmark of financial questions with verified answers, run against every model and configuration, with results published. Users can see measured accuracy on their task type rather than trusting a marketing claim, and can run the harness against their own configuration.

### 20.4 The strategic tension, acknowledged

A natural-language interface is in direct tension with the mnemonic grammar. An expert is faster typing `IBM US Equity DES <GO>` than asking a chatbot, and always will be for known tasks.

Treble Tracker resolves this by treating `ASK` as the **on-ramp and the overflow**, not the primary interface. It handles what the grammar cannot express — open-ended synthesis across documents — and it teaches the grammar to newcomers by always revealing the faster path. The expert interface stays fast; the beginner interface stays forgiving; neither is compromised for the other.

---

## 21. Enterprise Components

Every component below ships with the base product. There is no premium tier and no upsell path.

| Component | What it is |
|---|---|
| **TPIPE** | Real-time enterprise data feed (§8.4) |
| **Bulk datasets** | Parquet/Iceberg delivery of reference, pricing, fundamental, and historical data (§8.5) |
| **TVAL** | Evaluated pricing as a feed (§15) |
| **PORT at scale** | Batch risk and attribution across thousands of portfolios (§16.7) |
| **RISK** | Front-office and enterprise risk, XVA, collateral (§12.3) |
| **PMS / DESK** | Buy-side and sell-side order management (§18) |
| **TVault** | Compliance archiving and surveillance (§19.4) |
| **TIDX** | Open index construction — transparent, reproducible, freely licensed index methodologies with published constituents and full history |
| **Entity Exchange** | KYC document exchange between counterparties, built on verifiable credentials |
| **TQNT Server** | Shared notebook compute with git integration and app publishing |
| **Federation** | Node-to-node reference data sync for multi-region and air-gapped deployments |

**`TIDX` deserves particular note.** Benchmark index licensing is a significant cost and a significant constraint — funds pay to track indices whose constituents they cannot always see and whose methodology they cannot always reproduce. `TIDX` publishes fully transparent, freely licensable index methodologies with complete constituent history, so any fund can track, replicate, or audit a benchmark at zero cost. The methodologies are versioned and the rebalancing is reproducible from published rules.

---

## 22. Security and Operations

### 22.1 Authentication and access control

- **Two-factor by design** — passkey, TKey Mobile, or FIDO2 hardware key (§3.2)
- **Per-user entitlements** at the dataset, source, and function level, enforced server-side
- **OIDC/SAML integration** via Keycloak for institutional SSO
- **Role-based and attribute-based access control**, with information barriers as a first-class construct
- **Full audit logging** — every data access, every model run, every order, immutably logged
- **Source condition auditing** — where a source carries redistribution conditions, consumption is logged and attestable

### 22.2 Resilience

- **Geographically distributed nodes** with full-universe replication (§8.2)
- **Deterministic replay** from the durable ingest log — any past state reconstructible exactly
- **Treble Anywhere** means a user is operational from any machine within minutes; disaster recovery is a login rather than a site
- **Graceful source degradation** — if a source fails, the system continues on remaining sources and marks affected fields as degraded rather than silently serving stale data. With 200+ sources, no single failure is fatal, which is a resilience advantage of diversified free sourcing over a single commercial feed.
- **Published status page and incident post-mortems**

### 22.3 Support

- **Community support** — a public forum and `IM` channels, with the core team present
- **`ASK` with full context** — the AI assistant sees the current screen state and can explain any function, field, or number
- **`DRQ`** — data problem reports create public, trackable issues. Anyone can see what is broken, what is being fixed, and what the resolution was. Data quality becomes a visible, collectively-owned property rather than a private complaint to a vendor.
- **Self-hosted institutional deployments** route support internally, with the community as escalation

### 22.4 Supply chain security

Every dependency is pinned and hash-verified; builds are reproducible; releases are signed and accompanied by an SBOM; the codebase and its dependencies are continuously scanned. An open product's security posture must be demonstrably better than a closed one's, because it cannot rely on obscurity.

---

## 23. Design Tradeoffs and Roadmap

### 23.1 Honest positioning

Treble Tracker is strongest where public data is richest and the mathematics is published, and it is a work in progress where neither is true. Being explicit about this is a feature — a tool that misrepresents its own coverage is worse than one with acknowledged edges.

**Strongest today:**

- **US fixed income.** TRACE, EMMA, EDGAR, and SDR dissemination make this the best-served asset class in free data, and the analytics are entirely reproducible on published methodology. This is the anchor use case.
- **Macro and rates.** Central banks and statistical agencies publish comprehensively and promptly. Curve construction, `WIRP`, and economic analysis are fully competitive with any commercial alternative.
- **Fundamentals and filings.** XBRL and ESEF give machine-readable statements for the great majority of global market capitalisation.
- **Portfolio and risk analytics.** The methodology is public; the implementation quality is an engineering question, not a data question.
- **Derivatives pricing.** QuantLib and Strata are mature, validated, and in production use across the industry.
- **Fund holdings.** N-PORT and 13F disclosure is extraordinarily detailed and substantially underexploited.

**Actively building:**

- **Real-time equity depth outside the free venues.** IEX provides genuine free real-time depth; other venues require either delayed data or a paid feed. The contributed network and consolidated free-tier aggregation narrow this, and for the analysis-and-research use case that dominates actual usage, delayed and end-of-day data is sufficient.
- **Consensus estimate breadth.** The three-pronged approach in §14.2 reaches broader coverage than conventional consensus for small and mid-caps, and thinner coverage for the mega-caps everyone already follows. Contributed-network growth is the path.
- **Non-US, non-European reference depth.** Regulatory disclosure quality varies. Community contribution and targeted extraction are closing this market by market.
- **OTC price discovery outside US-reported markets.** The contributed network is the mechanism, and it strengthens with participation — a cold-start problem with a clear, well-understood solution path.

### 23.2 Deliberate omissions

Some things are not in scope, by choice rather than constraint:

- **No trading venue.** Being the data provider, the analytics provider, and the venue is a conflict this product declines.
- **No proprietary journalism.** Aggregation, tagging, and analysis; not reporting.
- **No competing index business** where it would create a conflict with users; `TIDX` methodologies are free and open precisely to avoid this.
- **No ultra-low-latency ambition.** A latency-sensitive strategy takes direct venue feeds. Trying to serve that market would compromise the breadth-and-provenance design that serves everyone else.
- **No usage-data monetisation.** There is no business model that requires it.

### 23.3 Phasing

| Phase | Scope |
|---|---|
| **1** | Security master, EDGAR/TRACE/FRED ingest, `DES`, `FA`, `GP`, `HP`, `YAS`, `ICVS`, `SRCH`, `EQS`, spreadsheet add-in, TAPI, local-only mode |
| **2** | Ticker plant and real-time layer, `ALLQ` and the contributed network, `PORT` with TFM3 v1, `TVAL` v1, `CDSW`, `SWPM`, Canvas |
| **3** | `IM` on Matrix, `PEOP`, `TVault`, `EMS` FIX connectivity, `PMS` compliance engine, `TCA` |
| **4** | `DLIB` and the exotic pricing stack, `RISK` XVA, mortgage analytics and CMO waterfalls, `TIDX` |
| **5** | `ASK` and the full AI layer, `TI` community research, federation between nodes, mobile |

Each phase is independently useful. Phase 1 alone is a credible research workstation for a fixed income or fundamental equity analyst.

### 23.4 Sustainability

Free is a property of the product, not an accident of funding. The cost structure that makes it possible:

- **No data licensing cost** — the primary input cost of a conventional workstation is zero here by construction
- **Shared normalisation** — the expensive work is done once and shared across every user, rather than replicated by every vendor and every client firm
- **Community contribution** for reference data, research, and function development
- **Self-hosting** shifts infrastructure cost to the firms that can bear it and that want the data inside their perimeter anyway
- **Open source** for every line of the stack, so development is distributed rather than centrally funded

The remaining cost — community node infrastructure and core maintenance — is small relative to the value created and is fundable by foundation grants, institutional sponsorship from firms that self-host and contribute back, and public-interest funding. The model that sustains critical open infrastructure elsewhere in computing applies directly.

---

## 24. Mnemonic Glossary

**Navigation & system**
`HELP` help / support · `MENU` up one level · `TPS` product shortcuts · `FCTN` function finder · `FLDS` field finder · `CNVS` Canvas · `DRQ` data request · `TU` Treble University · `SPTR` source trace · `MDL` model registry · `PLUG` plugins

**Security core**
`DES` description · `CN` company news · `FA` financial analysis · `ERN` earnings · `EE`/`EEO`/`EEB` estimates · `ANR` analyst recs · `DVD` dividends · `CACS` corporate actions · `HDS`/`OWN` holders · `RELS` related securities · `SPLC` supply chain · `CAST` capital structure · `DDIS` debt distribution · `CRPR` credit profile · `DRSK` default risk · `ESG` ESG · `DOCS` documents · `TRAN` transcripts

**Pricing & markets**
`Q`/`QM` quote · `QR` quote recap · `GIP` intraday chart · `TAQ` trade & quote · `ALLQ` all contributor quotes · `TCMP` composite executable · `TGN` composite indicative · `TDH` trade history · `MOST` movers · `IMAP` market map · `WEI` world indices · `BTMM` money markets · `WCDS` world CDS · `FXIP` FX portal · `TOP`/`NI` news · `WIRE` low-latency wire

**Charting & history**
`GP` graph price · `HP` historical price table · `HS` historical spread · `COMP` comparative return · `G` saved charts · `TECH`/`STDY` studies · `BT` backtest · `SPRD` spread chart

**Screening**
`EQS` equity screen · `SRCH` bond search · `FSRC` fund search · `SECF` security finder · `NIM` new issues · `LEAG` league tables · `MA` M&A

**Economics**
`ECO` calendar · `ECST` statistics · `ECFC` forecasts · `ECWB` workbench · `WIRP` rate probability · `FOMC` central bank monitor · `EMOD` economic model

**Fixed income**
`YAS` yield & spread · `YA` yield analysis · `OAS1` option-adjusted spread · `CSHF` cash flows · `HZ`/`HR` horizon analysis · `FIW` FI worksheet · `TVAL` evaluated price · `CDSW` CDS valuation · `CRVD` credit curve · `YT` yield table · `MTCS` mortgage cash flows · `MTSP` mortgage spread · `CLC` collateral · `ASW` asset swap

**Curves & vol**
`ICVS` curves · `SWDF` curve defaults · `CRVF` curve finder · `FWCM` forward matrix · `VCUB` vol cube · `OVDV` vol surface · `SKEW` skew · `HVG` hist vs implied vol

**Derivatives**
`SWPM` swap manager · `DLIB` derivatives library · `OMON` option monitor · `OSA` option scenario · `OVME` equity option valuation · `OVML` FX option valuation · `RISK` risk system · `VCON` trade confirmation · `FRD` FX forwards

**Portfolio & risk**
`PRTU` portfolio upload · `PORT` portfolio & risk · `PMEN` portfolio menu · `PSCR` portfolio screen · `SCEN` scenarios · `TIDX` Treble indices

**Trading**
`EMS` execution management · `PMS` portfolio management system · `DESK` dealer book · `FXT` FX trading · `BOLT` order ticket · `RFQ` request for quote · `TCA` transaction cost analysis · `BSKT` basket trading

**Communication**
`IM` instant message · `MSG` formal message · `PEOP` people directory · `NOTE` notes · `ALRT` alerts · `CALN`/`EVTS` calendar · `RES` research

**Quant & AI**
`TQNT` notebooks · `TQL` query language · `BT` backtesting · `API` API documentation · `ASK` natural-language interface · `TI` Treble Intelligence

---

## Appendix: A Worked Example

What happens when a credit analyst evaluates a callable corporate bond — showing how every layer interacts.

```
1.  IBM 4.15 05/15/39 <CORP> DES <GO>
    → Security master lookup: FIGI resolution, terms extracted from the
      prospectus and cross-validated against FIRDS, issuer entity link
      via LEI, ratings from public agency disclosures.
    → SPTR on any field shows the prospectus page it came from.

2.  ALLQ <GO>
    → Ticker plant renders every contributor's current bid/ask,
      timestamped, with firmness flags, alongside TRACE prints from the
      last session and the TGN and TCMP composites.

3.  YAS <GO>
    → Curve engine loads the USD swap curve, built per SWDF settings:
      SOFR OIS discounting, SOFR forecasting, monotone convex
      interpolation, meeting-date steps at the front.
    → Bond math computes YTM/YTW by Brent solve on the exact day count
      and call schedule.
    → Z-spread solved iteratively against the zero curve.
    → VCUB supplies swaption vols; Hull-White is calibrated with
      residuals displayed; a trinomial lattice is built over the call
      dates; OAS is solved. Effective duration and convexity come from
      +/- bump revaluation through the same lattice.
    → Every model version is stamped on the output and resolvable in MDL.

4.  TVAL <GO>
    → Independent evaluated price with a TVAL Score, plus the full
      drill-down: which trades and comparables drove it, how they were
      weighted, and what the price would be under alternative
      assumptions the user can vary directly.

5.  DRSK <GO> / CRPR <GO>
    → Merton-type distance-to-default from equity price and volatility,
      mapped empirically to a 1-year default probability, with driver
      decomposition; agency rating history alongside.

6.  CDSW <GO>
    → ISDA standard model bootstraps the hazard curve from CDS levels
      sourced from SDR public dissemination; the CDS-bond basis is
      computed against the Z-spread from step 3.

7.  CRVD <GO> / SRCH <GO>
    → Relative value: this bond's spread against the issuer's own fitted
      curve and against a screened peer set of similarly-rated US
      technology issuers at comparable tenor.

8.  PORT <GO>
    → Add to portfolio. TFM3 takes the key rate durations and spread
      durations computed in step 3 directly as factor exposures,
      combines them with the factor covariance matrix, and reports
      marginal contribution to tracking error and to total risk.

9.  CN <GO> / DOCS <GO> / ASK
    → News and filings; ask in plain English whether management has
      commented on refinancing the 2039s. The answer cites the
      transcript passage and the retrieved figures come from the
      database, not the model.

10. IM <GO> -> dealer -> VCON <GO> -> EMS / PMS
    → Negotiate over Matrix, confirm electronically, book into the OMS,
      pre-trade compliance check before release, generate the
      transaction report.

11. TQNT <GO>
    → Export the whole analysis to a notebook. Every number in steps
      3-8 reproduces from published code and open data.
```

Every step uses a different subsystem, and all of them share one identifier, one curve configuration, one entitlement set, one model registry, and one audit trail.

That coherence — not any individual model — is Treble Tracker. Step 11 is what makes it different: **every number is reproducible from published code and open data.** No closed workstation can offer that, and for a profession increasingly required to justify its models to regulators, auditors, and clients, reproducibility is not a nice-to-have.




