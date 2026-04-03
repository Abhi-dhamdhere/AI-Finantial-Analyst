"""
kpi_extractor.py — v4
~~~~~~~~~~~~~~~~~~~~~
Fully compatible with pdf_parser.py v2 output which contains:

  - Plain text sections (newlines preserved, horizontal space collapsed)
  - [TABLE — Page N] ... [/TABLE] blocks with pipe-delimited rows

Extraction strategy
-------------------
Priority 1 → TABLE blocks
    pdfplumber already did the hard work of isolating cells.
    We scan rows where the first cell matches a KPI keyword,
    then read subsequent cells as ordered column values.
    Unit is detected from the table header row or the surrounding text.

Priority 2 → Plain text rows
    For pages where table extraction didn't fire (e.g. notes pages,
    prose paragraphs mentioning figures), we fall back to line scanning.

Priority 3 → Inline / sentence scan
    Catches "Net profit of Rs 18,641 crore" style mentions in notes.

Noise guards
------------
- Row-index integers (1, 2, 3 … 50) at line start are skipped
- EPS requires a decimal point (rejects whole-number row indices)
- Revenue/EBITDA require normalised value >= 1 Cr
- Document unit is detected once from header and propagated everywhere
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KPIValue:
    raw: float
    unit: str                        # "cr", "lakh", "mn", "bn", "rs", ""
    normalised_cr: Optional[float]   # converted to Rs Crore; None for EPS
    context: str                     # <=120 char snippet for debugging

    def __eq__(self, other):
        return (isinstance(other, KPIValue)
                and round(self.raw, 3) == round(other.raw, 3)
                and self.unit == other.unit)

    def __hash__(self):
        return hash((round(self.raw, 3), self.unit))

    def display(self) -> str:
        if self.unit == "rs":               # EPS — per share amount
            return f"Rs {self.raw:,.2f}"
        if self.normalised_cr is not None:
            return f"Rs {self.normalised_cr:,.2f} Cr"
        return f"{self.raw:,.2f} (unit unknown)"


@dataclass
class ExtractedKPIs:
    revenue:    list[KPIValue] = field(default_factory=list)
    net_profit: list[KPIValue] = field(default_factory=list)
    ebitda:     list[KPIValue] = field(default_factory=list)
    eps:        list[KPIValue] = field(default_factory=list)

    def to_prompt_dict(self) -> dict:
        """Return a clean dict for build_prompt()."""
        def fmt(values: list[KPIValue]) -> Optional[str]:
            if not values:
                return None
            primary = values[0].display()
            alts = [v.display() for v in values[1:3]]
            return primary + (f"  (also: {', '.join(alts)})" if alts else "")

        return {
            "Revenue":    fmt(self.revenue),
            "Net Profit": fmt(self.net_profit),
            "EBITDA":     fmt(self.ebitda),
            "EPS":        fmt(self.eps),
        }

    def summary(self) -> str:
        """Human-readable debug output."""
        d = self.to_prompt_dict()
        lines = ["── Extracted KPIs ──"]
        for k, v in d.items():
            lines.append(f"  {k:15s}: {v or 'Not found'}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Unit detection & conversion
# ─────────────────────────────────────────────────────────────────────────────

_UNIT_TO_CR: dict[str, float] = {
    "cr": 1.0,  "crore": 1.0,   "crores": 1.0,
    "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01,
    "mn": 0.1,  "million": 0.1, "millions": 0.1,
    "bn": 100.0, "billion": 100.0, "billions": 100.0,
}

# Matches "(Rs in crore)", "(in crore)", "Rs Crore", "Rupees in Lakhs" etc.
_UNIT_HEADER_RE = re.compile(
    r"[\(\[]?\s*(?:Rs\.?|Rupees?)?\s*in\s+(crores?|lakhs?|lacs?|millions?|billions?)\s*[\)\]]?"
    r"|(?:Rs\.?)\s*(crores?|lakhs?|lacs?|millions?)",
    re.IGNORECASE,
)

_UNIT_CANONICAL: dict[str, str] = {
    "crore": "cr",  "crores": "cr",
    "lakh":  "lakh", "lakhs": "lakh", "lac": "lakh", "lacs": "lakh",
    "million": "mn", "millions": "mn",
    "billion": "bn", "billions": "bn",
}


def _detect_unit(text: str) -> str:
    """
    Scan text (or a table block) for a unit declaration.
    Returns canonical unit key. Defaults to 'cr' for Indian filings.
    """
    m = _UNIT_HEADER_RE.search(text[:4000])
    if m:
        raw = (m.group(1) or m.group(2) or "").lower()
        return _UNIT_CANONICAL.get(raw, "cr")
    return "cr"


def _to_crore(raw: float, unit: str) -> Optional[float]:
    factor = _UNIT_TO_CR.get(unit)
    return round(raw * factor, 2) if factor is not None else None


# ─────────────────────────────────────────────────────────────────────────────
# Document structure splitter
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Section:
    kind: str       # "table" or "text"
    content: str    # raw content of this section
    unit: str       # detected unit for this section


def _split_sections(text: str, doc_unit: str) -> list[_Section]:
    """
    Split the pdf_parser output into TABLE and TEXT sections.
    Each section gets its own unit (table headers may override doc_unit).
    """
    sections: list[_Section] = []
    table_re = re.compile(
        r"\[TABLE[^\]]*\](.*?)\[/TABLE\]", re.DOTALL | re.IGNORECASE
    )

    last_end = 0
    for m in table_re.finditer(text):
        # Text before this table
        preceding = text[last_end:m.start()]
        if preceding.strip():
            sections.append(_Section("text", preceding, doc_unit))

        table_content = m.group(1)
        # Unit might be declared inside the table itself (as a header cell)
        table_unit = _detect_unit(table_content) or doc_unit
        sections.append(_Section("table", table_content, table_unit))
        last_end = m.end()

    # Remaining text after last table
    tail = text[last_end:]
    if tail.strip():
        sections.append(_Section("text", tail, doc_unit))

    # If no tables found at all, treat entire doc as plain text
    if not sections:
        sections.append(_Section("text", text, doc_unit))

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# Number helpers
# ─────────────────────────────────────────────────────────────────────────────

# For plain-text rows: require 4+ digit numbers to avoid row indices / %
_NUM_RE_STRICT = re.compile(r"(?<!\d)([\d,]+\.\d+|\d{4,}(?:,\d+)*)(?!\d)")

# For table cells: any positive number (cells are already isolated)
_NUM_RE_CELL   = re.compile(r"^\s*-?\s*([\d,]+(?:\.\d+)?)\s*$")

# Decimal numbers for EPS
_DECIMAL_RE    = re.compile(r"\b(\d{1,4}\.\d{1,4})\b")


def _parse_num(s: str) -> Optional[float]:
    try:
        v = float(s.replace(",", ""))
        return v if v > 0 else None
    except ValueError:
        return None


def _is_row_index(val: float, line: str) -> bool:
    """True if val is a small integer at the very start of the line."""
    if val != int(val) or val > 50:
        return False
    m = re.match(r"^\s*(\d{1,2})\b", line)
    return bool(m and float(m.group(1)) == val)


# ─────────────────────────────────────────────────────────────────────────────
# KPI keyword config
# ─────────────────────────────────────────────────────────────────────────────

# (inline_patterns, table/row_keywords)
# Longer/more specific phrases first to avoid greedy short-keyword matches.
_KPI_CONFIG: dict[str, tuple[list[str], list[str]]] = {
    "Revenue": (
        [
            "total income",
            "income from operations",
            "revenue from operations",
            "net revenue",
            "net sales",
            "turnover",
        ],
        [
            "total income",
            "income from operations",
            "revenue from operations",
            "net sales",
            "turnover",
        ],
    ),
    "Net Profit": (
        [
            "net profit for the period",
            "net profit for the quarter",
            "net profit for the year",
            "net profit from ordinary activities after tax",
            "profit after tax",
            "profit for the period",
            "net profit after tax",
        ],
        [
            "net profit for the period",
            "net profit from ordinary activities after tax",
            "profit after tax",
            "profit for the period",
        ],
    ),
    "EBITDA": (
        [
            "operating profit before provisions",
            "ebitda",
            "pbdit",
            "operating profit",
        ],
        [
            "operating profit before provisions",
            "ebitda",
            "pbdit",
            "operating profit",
        ],
    ),
    "EPS": (
        [
            "basic eps before",
            "basic eps",
            "diluted eps before",
            "diluted eps",
            "earnings per share",
        ],
        [
            "basic eps",
            "diluted eps",
            "earnings per share",
        ],
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# TABLE section extractor
# ─────────────────────────────────────────────────────────────────────────────

def _extract_from_table_section(
    content: str,
    table_keywords: list[str],
    unit: str,
    is_eps: bool,
) -> list[KPIValue]:
    """
    Parse pipe-delimited table rows from a [TABLE] block.

    Row format (from pdf_parser):
        Particulars | 30.09.2025 | 30.06.2025 | 30.09.2024
        Total Income (1)+(2) | 91040.72 | 99200.03 | 85499.64

    Strategy:
    - First cell = label (keyword match target)
    - Remaining cells = column values (left = most recent period)
    - Skip header rows (no numbers in data cells)
    """
    results: list[KPIValue] = []
    seen: set = set()

    kw_patterns = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in table_keywords
    ]

    for line in content.splitlines():
        if "|" not in line:
            continue

        cells = [c.strip() for c in line.split("|")]
        if not cells:
            continue

        label = cells[0]
        # Check if this row's label matches any keyword
        if not any(pat.search(label) for pat in kw_patterns):
            continue

        # Data cells are everything after the label cell
        data_cells = cells[1:]

        for cell in data_cells:
            if is_eps:
                # EPS: look for decimal in cell
                dm = _DECIMAL_RE.search(cell)
                if dm:
                    val = _parse_num(dm.group(1))
                    if val and 0.5 <= val <= 10_000:
                        kv = KPIValue(raw=val, unit="rs", normalised_cr=None,
                                      context=line[:120])
                        if kv not in seen:
                            seen.add(kv)
                            results.append(kv)
            else:
                # Try to parse cell as a number
                cm = _NUM_RE_CELL.match(cell)
                if cm:
                    val = _parse_num(cm.group(1))
                    if val and val > 0:
                        norm = _to_crore(val, unit)
                        kv = KPIValue(raw=val, unit=unit, normalised_cr=norm,
                                      context=line[:120])
                        if kv not in seen:
                            seen.add(kv)
                            results.append(kv)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Plain-text section extractor
# ─────────────────────────────────────────────────────────────────────────────

def _extract_from_text_section(
    content: str,
    table_keywords: list[str],
    unit: str,
    is_eps: bool,
) -> list[KPIValue]:
    """
    Line-by-line scan of plain text sections.
    Uses strict 4-digit minimum for numbers to avoid row-index noise.
    """
    results: list[KPIValue] = []
    seen: set = set()

    kw_patterns = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in table_keywords
    ]

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not any(pat.search(stripped) for pat in kw_patterns):
            continue

        if is_eps:
            for m in _DECIMAL_RE.finditer(stripped):
                val = _parse_num(m.group(1))
                if val and 0.5 <= val <= 10_000:
                    kv = KPIValue(raw=val, unit="rs", normalised_cr=None,
                                  context=stripped[:120])
                    if kv not in seen:
                        seen.add(kv)
                        results.append(kv)
        else:
            for m in _NUM_RE_STRICT.finditer(stripped):
                val = _parse_num(m.group(1))
                if not val or val <= 0:
                    continue
                if _is_row_index(val, stripped):
                    continue
                norm = _to_crore(val, unit)
                kv = KPIValue(raw=val, unit=unit, normalised_cr=norm,
                              context=stripped[:120])
                if kv not in seen:
                    seen.add(kv)
                    results.append(kv)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Inline / prose extractor  (Pass 3 — catches note mentions)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_inline(
    text: str,
    inline_patterns: list[str],
    unit: str,
    is_eps: bool,
) -> list[KPIValue]:
    """
    Regex scan for "keyword ... number" in running prose.
    Max 100-char gap between keyword and number.
    """
    results: list[KPIValue] = []
    seen: set = set()

    # Work on horizontally-collapsed text (handles soft line wraps)
    flat = re.sub(r"[ \t]+", " ", text)

    kw_re = re.compile(
        r"(?:" + "|".join(re.escape(p) for p in inline_patterns) + r")"
        r"[^|\n]{0,100}"
        r"(?:Rs\.?\s*)?([\d,]+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    for m in kw_re.finditer(flat):
        val = _parse_num(m.group(1))
        if not val or val <= 0:
            continue
        ctx = flat[max(0, m.start()-20): m.start()+80]

        if is_eps:
            if val == int(val) or not (0.5 <= val <= 10_000):
                continue
            kv = KPIValue(raw=val, unit="rs", normalised_cr=None, context=ctx)
        else:
            norm = _to_crore(val, unit)
            kv = KPIValue(raw=val, unit=unit, normalised_cr=norm, context=ctx)

        if kv not in seen:
            seen.add(kv)
            results.append(kv)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Sanity filter
# ─────────────────────────────────────────────────────────────────────────────

def _sanity_filter(kpi_name: str, values: list[KPIValue]) -> list[KPIValue]:
    clean = []
    for kv in values:
        effective = kv.normalised_cr if kv.normalised_cr is not None else kv.raw

        if kpi_name == "EPS":
            # Must have a decimal point (whole integers = row numbers)
            if kv.raw != int(kv.raw) and 0.5 <= kv.raw <= 10_000:
                clean.append(kv)
        elif kpi_name in ("Revenue", "EBITDA"):
            if effective >= 1.0:
                clean.append(kv)
        else:  # Net Profit (can be small or even near-zero for some companies)
            if effective >= 0.01:
                clean.append(kv)

    return clean


# ─────────────────────────────────────────────────────────────────────────────
# Merge & deduplicate
# ─────────────────────────────────────────────────────────────────────────────

def _merge(*lists: list[KPIValue]) -> list[KPIValue]:
    """Merge multiple result lists, preserving order, deduplicating by value."""
    seen: set = set()
    merged: list[KPIValue] = []
    for lst in lists:
        for kv in lst:
            if kv not in seen:
                seen.add(kv)
                merged.append(kv)
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_kpis(text: str) -> ExtractedKPIs:
    """
    Main entry point.

    Parameters
    ----------
    text : Raw output from pdf_parser.extract_text_from_pdf().
           Must preserve newlines. May contain [TABLE]...[/TABLE] blocks.

    Returns
    -------
    ExtractedKPIs
        .revenue, .net_profit, .ebitda, .eps  — lists of KPIValue
        .to_prompt_dict()                      — dict for build_prompt()
        .summary()                             — human-readable debug string
    """
    # Step 1: detect document-level unit from the full text header
    doc_unit = _detect_unit(text)
    logger.info("Document unit: %s", doc_unit)

    # Step 2: split into TABLE and TEXT sections
    sections = _split_sections(text, doc_unit)
    logger.info(
        "Sections: %d table, %d text",
        sum(1 for s in sections if s.kind == "table"),
        sum(1 for s in sections if s.kind == "text"),
    )

    result = ExtractedKPIs()

    for kpi_name, (inline_pats, table_kws) in _KPI_CONFIG.items():
        is_eps = (kpi_name == "EPS")

        table_hits: list[KPIValue] = []
        text_hits:  list[KPIValue] = []
        inline_hits: list[KPIValue] = []

        for section in sections:
            if section.kind == "table":
                table_hits += _extract_from_table_section(
                    section.content, table_kws, section.unit, is_eps
                )
            else:
                text_hits += _extract_from_text_section(
                    section.content, table_kws, section.unit, is_eps
                )
                inline_hits += _extract_inline(
                    section.content, inline_pats, section.unit, is_eps
                )

        # TABLE results take priority — they're the most reliable
        merged = _merge(table_hits, text_hits, inline_hits)
        filtered = _sanity_filter(kpi_name, merged)

        if kpi_name == "Revenue":
            result.revenue = filtered
        elif kpi_name == "Net Profit":
            result.net_profit = filtered
        elif kpi_name == "EBITDA":
            result.ebitda = filtered
        elif kpi_name == "EPS":
            result.eps = filtered

    return result


def extract_kpis_simple(text: str) -> dict:
    """Backward-compatible wrapper returning a prompt-ready dict."""
    return extract_kpis(text).to_prompt_dict()


# ─────────────────────────────────────────────────────────────────────────────
# Self-test  (mirrors actual pdf_parser.py v2 output for HDFC Q2)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _sample = """
UNAUDITED STANDALONE FINANCIAL RESULTS FOR THE QUARTER AND HALF YEAR ENDED SEPTEMBER 30, 2025

