"""Legal-status enrichment (M9): event classification, fixture/OPS providers.

P-NO-GUESS is the spine: ACTIVE is never inferred from the absence of death
events; every non-UNKNOWN status must cite the event that proves it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.connectors.base import PatentRecord
from patentkit.connectors.legal_status import (
    EXPIRED,
    LAPSED,
    REVOKED,
    UNKNOWN,
    FixtureLegalStatusProvider,
    LegalEvent,
    apply_legal_status,
    classify_events,
    make_ops_provider_from_env,
    parse_ops_legal_xml,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
SAMPLE = os.path.join(ROOT, "samples", "legal_status_SAMPLE.json")


def _rec(canonical="US-9500000-B2") -> PatentRecord:
    office, number = canonical.split("-")[0], canonical.split("-")[1]
    return PatentRecord(canonical=canonical, office=office, number=number)


# ---- classify_events (pure) ----------------------------------------------

def test_no_death_event_stays_unknown_not_active():
    status, evidence = classify_events(
        [LegalEvent("2020-01-01", "GRANT", "PATENT GRANTED")]
    )
    assert status == UNKNOWN          # absence of evidence is not ACTIVE
    assert evidence is None


def test_lapse_event_classified_with_verbatim_evidence():
    status, evidence = classify_events([
        LegalEvent("2020-01-01", "GRANT", "PATENT GRANTED"),
        LegalEvent("2024-07-01", "MM4A", "LAPSE DUE TO NON-PAYMENT OF MAINTENANCE FEE"),
    ])
    assert status == LAPSED
    assert "NON-PAYMENT" in evidence and "2024-07-01" in evidence


def test_expiry_and_revocation():
    assert classify_events([LegalEvent(None, "EXP", "PATENT EXPIRED AT END OF TERM")])[0] == EXPIRED
    assert classify_events([LegalEvent(None, "REV", "PATENT REVOKED IN OPPOSITION")])[0] == REVOKED


def test_reinstatement_after_lapse_returns_to_unknown():
    status, _ = classify_events([
        LegalEvent("2022-03-17", "PG25", "LAPSED IN A CONTRACTING STATE"),
        LegalEvent("2022-09-01", "RESTORE", "RIGHTS REINSTATED AFTER APPEAL"),
    ])
    assert status == UNKNOWN          # revived, but in-force is still unproven


# ---- fixture provider ------------------------------------------------------

def test_fixture_provider_lapsed_and_unknown():
    p = FixtureLegalStatusProvider(SAMPLE)
    lapsed = p.lookup("US-9500000-B2")
    assert lapsed.status == LAPSED
    assert lapsed.needs_review is False
    granted_only = p.lookup("US-10123456-B2")
    assert granted_only.status == UNKNOWN
    assert granted_only.needs_review
    assert p.lookup("XX-0-Z9") is None


def test_fixture_reinstated_ep_is_unknown():
    p = FixtureLegalStatusProvider(SAMPLE)
    assert p.lookup("EP-1234567-B1").status == UNKNOWN


# ---- apply to records ------------------------------------------------------

def test_apply_sets_status_and_cites_evidence():
    p = FixtureLegalStatusProvider(SAMPLE)
    dead_rec, unknown_rec = _rec("US-9500000-B2"), _rec("US-10123456-B2")
    infos = apply_legal_status([dead_rec, unknown_rec], p)
    assert len(infos) == 2
    assert dead_rec.legal_status == LAPSED
    assert any("根拠" in n and "NON-PAYMENT" in n for n in dead_rec.notes)
    assert unknown_rec.legal_status == UNKNOWN
    assert any("存続は未確認" in n for n in unknown_rec.notes)


def test_apply_handles_provider_miss():
    p = FixtureLegalStatusProvider(SAMPLE)
    rec = _rec("WO-2020123456-A1")
    infos = apply_legal_status([rec], p)
    assert infos == []
    assert rec.legal_status is None
    assert any("no data" in n for n in rec.notes)


# ---- OPS XML parser (pure, no network) ------------------------------------

_OPS_XML = """<?xml version="1.0"?>
<ops:world-patent-data xmlns:ops="http://ops.epo.org">
  <ops:patent-family>
    <ops:family-member>
      <ops:legal code="PGFP" date="2019-05-31">
        <ops:pre>ANNUAL FEE PAID TO NATIONAL OFFICE</ops:pre>
      </ops:legal>
      <ops:legal code="PG25" date="2023-01-04">
        <ops:pre>LAPSED IN A CONTRACTING STATE ANNOUNCED VIA POSTGRANT INFORMATION</ops:pre>
      </ops:legal>
    </ops:family-member>
  </ops:patent-family>
</ops:world-patent-data>
"""


def test_parse_ops_legal_xml_extracts_events_and_classifies():
    info = parse_ops_legal_xml(_OPS_XML, "EP-1234567-B1")
    assert len(info.events) == 2
    assert info.status == LAPSED
    assert "LAPSED IN A CONTRACTING STATE" in info.evidence
    assert info.source_url and "ops.epo.org" in info.source_url


def test_parse_ops_legal_xml_no_events_is_flagged_unknown():
    info = parse_ops_legal_xml("<root/>", "EP-1-B1")
    assert info.status == UNKNOWN
    assert any("no legal events" in n for n in info.notes)
    assert any("IN-FORCE IS NOT CONFIRMED" in n for n in info.notes)


def test_parse_ops_legal_xml_bad_xml_degrades():
    info = parse_ops_legal_xml("not xml <<", "EP-1-B1")
    assert info.status == UNKNOWN
    assert any("could not be parsed" in n for n in info.notes)


def test_ops_env_factory_none_when_unset(monkeypatch):
    monkeypatch.delenv("OPS_CONSUMER_KEY", raising=False)
    monkeypatch.delenv("OPS_CONSUMER_SECRET", raising=False)
    assert make_ops_provider_from_env() is None
