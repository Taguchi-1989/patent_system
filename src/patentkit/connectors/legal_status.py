"""Legal-status enrichment (M9) — is this patent still alive?

In a real FTO screen the FIRST sieve is legal status: a lapsed/expired patent
usually drops out of scope. The base datasets we use keylessly (BigQuery
patents.publications, USPTO bulk XML) do not carry legal status, so this is a
separate enrichment pass over already-fetched records.

Provider split, same as PatentSource:
  - FixtureLegalStatusProvider : local JSON, zero keys — dev/test/demo.
  - OPSLegalStatusProvider     : EPO OPS INPADOC legal data. Free registration
    (Consumer key/secret at developers.epo.org), stdlib urllib only. The XML
    parser is a pure function so it is testable without network.

P-NO-GUESS applies hard here: INPADOC gives legal EVENTS, not a simple
alive/dead flag. We only claim LAPSED/EXPIRED/REVOKED when an explicit event
says so (and we cite that event verbatim). We never infer ACTIVE from the
absence of death events — absence of evidence stays UNKNOWN + needs_review.
"""

from __future__ import annotations

import base64
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .base import PatentRecord

# Recognized terminal statuses. Anything we cannot prove stays UNKNOWN.
ACTIVE = "ACTIVE"
LAPSED = "LAPSED"
EXPIRED = "EXPIRED"
REVOKED = "REVOKED"
UNKNOWN = "UNKNOWN"

_OPS_BASE = "https://ops.epo.org/3.2"
_OPS_LEGAL_URL = _OPS_BASE + "/rest-services/legal/publication/docdb/{doc}"
_OPS_TOKEN_URL = _OPS_BASE + "/auth/accesstoken"

# Event-description patterns that constitute explicit evidence of death.
_DEATH_PATTERNS: list[tuple[str, str]] = [
    (EXPIRED, r"\bEXPIR"),                 # EXPIRED / EXPIRY
    (LAPSED, r"\bLAPSE"),                  # LAPSE / LAPSED
    (LAPSED, r"NON[- ]?PAYMENT"),          # fee not paid
    (REVOKED, r"\bREVOK"),                 # REVOKED / REVOCATION
    (LAPSED, r"\bWITHDRAW"),               # application withdrawn
    (LAPSED, r"DEEMED TO BE WITHDRAWN"),
]
# Patterns that signal the patent came back to life after a death event.
_REVIVAL_RE = re.compile(r"REINSTAT|RESTOR|RE-ESTABLISH", re.IGNORECASE)


@dataclass
class LegalEvent:
    date: str | None
    code: str
    description: str


@dataclass
class LegalStatusInfo:
    """One enrichment result, with the evidence that justifies the status."""

    canonical: str
    status: str = UNKNOWN
    events: list[LegalEvent] = field(default_factory=list)
    evidence: str | None = None            # verbatim event text behind `status`
    source: str = "unknown"
    source_url: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.status == UNKNOWN


def classify_events(events: list[LegalEvent]) -> tuple[str, str | None]:
    """(status, verbatim evidence) from explicit events only — never guessed.

    Later events win: a death event followed by a reinstatement returns to
    UNKNOWN (we know it was revived, not that it is currently in force).
    """
    status, evidence = UNKNOWN, None
    for ev in events:                      # events assumed oldest -> newest
        text = f"{ev.code} {ev.description}".upper()
        if _REVIVAL_RE.search(text):
            status, evidence = UNKNOWN, None
            continue
        for st, pat in _DEATH_PATTERNS:
            if re.search(pat, text):
                status = st
                evidence = (f"{ev.date or '?'} {ev.code}: {ev.description}").strip()
                break
    return status, evidence


# ---------------------------------------------------------------------------
# Fixture provider (zero keys)
# ---------------------------------------------------------------------------

class FixtureLegalStatusProvider:
    """Reads {canonical: {status, events: [{date, code, description}]}} JSON."""

    name = "legal-fixture"

    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8") as f:
            self._data: dict[str, dict] = json.load(f)

    def lookup(self, canonical: str) -> LegalStatusInfo | None:
        entry = self._data.get(canonical)
        if entry is None:
            return None
        events = [LegalEvent(e.get("date"), e.get("code", ""), e.get("description", ""))
                  for e in entry.get("events", [])]
        status = entry.get("status")
        if status is None:                 # derive from events, never guess
            status, evidence = classify_events(events)
        else:
            _, evidence = classify_events(events)
        return LegalStatusInfo(
            canonical=canonical, status=status, events=events, evidence=evidence,
            source=f"fixture: {os.path.basename(self.path)}",
        )


