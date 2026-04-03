"""
analyzer.py — Ollama LLM interface for AI Financial Analyst
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Improvements over v1:
- Configurable via environment variables (OLLAMA_URL, OLLAMA_MODEL)
- Retry logic with exponential back-off (transient failures)
- Model availability pre-check with helpful error message
- Context window explicitly set (num_ctx) to avoid silent truncation
- Markdown-safe clean_output (preserves blank lines between sections)
- Structured OllamaError exception for callers that want to handle errors
- Optional streaming mode for Streamlit live output
"""

from __future__ import annotations

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Config  (override via environment variables)
# ─────────────────────────────────────────────────────────────────────────────

OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
MODEL_NAME   = os.getenv("OLLAMA_MODEL", "mistral")

_GENERATE_ENDPOINT = f"{OLLAMA_URL}/api/generate"
_TAGS_ENDPOINT     = f"{OLLAMA_URL}/api/tags"

# LLM generation params
_LLM_OPTIONS = {
    "temperature": 0.1,   # factual / deterministic
    "top_p":       0.9,
    "num_ctx":     8192,  # explicit context window — avoids silent truncation
}

# Retry settings
_MAX_RETRIES    = 3
_RETRY_DELAY_S  = 2.0   # seconds; doubles on each retry
_TIMEOUT_S      = 180   # generous timeout for large prompts


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class OllamaError(RuntimeError):
    """Raised when the Ollama backend returns an unrecoverable error."""


# ─────────────────────────────────────────────────────────────────────────────
# Model availability check
# ─────────────────────────────────────────────────────────────────────────────

def check_model_available(model: str = MODEL_NAME) -> tuple[bool, str]:
    """
    Verify that Ollama is reachable and `model` is pulled locally.

    Returns
    -------
    (True,  "")                   — all good
    (False, human-readable reason) — something is wrong
    """
    try:
        resp = requests.get(_TAGS_ENDPOINT, timeout=5)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return False, (
            f"Cannot connect to Ollama at {OLLAMA_URL}. "
            "Make sure Ollama is running (`ollama serve`)."
        )
    except requests.exceptions.Timeout:
        return False, f"Ollama at {OLLAMA_URL} did not respond within 5 s."
    except requests.exceptions.HTTPError as e:
        return False, f"Ollama returned HTTP {e.response.status_code} on /api/tags."

    available_models: list[str] = [
        m.get("name", "").split(":")[0]          # strip tag, e.g. "mistral:latest" → "mistral"
        for m in resp.json().get("models", [])
    ]

    if model.split(":")[0] not in available_models:
        pulled = ", ".join(available_models) if available_models else "none"
        return False, (
            f"Model '{model}' is not pulled. "
            f"Run `ollama pull {model}` to download it. "
            f"Currently available: {pulled}."
        )

    return True, ""


# ─────────────────────────────────────────────────────────────────────────────
# Output cleaner
# ─────────────────────────────────────────────────────────────────────────────

def clean_output(text: str) -> str:
    """
    Light cleanup that preserves markdown structure.

    - Strips leading/trailing whitespace per line
    - Collapses 3+ consecutive blank lines → 2  (keeps section spacing)
    - Does NOT remove all blank lines (that would destroy markdown headers/lists)
    """
    lines = [line.rstrip() for line in text.split("\n")]

    cleaned: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:          # allow max 2 consecutive blanks
                cleaned.append("")
        else:
            blank_run = 0
            cleaned.append(line)

    return "\n".join(cleaned).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Core HTTP call  (with retry)
# ─────────────────────────────────────────────────────────────────────────────

def _call_ollama(prompt: str, model: str, options: dict) -> str:
    """
    POST to Ollama /api/generate with retry + exponential back-off.
    Raises OllamaError on unrecoverable failure.
    """
    payload = {
        "model":   model,
        "prompt":  prompt,
        "stream":  False,
        "options": options,
    }

    delay = _RETRY_DELAY_S
    last_error: Exception | None = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(_GENERATE_ENDPOINT, json=payload, timeout=_TIMEOUT_S)

            if resp.status_code != 200:
                # Non-200 from Ollama usually means bad model name or OOM
                try:
                    detail = resp.json().get("error", resp.text[:200])
                except Exception:
                    detail = resp.text[:200]
                raise OllamaError(
                    f"Ollama returned HTTP {resp.status_code}: {detail}"
                )

            data = resp.json()
            if "response" not in data:
                raise OllamaError(
                    f"Unexpected Ollama response shape. Keys present: {list(data.keys())}"
                )

            return data["response"].strip()

        except OllamaError:
            raise   # don't retry logic errors

        except requests.exceptions.ConnectionError as e:
            last_error = e
            logger.warning("Attempt %d/%d — connection error: %s", attempt, _MAX_RETRIES, e)

        except requests.exceptions.Timeout as e:
            last_error = e
            logger.warning("Attempt %d/%d — timeout after %ds", attempt, _MAX_RETRIES, _TIMEOUT_S)

        except Exception as e:
            last_error = e
            logger.warning("Attempt %d/%d — unexpected error: %s", attempt, _MAX_RETRIES, e)

        if attempt < _MAX_RETRIES:
            logger.info("Retrying in %.1f s…", delay)
            time.sleep(delay)
            delay *= 2   # exponential back-off

    raise OllamaError(
        f"All {_MAX_RETRIES} attempts failed. Last error: {last_error}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def analyze_financials(
    prompt: str,
    model:   str  = MODEL_NAME,
    options: dict | None = None,
) -> str:
    """
    Send `prompt` to Ollama and return the cleaned response text.

    Parameters
    ----------
    prompt  : The full prompt string (from build_prompt).
    model   : Ollama model name. Defaults to OLLAMA_MODEL env var or 'mistral'.
    options : LLM sampling options. Merged over defaults if provided.

    Returns
    -------
    Cleaned response string, or a user-facing error message starting with '❌'.

    Notes
    -----
    Returns error strings (never raises) so Streamlit callers don't need
    try/except — but OllamaError IS raised internally for structured handling
    if you prefer that pattern.
    """
    merged_options = {**_LLM_OPTIONS, **(options or {})}

    try:
        raw = _call_ollama(prompt, model, merged_options)
        return clean_output(raw)

    except OllamaError as e:
        logger.error("OllamaError: %s", e)
        return f"❌ Ollama error: {e}"

    except Exception as e:
        logger.error("Unexpected error in analyze_financials: %s", e)
        return f"❌ Unexpected error: {e}"


def analyze_financials_stream(prompt: str, model: str = MODEL_NAME):
    """
    Streaming variant — yields text chunks as they arrive.
    Use with Streamlit's `st.write_stream()` for live output.

    Example
    -------
    with st.spinner("Analysing…"):
        output = st.write_stream(analyze_financials_stream(prompt))
    """
    payload = {
        "model":   model,
        "prompt":  prompt,
        "stream":  True,
        "options": _LLM_OPTIONS,
    }

    try:
        with requests.post(_GENERATE_ENDPOINT, json=payload,
                           stream=True, timeout=_TIMEOUT_S) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                import json
                chunk = json.loads(line)
                token = chunk.get("response", "")
                if token:
                    yield token
                if chunk.get("done"):
                    break

    except Exception as e:
        yield f"\n\n❌ Stream error: {e}"