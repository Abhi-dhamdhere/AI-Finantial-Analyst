def build_prompt(financial_text: str, kpis: dict = None) -> str:
    """
    Build a structured, data-driven prompt for Indian stock market financial analysis.

    Args:
        financial_text: Raw financial text extracted from reports/documents.
        kpis: Optional dict of extracted KPIs. Supported keys:
              "Revenue", "Net Profit", "EBITDA", "EPS"

    Returns:
        A fully formatted prompt string ready to send to an LLM.
    """
    if kpis is None:
        kpis = {}

    # ── KPI block ──────────────────────────────────────────────────────────────
    revenue  = kpis.get("Revenue",    "Not extracted")
    profit   = kpis.get("Net Profit", "Not extracted")
    ebitda   = kpis.get("EBITDA",     "Not extracted")
    eps      = kpis.get("EPS",        "Not extracted")

    has_kpis = any(v not in ("Not extracted", None, [], {}, "") for v in [revenue, profit, ebitda, eps])

    kpi_block = f"""
┌─────────────────────────────────┐
│     VERIFIED / EXTRACTED KPIs   │
└─────────────────────────────────┘
  Revenue    : {revenue}
  Net Profit : {profit}
  EBITDA     : {ebitda}
  EPS        : {eps}
""" if has_kpis else "  ⚠️  No structured KPIs were pre-extracted. Rely on the raw text below.\n"

    # ── Raw text (token-safe slice) ─────────────────────────────────────────────
    text_sample = financial_text[:4000]

    # ── Final prompt ────────────────────────────────────────────────────────────
    prompt = f"""
You are a professional equity research analyst specialising in the Indian stock market.
Your task: produce a STRICT, DATA-DRIVEN financial analysis report using ONLY the data provided below.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                 GROUND RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅  Use ONLY data present in this prompt (KPIs + raw text).
✅  Always PRIORITISE the VERIFIED KPIs over the raw text if they conflict.
✅  Use real numbers wherever visible; show % change when calculable from given data.
✅  Be concise, precise, and analytical — write like a real sell-side analyst.

❌  Do NOT use prior knowledge or external assumptions.
❌  Do NOT invent numbers, ratios, segments, or trends.
❌  Do NOT derive/calculate new metrics unless the formula inputs are explicitly given.
❌  Do NOT repeat raw data verbatim in the output.

If a data point is genuinely absent → write:
    "Not clearly available in the provided data"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
              FINANCIAL DATA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{kpi_block}
┌─────────────────────────────────┐
│   RAW FINANCIAL TEXT (context)  │
└─────────────────────────────────┘
{text_sample}

IGNORE from the raw text: addresses, CIN numbers, contact details, website URLs,
repeated headers, boilerplate legal text.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
           REQUIRED OUTPUT SECTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Use clean Markdown. Follow this structure exactly:

---

## 🏢 Business Overview
- What the company does (1–2 lines)
- Sector: (IT / Banking / FMCG / Pharma / etc.)

---

## 📊 Revenue Trend
- Latest revenue figure with period label
- YoY / QoQ growth % (only if derivable from provided data)
- Direction: growing / declining / flat — with evidence

---

## 💰 Profitability Analysis
- Net Profit (PAT) with period label
- EBITDA / operating margin (only if data is present)
- Margin trend commentary (only if visible in data)
- EPS (if available)

---

## ⚡ Key Highlights
- 3–5 bullet points covering the most important financial or business signals

---

## ⚠️ Risks & Red Flags
- Declining revenue or profit (cite figures)
- Rising costs or margin compression
- Any other negative signals visible in the data
- If none visible → state "No major red flags identified in the provided data"

---

## 🧠 Final Verdict  *(MANDATORY)*
**Stance:** choose exactly one →  Bullish 🟢  |  Neutral 🟡  |  Bearish 🔴

**Confidence Level:** High / Medium / Low  

**Reasoning (2–3 lines):**
- Must reference actual data
- No vague statements

---

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         THINKING STYLE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Think like a sell-side analyst
- Prefer facts over storytelling
- Avoid repetition
- Keep output clean and readable

---
"""

    return prompt