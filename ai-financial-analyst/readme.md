# 📊 AI Financial Analyst — Indian Market

A fully local, privacy-first equity research tool. Upload any Indian company's quarterly or annual results PDF and get an instant, structured analyst report — powered by a local LLM via Ollama. No API keys. No data leaves your machine.

---

## 🖥️ Demo

![App Screenshot](screenshot.png)

> Upload → Extract → Analyse → Report in ~30 seconds

---

## 🏗️ Architecture

```
PDF Upload
    │
    ▼
pdf_parser.py          — Extracts text + pipe-delimited [TABLE] blocks
    │
    ├──► kpi_extractor.py   — Pulls Revenue, PAT, EBITDA/Op.Profit, EPS
    │         │               with unit normalisation (₹ Crore)
    │         ▼
    └──► prompt_builder.py  — Builds sector-aware LLM prompt
              │               (Banking / IT / FMCG / General)
              ▼
         analyzer.py        — Sends prompt to Ollama (Mistral)
              │               Retry logic + streaming support
              ▼
         app.py             — Streamlit UI
                              Left panel: AI analysis report
                              Right panel: KPI cards + trend chart
```

---

## 📁 File Reference

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI — upload, orchestration, display |
| `pdf_parser.py` | PDF → clean text + `[TABLE]` blocks via pdfplumber |
| `kpi_extractor.py` | Extracts KPIs from text and table blocks |
| `prompt_builder.py` | Builds the structured LLM prompt |
| `analyzer.py` | Ollama HTTP client with retry and streaming |

---

## ⚙️ How Each Module Works

### `pdf_parser.py`
- Uses `pdfplumber` for both plain text and table extraction
- Preserves newlines so table rows stay intact (critical for KPI extraction)
- Outputs `[TABLE — Page N] ... [/TABLE]` blocks with pipe-delimited cells
- Strips boilerplate: CIN numbers, addresses, auditor signatures, phone/fax
- Raises a clear `RuntimeError` for scanned/image-only PDFs

### `kpi_extractor.py`
- Splits the document into `TABLE` sections and `TEXT` sections
- **TABLE sections** (highest priority): matches keyword in cell[0], reads cell[1..N] as column values — clean, no row-number noise
- **TEXT sections** (fallback): line scan with 4-digit floor to block row-index integers
- **Inline scan** (Pass 3): catches prose mentions like "net profit of Rs X crore"
- Detects document-level unit (`₹ in crore` / `₹ in lakhs`) from header and propagates it to all values
- Returns `ExtractedKPIs` object with `.revenue`, `.net_profit`, `.ebitda`, `.eps` — each a list of `KPIValue` with `.raw`, `.unit`, `.normalised_cr`, `.context`
- Call `.to_prompt_dict()` to get the dict for `build_prompt()`
- Call `.summary()` for a debug printout

### `prompt_builder.py`
- Builds a sector-adaptive prompt: Banking gets NIM/NPA/CAR sections; IT gets utilisation/attrition; FMCG gets volume/margin; others get standard Revenue/EBITDA/PAT
- Multi-period KPI block: surfaces Q_current + prior periods so the LLM can compute QoQ/YoY growth
- Table-first text slice: complete `[TABLE]` blocks are always included before plain text within the 5000-char budget
- `EBITDA` is relabelled `Operating Profit` for banking documents (EBITDA is not a meaningful metric for banks)

### `analyzer.py`
- Sends prompts to Ollama's `/api/generate` endpoint
- Configurable via env vars: `OLLAMA_URL`, `OLLAMA_MODEL`
- 3 retries with exponential back-off (2s → 4s → 8s) for transient failures
- `check_model_available()` pre-checks that Ollama is running and the model is pulled before the first PDF is uploaded
- `num_ctx: 8192` set explicitly — prevents silent prompt truncation
- `analyze_financials_stream()` yields tokens for live Streamlit output via `st.write_stream()`

### `app.py`
- 5-step pipeline with individual spinners: PDF parse → company/sector detect → KPI extract → LLM analysis
- Company name: LLM extraction first, rule-based fallback if LLM fails
- KPI panel: metric cards with period-over-period delta for Revenue, PAT, and EPS; line chart for revenue trend (only when ≥2 normalised values exist)
- Debug expander: shows raw extraction output and the exact dict sent to the LLM
- Error boundaries at every step — `st.stop()` on failure with a user-friendly message

---

## 🚀 Setup & Installation

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com) installed and running
- Mistral model pulled

### 1. Install Ollama and pull the model

```bash
# Install Ollama (macOS / Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull Mistral
ollama pull mistral

# Verify it's running
ollama serve
```

