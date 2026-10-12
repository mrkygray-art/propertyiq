"""AI suggestions for T-12 labels the synonym list doesn't recognize.

Only the label text is sent, never amounts, and after masking anything that looks like personal
data (emails, phone numbers, names after "tenant"/"resident"). The local model must answer with
categories from the fixed list; anything else is ignored. Every AI mapping is marked low
confidence and shown to the reviewer, who approves or changes it.
"""
from __future__ import annotations

import json
import re

from propertyiq.llm import LLMError, LLMProvider
from propertyiq.screening.intake.schema import CATEGORY_MEANING, T12_CATEGORIES

ALLOWED = [k for k, s, _ in T12_CATEGORIES if s in ("income", "expense", "below_line")]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\b\d{3}-\d{4}\b")
_PERSON = re.compile(r"\b(tenant|resident|lessee|occupant|unit\s*\w+)\s*[:\-]\s*[A-Z][\w'\-]+(\s+[A-Z][\w'\-]+)*", re.I)


def mask(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return _PERSON.sub(lambda m: f"{m.group(1)}: [name]", text)


SYSTEM = (
    "You map line-item labels from a property operating statement (T-12) to standard categories. "
    "Answer with JSON only: an object whose keys are the label numbers and whose values are category keys "
    "copied exactly from the CATEGORIES list (for example \"contract_services\" or \"utilities_gas\"). "
    "The document's section heading is context only; it is never an answer. "
    "Use \"unmapped\" if unsure. Do not invent categories."
)


def suggest(labels: list[tuple[str, str]], provider: LLMProvider) -> dict[str, str]:
    """labels: (label, section heading). Returns {label: category} for labels the model mapped confidently."""
    if not labels:
        return {}
    cats = "\n".join(f"- {k}: {CATEGORY_MEANING[k]}" for k in ALLOWED)
    items = "\n".join(f'{i}. "{mask(label)}" (appears in the document under the heading "{mask(section) or "none"}")'
                      for i, (label, section) in enumerate(labels, start=1))
    example = ", ".join(f'"{i}": "<category key>"' for i in range(1, len(labels) + 1))
    prompt = (f"CATEGORIES (key: meaning):\n{cats}\n\nLABELS:\n{items}\n\n"
              f"Reply with JSON shaped like {{{example}}}, using keys from CATEGORIES only.\nJSON:")
    try:
        text = provider.chat(SYSTEM, prompt, temperature=0.0, max_tokens=300).text
    except LLMError:
        return {}
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for key, cat in raw.items():
        try:
            idx = int(str(key).strip().rstrip(".")) - 1
        except ValueError:
            continue
        if 0 <= idx < len(labels) and isinstance(cat, str) and cat in ALLOWED:
            out[labels[idx][0]] = cat
    return out
