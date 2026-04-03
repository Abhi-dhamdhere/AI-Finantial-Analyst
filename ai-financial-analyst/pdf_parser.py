"""
pdf_parser.py
~~~~~~~~~~~~~
Extracts clean, structured text from Indian financial report PDFs.

Key fixes over v1:
- Does NOT collapse newlines into spaces — table row structure is preserved
- Extracts tables explicitly via pdfplumber (when present) and appends them
  as pipe-delimited rows so kpi_extractor can scan them reliably
- Cleans only horizontal noise (multiple spaces → one space per line)
- Strips boilerplate junk lines (addresses, CIN, website, phone numbers)
  so the LLM doesn't waste context on them
- Falls back gracefully on pages with no extractable text
"""

from __future__ import annotations

import re
import logging
import pdfplumber

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Boilerplate filters  (lines we never want the LLM or extractor to see)
# ─────────────────────────────────────────────────────────────────────────────

_JUNK_PATTERNS: list[re.Pattern] = [
    re.compile(r"CIN\s*:\s*[A-Z0-9]+", re.IGNORECASE),
    re.compile(r"website\s*:\s*https?://", re.IGNORECASE),
    re.compile(r"tel\.?\s*:\s*[\d\s\-]+", re.IGNORECASE),
    re.compile(r"fax\.?\s*:\s*[\d\s\-]+", re.IGNORECASE),
    re.compile(r"regd\.?\s*office", re.IGNORECASE),
    re.compile(r"chartered\s+accountants?", re.IGNORECASE),
    re.compile(r"udin\s*:\s*\d+", re.IGNORECASE),
    re.compile(r"membership\s+no\.?\s*\d+", re.IGNORECASE),
    # Very short lines are usually page numbers or stray OCR artefacts
]

def _is_junk_line(line: str) -> bool:
    stripped = line.strip()
    if len(stripped) < 3:
        return True
    return any(pat.search(stripped) for pat in _JUNK_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# Table → text serialiser
# ─────────────────────────────────────────────────────────────────────────────

def _table_to_text(table: list[list[str | None]]) -> str:
    """
    Convert a pdfplumber table (list of rows, each row a list of cell strings)
    into pipe-delimited text rows.

    Example output:
        Particulars | 30.09.2025 | 30.06.2025 | 30.09.2024
        Total Income (1)+(2) | 91040.72 | 99200.03 | 85499.64
        Net Profit for the period | 18641.28 | 18155.21 | 16820.97
    """
    lines: list[str] = []
    for row in table:
        # Replace None cells with empty string, strip whitespace
        cells = [str(c).strip() if c is not None else "" for c in row]
        # Skip rows that are entirely empty
        if not any(cells):
            continue
        lines.append(" | ".join(cells))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Per-page text cleaner
# ─────────────────────────────────────────────────────────────────────────────

def _clean_page_text(raw: str) -> str:
    """
    Clean a single page's raw text:
    - Preserve newlines (critical for table row detection)
    - Collapse multiple spaces/tabs on the SAME line → single space
    - Remove junk lines
    - Remove 3+ consecutive blank lines → max 2
    """
    cleaned_lines: list[str] = []
    blank_run = 0

    for line in raw.splitlines():
        # Collapse horizontal whitespace only (NOT newlines)
        line = re.sub(r"[ \t]+", " ", line).strip()

        if _is_junk_line(line):
            continue

        if line == "":
            blank_run += 1
            if blank_run <= 2:
                cleaned_lines.append("")
        else:
            blank_run = 0
            cleaned_lines.append(line)

    return "\n".join(cleaned_lines)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(
    file_path: str,
    max_pages: int = 20,
    include_tables: bool = True,
) -> str:
    """
    Extract clean, structured text from a financial results PDF.

    Parameters
    ----------
    file_path    : Path to the PDF file.
    max_pages    : Stop after this many pages (avoids processing 100-page ARs).
                   Set to None to process all pages.
    include_tables : If True, pdfplumber table extraction is run alongside
                     text extraction and appended as pipe-delimited rows.
                     This significantly improves KPI extraction accuracy.

    Returns
    -------
    Clean text string with newlines preserved.
    """
    full_text_parts: list[str] = []
    pages_processed = 0

    try:
        with pdfplumber.open(file_path) as pdf:
            total = len(pdf.pages)
            limit = min(total, max_pages) if max_pages else total
            logger.info("PDF has %d pages; processing first %d", total, limit)

            for i, page in enumerate(pdf.pages[:limit]):
                pages_processed += 1

                # ── Plain text extraction ─────────────────────────────────
                raw_text = page.extract_text() or ""
                if raw_text.strip():
                    cleaned = _clean_page_text(raw_text)
                    if cleaned.strip():
                        full_text_parts.append(cleaned)

                # ── Table extraction (the important addition) ─────────────
                if include_tables:
                    try:
                        tables = page.extract_tables()
                        for table in tables:
                            if table:
                                table_text = _table_to_text(table)
                                if table_text.strip():
                                    # Wrap in a clear marker so the LLM
                                    # and extractor know this is tabular data
                                    full_text_parts.append(
                                        f"\n[TABLE — Page {i+1}]\n{table_text}\n[/TABLE]\n"
                                    )
                    except Exception as e:
                        logger.debug("Table extraction failed on page %d: %s", i+1, e)

    except Exception as e:
        logger.error("Failed to open PDF '%s': %s", file_path, e)
        raise RuntimeError(f"PDF parsing failed: {e}") from e

    if not full_text_parts:
        raise RuntimeError(
            "No text could be extracted from this PDF. "
            "It may be a scanned/image-only document. "
            "Try running OCR on it first (e.g. with ocrmypdf)."
        )

    result = "\n\n".join(full_text_parts)
    logger.info(
        "Extracted ~%d chars from %d pages", len(result), pages_processed
    )
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Quick self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python pdf_parser.py <path_to_pdf>")
        sys.exit(1)

    path = sys.argv[1]
    text = extract_text_from_pdf(path)

    print(f"── Extracted {len(text)} characters ──")
    print(text[:3000])
    print("\n... (truncated)")