"""Extract ``file:line`` citations from agent final answers.

Citations appear in several phrasings — ``src/auth/oauth.py:144``,
``lib/route.js, lines 455-457``, ``reply.js (line 623)``. All are normalised to
the colon form ``path:N`` (start line for a range) that Stage 0 of the judge
verifies against the real repo.

Matching is conservative: a recognised path must be followed by an explicit
separator (``:``, the word ``line(s)``, or ``(line``) so that prose like
``foo.js 30 times`` or ``note: this`` does not yield false positives.
"""

from __future__ import annotations

import re

_PATH = r"[\w./-]+\.\w+"
# Colon, ", lines " / "lines ", or "(line " / "(lines ".
_SEP = r"(?::|,?\s+lines?\s+|\s*\(\s*lines?\s+)"
# Start line, optionally followed by "-<end>" (ranges); we emit the start line.
_LINE = r"(\d+)(?:\s*-\s*\d+)?"
_CITATION_RE = re.compile(rf"({_PATH}){_SEP}{_LINE}")


def extract_citations(text: str) -> list[str]:
    """Return ordered, de-duplicated ``file:line`` citations found in ``text``.

    En/em dashes are normalised to ``-`` so ranges like ``623–629`` parse. For a
    range, the start line is emitted, matching Stage 0's single-line contract.
    """
    normalised = text.replace("\u2013", "-").replace("\u2014", "-")
    seen: set[str] = set()
    out: list[str] = []
    for match in _CITATION_RE.finditer(normalised):
        citation = f"{match.group(1)}:{match.group(2)}"
        if citation not in seen:
            seen.add(citation)
            out.append(citation)
    return out