# ---------------------------------------------------------------------------
# EPO OPS INPADOC provider (free key; stdlib only, lazy network)
# ---------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_ops_legal_xml(xml_text: str, canonical: str) -> LegalStatusInfo:
    """Pure parser for the OPS legal-data XML (testable without network).

    Walks any element whose local name is ``legal`` and collects its event
    code/date/description from attributes or child elements (OPS has shifted
    this layout between versions, so we read both).
    """
    info = LegalStatusInfo(
        canonical=canonical,
        source="EPO OPS (INPADOC legal data)",
        source_url=_OPS_LEGAL_URL.format(doc=canonical.replace("-", ".")),
    )
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        info.notes.append(f"OPS legal XML could not be parsed: {e}")
        return info

    for el in root.iter():
        if _strip_ns(el.tag) != "legal":
            continue
        code = el.attrib.get("code", "")
        date = el.attrib.get("date")
        desc_parts: list[str] = []
        for child in el.iter():
            name = _strip_ns(child.tag)
            if name in ("L500EP", "pre", "legal-description", "text") and (child.text or "").strip():
                desc_parts.append(child.text.strip())
            elif name == "L007EP" and (child.text or "").strip():  # event date variant
                date = date or child.text.strip()
        info.events.append(LegalEvent(date, code, " / ".join(desc_parts)))

    info.status, info.evidence = classify_events(info.events)
    if not info.events:
        info.notes.append("no legal events returned by OPS for this document")
    if info.status == UNKNOWN:
        info.notes.append(
            "no explicit lapse/expiry/revocation event — IN-FORCE IS NOT CONFIRMED "
            "(INPADOC has no positive 'alive' flag); verify in the national register"
        )
    return info


class OPSLegalStatusProvider:
    """Live OPS client. OAuth2 client-credentials with a free consumer key."""

    name = "ops-legal"

    def __init__(self, consumer_key: str, consumer_secret: str):
        self._key = consumer_key
        self._secret = consumer_secret
        self._token: str | None = None

    def _http(self):
        import urllib.request  # noqa: PLC0415 — stdlib, but keep import lazy/visible
        return urllib.request

    def _fetch_token(self) -> str:
        req_mod = self._http()
        creds = base64.b64encode(f"{self._key}:{self._secret}".encode()).decode()
        req = req_mod.Request(
            _OPS_TOKEN_URL,
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {creds}",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        with req_mod.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())["access_token"]

    def lookup(self, canonical: str) -> LegalStatusInfo | None:
        req_mod = self._http()
        if self._token is None:
            self._token = self._fetch_token()
        url = _OPS_LEGAL_URL.format(doc=canonical.replace("-", "."))
        req = req_mod.Request(url, headers={"Authorization": f"Bearer {self._token}"})
        try:
            with req_mod.urlopen(req, timeout=30) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — degrade to flagged-unknown, never fabricate
            info = LegalStatusInfo(canonical=canonical, source=self.name, source_url=url)
            info.notes.append(f"OPS legal lookup failed ({e}); status stays UNKNOWN")
            return info
        return parse_ops_legal_xml(xml_text, canonical)


def make_ops_provider_from_env() -> OPSLegalStatusProvider | None:
    """Build the OPS provider from OPS_CONSUMER_KEY/_SECRET, or None if unset."""
    key = os.environ.get("OPS_CONSUMER_KEY")
    secret = os.environ.get("OPS_CONSUMER_SECRET")
    if not key or not secret:
        return None
    return OPSLegalStatusProvider(key, secret)


# ---------------------------------------------------------------------------
# Application to fetched records
# ---------------------------------------------------------------------------

def apply_legal_status(records: list[PatentRecord], provider) -> list[LegalStatusInfo]:
    """Enrich records in place; every status carries its evidence as a note."""
    infos: list[LegalStatusInfo] = []
    for rec in records:
        info = provider.lookup(rec.canonical)
        if info is None:
            rec.notes.append(f"legal status: no data in {getattr(provider, 'name', 'provider')}")
            continue
        infos.append(info)
        rec.legal_status = info.status
        if info.status in (LAPSED, EXPIRED, REVOKED):
            rec.notes.append(
                f"legal status {info.status} — 根拠: {info.evidence} "
                f"(出典: {info.source}) — FTO対象から除外候補（最終判断は登録原簿で確認）"
            )
        else:
            rec.notes.append(
                f"legal status UNKNOWN — 存続は未確認（{info.source}）; "
                "要確認: 各国の登録原簿（JPは J-PlatPat 経過情報）で存続を確認"
            )
        rec.notes.extend(info.notes)
    return infos
