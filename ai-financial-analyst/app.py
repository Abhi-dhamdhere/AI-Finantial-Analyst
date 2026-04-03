"""
app.py — AI Financial Analyst (Indian Market)
Compatible with the updated kpi_extractor.py (ExtractedKPIs object)
and prompt_builder.py.
"""

import tempfile
import streamlit as st

from pdf_parser import extract_text_from_pdf
from prompt_builder import build_prompt
from analyzer import analyze_financials
from kpi_extractor import extract_kpis, ExtractedKPIs


# ─────────────────────────────────────────────────────────────────────────────
# Company name helpers
# ─────────────────────────────────────────────────────────────────────────────

def extract_company_name_ai(text: str) -> str | None:
    """Ask the LLM to extract the company name. Returns None on any failure."""
    prompt = (
        "Extract ONLY the company name from the financial report text below.\n"
        "Rules:\n"
        "- Return ONLY the company name — nothing else.\n"
        "- No explanation, no punctuation, no extra words.\n\n"
        f"Text:\n{text[:1500]}"
    )
    try:
        result = analyze_financials(prompt)
        if not result:
            return None
        result = result.strip().strip('"').strip("'")
        # Reject if it looks like an error message or is absurdly long
        if not result or len(result) > 120 or "error" in result.lower():
            return None
        return result
    except Exception:
        return None


def extract_company_name_rule(text: str) -> str:
    """Regex/rule-based fallback for company name."""
    keywords = ["ltd", "limited", "bank", "technologies", "tech", "industries",
                "corp", "corporation", "solutions", "services", "enterprises"]
    for line in text.split("\n")[:40]:
        clean = line.strip()
        if 5 < len(clean) < 120 and any(k in clean.lower() for k in keywords):
            return clean
    # Last resort: first non-trivial line
    for line in text.split("\n")[:10]:
        if len(line.strip()) > 10:
            return line.strip()
    return "Unknown Company"


def get_company_name(text: str) -> tuple[str, bool]:
    """
    Returns (company_name, is_confident).
    Tries AI first, falls back to rule-based.
    """
    name = extract_company_name_ai(text)
    if name:
        return name, True
    return extract_company_name_rule(text), False


# ─────────────────────────────────────────────────────────────────────────────
# Sector detection
# ─────────────────────────────────────────────────────────────────────────────

_SECTOR_MAP: list[tuple[list[str], str]] = [
    (["bank", "nbfc", "non-banking", "microfinance", "housing finance",
      "insurance", "asset management", "brokerage"], "Banking / Financial Services"),
    (["fmcg", "biscuit", "food", "beverage", "dairy", "consumer goods",
      "personal care", "homecare", "packaged"], "FMCG / Consumer Goods"),
    (["software", "technology", "it service", "saas", "cloud", "digital",
      "infosys", "tcs", "wipro", "hcl"], "Information Technology"),
    (["pharma", "drug", "healthcare", "hospital", "diagnostic",
      "medicine", "biotech", "generics"], "Pharmaceuticals / Healthcare"),
    (["steel", "cement", "infra", "construction", "power", "energy",
      "mining", "metal", "realty", "real estate"], "Infrastructure / Core Industries"),
    (["automobile", "auto", "vehicle", "tyre", "ancillary"], "Automobile"),
    (["telecom", "telecommunication", "broadband", "spectrum"], "Telecom"),
    (["oil", "gas", "petroleum", "refinery", "lng", "crude"], "Oil & Gas"),
    (["retail", "e-commerce", "ecommerce", "supermarket", "hypermarket"], "Retail"),
]


def detect_sector(text: str) -> str:
    text_lower = text.lower()
    for keywords, sector in _SECTOR_MAP:
        if any(k in text_lower for k in keywords):
            return sector
    return "Diversified / Other"


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def render_kpi_card(label: str, value: str | None, delta: str | None = None):
    """Render a single KPI metric or a 'not available' message."""
    if value:
        st.metric(label=label, value=value, delta=delta)
    else:
        st.markdown(f"**{label}:** _Not found in document_")