### 2. Clone and install dependencies

```bash
git clone https://github.com/yourname/ai-financial-analyst.git
cd ai-financial-analyst

pip install -r requirements.txt
```

### 3. Run the app

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## 📦 requirements.txt

```
streamlit>=1.35.0
pdfplumber>=0.10.0
requests>=2.31.0
```

---

## 🔧 Configuration

All configuration is via environment variables — no code changes needed.

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `mistral` | Model to use for analysis |

```bash
# Example: use a different model
OLLAMA_MODEL=llama3:8b streamlit run app.py

# Example: remote Ollama instance
OLLAMA_URL=http://192.168.1.100:11434 streamlit run app.py
```

---

## 📊 Supported KPIs

| KPI | Keywords matched | Unit |
|---|---|---|
| Revenue | Total Income, Revenue from Operations, Net Sales, Turnover | ₹ Crore |
| Net Profit (PAT) | Net Profit for the period, Profit After Tax, PAT | ₹ Crore |
| EBITDA / Operating Profit | Operating Profit before provisions, EBITDA, PBDIT | ₹ Crore |
| EPS | Basic EPS, Diluted EPS, Earnings Per Share | ₹ per share |

All values are normalised to **₹ Crore** regardless of how the document states the unit (Crore, Lakh, Million).

---

## 🏦 Sector Detection

The app auto-detects the sector and adapts the analysis template:

| Sector | Special focus |
|---|---|
| Banking / NBFC | NIM, Gross NPA %, Net NPA %, Capital Adequacy Ratio |
| Information Technology | EBIT margin, utilisation rate, attrition, deal TCV |
| FMCG / Consumer | Volume vs price growth, gross margin, input costs |
| Pharma / Auto / Infra | Standard Revenue / EBITDA / PAT template |

---

## 📝 Analysis Report Structure

Every report follows this structure:

```
🏢 Business Overview     — Company name, sector, what it does
📊 Revenue Trend         — Latest revenue, QoQ/YoY growth
💰 Profitability         — PAT, EBITDA/Op.Profit, margins, EPS
                           (+ NPA/CAR for banking)
⚡ Key Highlights        — 3–5 data-backed insights
⚠️  Risks & Red Flags    — Specific figures for any negatives
🧠 Final Verdict         — Bullish / Neutral / Bearish
                           + Confidence level (High/Medium/Low)
                           + 2–3 line justification with numbers
```

---

## ⚠️ Known Limitations

- **Scanned PDFs**: Image-only PDFs cannot be parsed. The app detects this and shows an error. Use `ocrmypdf` to pre-process scanned documents.
- **Complex layouts**: Multi-column PDFs or PDFs with rotated text may produce garbled extraction. Check the debug expander to verify extracted KPIs.
- **Model quality**: Mistral 7B is capable but may hedge on limited data. For higher accuracy consider `llama3:70b` or `qwen2.5:32b` if you have the VRAM.
- **Consolidated vs Standalone**: The extractor picks up whichever table appears first in the PDF. For HDFC-style filings, this is the Standalone P&L.
- **Historical data only**: This tool analyses data present in the uploaded PDF. It does not fetch live market data or compare against peers.

---

## 🔍 Debugging KPI Extraction

If KPIs show wrong values, expand the **🔍 Raw Extraction Debug** panel in the app. It shows:

1. The raw `KPIValue` objects with `.context` snippets — you can see exactly which line each number came from
2. The exact dict passed to the LLM prompt

You can also run the extractor directly:

```bash
python kpi_extractor.py
# Runs the built-in self-test against a sample HDFC-style table
```

And the PDF parser:

```bash
python pdf_parser.py path/to/your.pdf
# Prints the first 3000 chars of extracted text including [TABLE] blocks
```

---

## 🗺️ Roadmap

- [ ] Multi-PDF comparison (Q1 vs Q2 vs Q3 in one shot)
- [ ] Camelot integration for better table detection on complex PDFs
- [ ] OCR support for scanned documents via `ocrmypdf`
- [ ] PDF export of the analysis report
- [ ] Confidence scoring on extracted KPIs
- [ ] Peer comparison (upload multiple company PDFs)

---

## 📄 License

For educational and personal use only. Not financial advice.

---

## 🙏 Built With

- [Streamlit](https://streamlit.io) — UI framework
- [pdfplumber](https://github.com/jsvine/pdfplumber) — PDF parsing and table extraction
- [Ollama](https://ollama.com) — Local LLM inference
- [Mistral 7B](https://mistral.ai) — Default analysis model