[TABLE — Page 1]
Particulars |  | Quarter ended |  |  | Half year ended | Year ended
 | 30.09.2025 | 30.06.2025 | 30.09.2024 | 30.09.2025 | 30.09.2024 | 31.03.2025
 | Unaudited | Unaudited | Unaudited | Unaudited | Unaudited | Audited
 |  | (in crore) |  |  |  |
1 Interest earned (a)+(b)+(c)+(d) | 76690.70 | 77470.20 | 74016.91 | 154160.90 | 147050.05 | 300517.04
2 Other Income (Refer note 13) | 14350.02 | 21729.83 | 11482.73 | 36079.85 | 22150.84 | 45632.28
3 Total Income (1)+(2) | 91040.72 | 99200.03 | 85499.64 | 190240.75 | 169200.89 | 346149.32
4 Interest expended | 45139.20 | 46032.23 | 43903.01 | 91171.43 | 87099.01 | 177846.95
5 Operating expenses (i)+(ii) | 17977.92 | 17433.84 | 16890.89 | 35411.76 | 33511.50 | 68174.89
6 Total Expenditure (4)+(5) | 63117.12 | 63466.07 | 60793.90 | 126583.19 | 120610.51 | 246021.84
7 Operating Profit before provisions and contingencies (3)-(6) | 27923.60 | 35733.96 | 24705.74 | 63657.56 | 48590.38 | 100127.48
8 Provisions (other than tax) | 3500.53 | 14441.63 | 2700.46 | 17942.16 | 5302.52 | 11649.42
12 Net Profit from ordinary activities after tax (10)-(11) | 18641.28 | 18155.21 | 16820.97 | 36796.49 | 32995.72 | 67347.36
14 Net Profit for the period (12)-(13) | 18641.28 | 18155.21 | 16820.97 | 36796.49 | 32995.72 | 67347.36
Basic EPS before & after extraordinary items - not annualized | 12.14 | 11.86 | 11.04 | 24.00 | 21.68 | 44.15
Diluted EPS before & after extraordinary items - not annualized | 12.09 | 11.79 | 10.99 | 23.88 | 21.59 | 43.95
[/TABLE]
"""

    kpis = extract_kpis(_sample)
    print(kpis.summary())
    print()
    print("EXPECTED:")
    print("  Revenue    : Rs 91,040.72 Cr")
    print("  Net Profit : Rs 18,641.28 Cr")
    print("  EBITDA     : Rs 27,923.60 Cr")
    print("  EPS        : Rs 12.14")
    print()
    print("── Prompt dict ──")
    for k, v in kpis.to_prompt_dict().items():
        print(f"  {k}: {v}")