def render_kpi_panel(kpis: ExtractedKPIs):
    """Right-panel KPI display — fully compatible with ExtractedKPIs object."""
    st.markdown("## 📊 Extracted KPIs")
    st.markdown("---")

    # ── Revenue ──────────────────────────────────────────────────────────────
    st.markdown("### 💰 Revenue")
    if kpis.revenue:
        primary = kpis.revenue[0]
        delta_str = None
        if len(kpis.revenue) >= 2:
            prev = kpis.revenue[1]
            # Compute delta only if both are normalised
            if primary.normalised_cr is not None and prev.normalised_cr is not None:
                diff = primary.normalised_cr - prev.normalised_cr
                delta_str = f"₹{diff:+,.2f} Cr vs prev period"
        render_kpi_card("Latest Revenue", primary.display(), delta_str)

        # Trend chart — only when we have ≥2 normalised values
        normalised = [v.normalised_cr for v in kpis.revenue if v.normalised_cr is not None]
        if len(normalised) >= 2:
            st.markdown("**Revenue Trend (₹ Cr)**")
            st.line_chart(normalised, use_container_width=True)
    else:
        st.write("_Revenue not found in document_")

    st.markdown("---")

    # ── Net Profit ────────────────────────────────────────────────────────────
    st.markdown("### 📈 Net Profit (PAT)")
    if kpis.net_profit:
        primary = kpis.net_profit[0]
        delta_str = None
        if len(kpis.net_profit) >= 2:
            prev = kpis.net_profit[1]
            if primary.normalised_cr is not None and prev.normalised_cr is not None:
                diff = primary.normalised_cr - prev.normalised_cr
                delta_str = f"₹{diff:+,.2f} Cr vs prev period"
        render_kpi_card("Net Profit", primary.display(), delta_str)
    else:
        st.write("_Net Profit not found in document_")

    st.markdown("---")

    # ── EBITDA ────────────────────────────────────────────────────────────────
    st.markdown("### ⚙️ EBITDA / Operating Profit")
    if kpis.ebitda:
        render_kpi_card("EBITDA", kpis.ebitda[0].display())
    else:
        st.write("_EBITDA not found in document_")

    st.markdown("---")

    # ── EPS ───────────────────────────────────────────────────────────────────
    st.markdown("### 🔢 EPS")
    if kpis.eps:
        primary = kpis.eps[0]
        eps_display = f"₹{primary.raw:,.2f}"
        delta_str = None
        if len(kpis.eps) >= 2:
            diff = primary.raw - kpis.eps[1].raw
            delta_str = f"₹{diff:+,.2f} vs prev period"
        render_kpi_card("Earnings Per Share", eps_display, delta_str)
    else:
        st.write("_EPS not found in document_")

    st.markdown("---")

    # ── Debug expander ────────────────────────────────────────────────────────
    with st.expander("🔍 Raw Extraction Debug", expanded=False):
        st.code(kpis.summary(), language="text")
        st.markdown("**Prompt dict passed to LLM:**")
        st.json(kpis.to_prompt_dict())


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="AI Financial Analyst — Indian Market",
    page_icon="📊",
    layout="wide",
)

st.title("📊 AI Financial Analyst — Indian Market")
st.caption("Upload a quarterly / annual results PDF and get an instant equity research report.")
st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# File upload
# ─────────────────────────────────────────────────────────────────────────────

uploaded_file = st.file_uploader("📂 Upload Financial Results PDF", type=["pdf"])

if uploaded_file is not None:
    st.success(f"✅ Uploaded: **{uploaded_file.name}**")

    if st.button("🚀 Run Analysis", type="primary"):

        # ── Step 1: Save to temp file ─────────────────────────────────────────
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.read())
            file_path = tmp.name

        # ── Step 2: Extract text ──────────────────────────────────────────────
        with st.spinner("📄 Extracting text from PDF…"):
            try:
                text = extract_text_from_pdf(file_path)
            except Exception as e:
                st.error(f"❌ Failed to parse PDF: {e}")
                st.stop()

        if not text or len(text.strip()) < 100:
            st.error("❌ Could not extract meaningful text from this PDF. "
                     "It may be scanned/image-only. Try a text-based PDF.")
            st.stop()

        # ── Step 3: Meta — company name + sector ─────────────────────────────
        with st.spinner("🏦 Identifying company and sector…"):
            company_name, is_confident = get_company_name(text)
            sector = detect_sector(text)

        # ── Step 4: KPI extraction ────────────────────────────────────────────
        with st.spinner("🔢 Extracting financial KPIs…"):
            kpis: ExtractedKPIs = extract_kpis(text)

        # ── Step 5: LLM analysis ──────────────────────────────────────────────
        with st.spinner("🤖 Running AI analysis… (this may take 15–30 seconds)"):
            try:
                prompt = build_prompt(text, kpis.to_prompt_dict())
                analysis_result = analyze_financials(prompt)
            except Exception as e:
                st.error(f"❌ AI analysis failed: {e}")
                st.stop()

        # ── Header ────────────────────────────────────────────────────────────
        st.markdown("---")
        if not is_confident:
            st.warning("⚠️ Could not confidently detect the company name — "
                       "showing best guess below.")
        col_h1, col_h2 = st.columns(2)
        col_h1.markdown(f"### 🏦 {company_name}")
        col_h2.markdown(f"### 🏭 Sector: {sector}")
        st.markdown("---")

        # ── Main layout ───────────────────────────────────────────────────────
        col_left, col_right = st.columns([2, 1])

        with col_left:
            st.markdown("## 📌 AI Analysis Report")
            st.markdown("---")
            if analysis_result:
                st.markdown(analysis_result)
            else:
                st.warning("⚠️ The AI returned an empty response. Try re-running.")

        with col_right:
            render_kpi_panel(kpis)

        st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    "<div style='text-align:center; color: grey; font-size: 0.85em;'>"
    "Built with Streamlit · Powered by LLM · For educational use only."
    "</div>",
    unsafe_allow_html=True,
)