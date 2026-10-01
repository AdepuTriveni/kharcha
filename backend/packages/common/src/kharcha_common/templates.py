"""Sender keys and template signatures (PROJECT_SPEC §10.4, §15.3).

``template_signature`` must stay identical to ``kharcha_ml.dataset.labels.template_signature``
(a test in the agents package checks both on the sample set).
"""

import re

_VPA = re.compile(r"\b[\w.\-]+@[a-z][a-z0-9]+\b", re.I)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MASKED = re.compile(r"(?:\bXX|\*\*|\bx)\d{3,4}\b", re.I)
_CAPS_WORD = re.compile(r"\b[A-Z][A-Z0-9&.'-]{2,}\b")
_SPACES = re.compile(r"\s+")


def sender_key(sender: str | None, source_app: str | None = None) -> str | None:
    """``"AX-HDFCBK"`` -> ``"HDFCBK"`` (the DLT header prefix varies); apps use the package."""
    if sender and sender.strip():
        return sender.strip().split("-")[-1].upper()
    if source_app and source_app.strip():
        return source_app.strip()
    return None


def template_signature(text: str, sender: str | None = None) -> str:
    """Message with numbers, VPAs, account hints and capitalised names masked."""
    t = _VPA.sub("<vpa>", text)
    t = _MASKED.sub("<acct>", t)
    t = _NUMBER.sub("<n>", t)
    t = _CAPS_WORD.sub("<name>", t)
    t = _SPACES.sub(" ", t).strip().lower()
    prefix = (sender or "").split("-")[-1].upper()
    return f"{prefix}|{t}"
