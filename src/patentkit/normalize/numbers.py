"""Canonical patent number normalization.

WHY THIS EXISTS (design note)
-----------------------------
The patent number is the join key for every downstream step: retrieval,
de-duplication, family grouping, diffing, and continuous monitoring. If
normalization is wrong, everything downstream silently misaligns — you fetch
the wrong document, or you compare two unrelated patents and never notice.

Per the project's core principle ("根拠が弱い場合、推測で断定しない" /
P-NO-GUESS), this module NEVER guesses silently. When an input is ambiguous,
or requires Japanese era-year conversion, or is a grant serial that does not
encode a year, it lowers `confidence` and records a human-readable note in
`notes` instead of pretending certainty. Callers can gate on `needs_review`.

HONEST SCOPE
------------
- US / EP / WO modern forms .......... high confidence
- JP modern (Western-year) Kokai/grant medium-high confidence
- JP era-year (昭和/平成/令和) forms ... converted best-effort, flagged
- JP grant serial (特許第NNNNNNN号) ... mapped as grant, flagged (no year encoded)

The exact office-specific string a retrieval API expects (e.g. EPO OPS
epodoc/docdb format) is intentionally NOT fully resolved here. This module
produces a normalized *structured* identity; the precise wire format is the
connector's job, so the authoritative API can resolve edge cases instead of us.

This module is pure stdlib on purpose: it must run in a bare Colab cell with
zero installs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Office(str, Enum):
    US = "US"
    EP = "EP"
    JP = "JP"
    WO = "WO"
    UNKNOWN = "UNKNOWN"


class DocType(str, Enum):
    PUBLICATION = "publication"   # 公開公報 / application publication (kind A)
    GRANT = "grant"               # 登録 / granted patent (kind B)
    APPLICATION = "application"   # 出願番号
    UNKNOWN = "unknown"


@dataclass
class CanonicalNumber:
    raw: str
    office: Office
    number: str                       # normalized numeric core (no separators)
    kind: str | None                  # kind code if known (A1, B2, A, B, ...)
    doc_type: DocType
    canonical: str                    # e.g. "US-10123456-B2", "JP-2003123456-A"
    confidence: float                 # 0.0 .. 1.0
    notes: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        """True when a human should sanity-check this normalization."""
        return self.confidence < 0.85 or self.office is Office.UNKNOWN


# Japanese era -> Western base year. Western year = base + era_year.
# (Heisei 1 = 1989 -> base 1988; Reiwa 1 = 2019 -> base 2018; Showa 1 = 1926 -> base 1925.)
# Patent Kokai numbers abbreviate the era to a single kanji (特開平10 -> 平 = Heisei),
# so both the full name and the single-kanji form are mapped.
_JP_ERA_BASE = {
    "明治": 1867, "大正": 1911, "昭和": 1925, "平成": 1988, "令和": 2018,
    "明": 1867, "大": 1911, "昭": 1925, "平": 1988, "令": 2018,
}
_JP_ERA_LETTER = {"M": 1867, "T": 1911, "S": 1925, "H": 1988, "R": 2018}
# Longer era names must precede single-kanji forms in the alternation.
_JP_ERA_ALT = "明治|大正|昭和|平成|令和|明|大|昭|平|令"

# Japanese document-type prefixes (kanji).
_JP_DOCTYPE = {
    "特開": (DocType.PUBLICATION, "A"),   # Kokai (unexamined publication)
    "公開特許公報": (DocType.PUBLICATION, "A"),
    "特表": (DocType.PUBLICATION, "A"),   # PCT national-phase publication
    "特公": (DocType.GRANT, "B"),         # examined publication (pre-1996)
    "特許": (DocType.GRANT, "B"),         # granted patent
}

# Trailing kind code: a letter (optionally one digit) at the end. We allow
# whitespace before it (e.g. "10,123,456 B2") and then verify a digit precedes
# it, so we don't mistake a stray trailing letter for a kind code.
_KIND_RE = re.compile(r"\s*([A-Z]\d?)\s*$")
# Characters we treat as separators when collapsing a number to bare digits.
_SEPARATORS = str.maketrans({" ": "", "　": "", ",": "", ".": "", "/": "", "-": "", "‐": "", "−": "", "第": "", "号": ""})


def _bare_digits(s: str) -> str:
    return re.sub(r"\D", "", s.translate(_SEPARATORS))


def normalize(raw: str) -> CanonicalNumber:
    """Normalize a single messy patent-number string into a CanonicalNumber.

    Always returns a CanonicalNumber (never raises on bad input); unparseable
    input yields office=UNKNOWN with a note and low confidence.
    """
    original = raw
    s = (raw or "").strip()
    if not s:
        return CanonicalNumber(original, Office.UNKNOWN, "", None,
                               DocType.UNKNOWN, "", 0.0,
                               ["empty input"])

    # Japanese kanji forms are detected before the latin 2-letter prefix,
    # because "特許第..." has no country letters at all.
    if re.search(r"[぀-ヿ一-鿿]", s):
        return _norm_jp(original, s)

    up = s.upper()
    if up.startswith("US"):
        return _norm_us(original, up[2:])
    if up.startswith("EP"):
        return _norm_ep(original, up[2:])
    if up.startswith("WO") or up.startswith("PCT"):
        return _norm_wo(original, up)
    if up.startswith("JP"):
        return _norm_jp(original, up[2:])

    # No recognizable office prefix. Do not guess the jurisdiction.
    digits = _bare_digits(up)
    return CanonicalNumber(
        original, Office.UNKNOWN, digits, None, DocType.UNKNOWN,
        digits, 0.4,
        ["no office prefix; jurisdiction cannot be determined from the number alone"],
    )


def _split_kind(body: str) -> tuple[str, str | None]:
    m = _KIND_RE.search(body)
    if not m:
        return body, None
    head = body[: m.start()]
    if not re.search(r"\d\s*$", head):  # a kind code must immediately follow a number
        return body, None
    return head, m.group(1)


def _norm_us(original: str, body: str) -> CanonicalNumber:
    notes: list[str] = []
    body, kind = _split_kind(body)
    digits = _bare_digits(body)
    if not digits:
        return CanonicalNumber(original, Office.US, "", kind, DocType.UNKNOWN,
                               "US-", 0.3, ["US prefix but no digits"])

    if len(digits) == 11:
        # YYYY + 7-digit serial -> application publication
        doc, conf = DocType.PUBLICATION, 0.9
        if kind is None:
            kind = "A1"
            notes.append("kind code assumed A1 for 11-digit US publication")
    elif 6 <= len(digits) <= 8:
        doc, conf = DocType.GRANT, (0.95 if kind else 0.85)
    else:
        doc, conf = DocType.UNKNOWN, 0.5
        notes.append(f"unexpected US number length ({len(digits)} digits)")

    canonical = f"US-{digits}" + (f"-{kind}" if kind else "")
    return CanonicalNumber(original, Office.US, digits, kind, doc, canonical, conf, notes)


def _norm_ep(original: str, body: str) -> CanonicalNumber:
    notes: list[str] = []
    body, kind = _split_kind(body)
    digits = _bare_digits(body)
    if not digits:
        return CanonicalNumber(original, Office.EP, "", kind, DocType.UNKNOWN,
                               "EP-", 0.3, ["EP prefix but no digits"])

    if kind and kind.startswith("B"):
        doc = DocType.GRANT
    elif kind and kind.startswith("A"):
        doc = DocType.PUBLICATION
    else:
        doc = DocType.UNKNOWN
    conf = 0.92 if (len(digits) == 7 and kind) else 0.8
    if len(digits) != 7:
        notes.append(f"EP publication numbers are typically 7 digits (got {len(digits)})")
    canonical = f"EP-{digits}" + (f"-{kind}" if kind else "")
    return CanonicalNumber(original, Office.EP, digits, kind, doc, canonical, conf, notes)


def _norm_wo(original: str, up: str) -> CanonicalNumber:
    notes: list[str] = []
    is_app = up.startswith("PCT")
    body = up[3:] if is_app else up[2:]
    body, kind = _split_kind(body)
    digits = _bare_digits(body)
    if not digits:
        return CanonicalNumber(original, Office.WO, "", kind, DocType.UNKNOWN,
                               "WO-", 0.3, ["WO/PCT prefix but no digits"])
    doc = DocType.APPLICATION if is_app else DocType.PUBLICATION
    if not is_app and kind is None:
        kind = "A1"
        notes.append("kind code assumed A1 for WO publication")
    conf = 0.88
    canonical = f"WO-{digits}" + (f"-{kind}" if kind else "")
    return CanonicalNumber(original, Office.WO, digits, kind, doc, canonical, conf, notes)


def _norm_jp(original: str, s: str) -> CanonicalNumber:
    notes: list[str] = []
    doc_type: DocType | None = None
    kind: str | None = None

    # 1) Strip a leading kanji document-type prefix, if present.
    for prefix, (dt, k) in _JP_DOCTYPE.items():
        if s.startswith(prefix):
            doc_type, kind = dt, k
            s = s[len(prefix):]
            break

    s = s.strip().lstrip("：:").strip()

    # 2) Latin trailing kind (e.g. "JP2003-123456A") overrides default if present.
    body, latin_kind = _split_kind(s.upper())
    if latin_kind:
        kind = latin_kind
        if doc_type is None:
            doc_type = DocType.GRANT if latin_kind.startswith("B") else DocType.PUBLICATION
        s = body

    # 3a) Era form: (era)(eraYear)-(serial), e.g. 特開平10-123456 / 平成10-123456.
    m = re.search(rf"({_JP_ERA_ALT})\s*(\d{{1,2}})[\-‐−]?(\d{{3,}})", s)
    if not m:
        m = re.search(r"(?<![A-Z])([MTSHR])\s*(\d{1,2})[\-‐−](\d{3,})", s)
    if m:
        base = _JP_ERA_BASE.get(m.group(1)) or _JP_ERA_LETTER.get(m.group(1))
        year = base + int(m.group(2))
        serial = m.group(3)
        number = f"{year}{serial.zfill(6)}"
        doc_type = doc_type or DocType.PUBLICATION
        kind = kind or "A"
        notes.append(f"Japanese era '{m.group(1)}{m.group(2)}' converted to Western year {year}; "
                     "exact DOCDB string may differ — verify on retrieval")
        canonical = f"JP-{number}-{kind}"
        return CanonicalNumber(original, Office.JP, number, kind, doc_type, canonical, 0.7, notes)

    # 3b) Western form: (YYYY)-(serial), e.g. 特開2003-123456 / JP2003-123456.
    m = re.search(r"(19|20)(\d{2})[\-‐−]?(\d{3,})", s)
    if m:
        year = m.group(1) + m.group(2)
        serial = m.group(3)
        number = f"{year}{serial.zfill(6)}"
        doc_type = doc_type or DocType.PUBLICATION
        kind = kind or "A"
        canonical = f"JP-{number}-{kind}"
        return CanonicalNumber(original, Office.JP, number, kind, doc_type, canonical, 0.85, notes)

    # 3c) Bare serial (e.g. 特許第4123456号 -> grant serial). No year is encoded.
    digits = _bare_digits(s)
    if digits:
        doc_type = doc_type or DocType.GRANT
        kind = kind or ("B" if doc_type is DocType.GRANT else "A")
        notes.append("Japanese grant/registration serial: no year is encoded in the number; "
                     "mapping to a publication requires a lookup, not string logic")
        canonical = f"JP-{digits}-{kind}"
        return CanonicalNumber(original, Office.JP, digits, kind, doc_type, canonical, 0.7, notes)

    return CanonicalNumber(original, Office.JP, "", kind, doc_type or DocType.UNKNOWN,
                           "JP-", 0.3, notes + ["JP prefix but could not parse a number"])
