"""SDI monitoring + report types (M11): seen-set diff, state, type framing."""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from patentkit.search import SearchQuery, rank_rows
from patentkit.search.rank import Candidate
from patentkit.search.report import render_search_report
from patentkit.search.sdi import (
    load_state,
    render_sdi_report,
    save_state,
    split_new,
    update_state,
)

ROOT = os.path.join(os.path.dirname(__file__), "..")
BRIEF = os.path.join(ROOT, "samples", "search_query_SAMPLE.json")
EXPORT = os.path.join(ROOT, "samples", "search_export_SAMPLE.json")


def _c(num, score=5) -> Candidate:
    return Candidate(publication_number=num, score=score, title=f"T {num}")


# ---- pure set logic ---------------------------------------------------------

def test_split_new_partitions_by_seen():
    seen = {"US-1-A1": "2026-01-01"}
    new, old = split_new([_c("US-1-A1"), _c("US-2-A1")], seen)
    assert [c.publication_number for c in new] == ["US-2-A1"]
    assert [c.publication_number for c in old] == ["US-1-A1"]


def test_update_state_absorbs_hits_and_keeps_first_seen():
    state = {"seen": {"US-1-A1": "2026-01-01"}, "runs": []}
    state = update_state(state, [_c("US-1-A1"), _c("US-2-A1")], "2026-06-13",
                         new_count=1, total_rows=2)
    assert state["seen"]["US-1-A1"] == "2026-01-01"   # first-seen date preserved
    assert state["seen"]["US-2-A1"] == "2026-06-13"
    assert state["runs"][-1] == {"date": "2026-06-13", "hits": 2, "new": 1, "rows": 2}


def test_state_roundtrip(tmp_path):
    path = str(tmp_path / "theme.json")
    assert load_state(path) == {"seen": {}, "runs": []}   # missing file = first run
    save_state(path, {"seen": {"X": "d"}, "runs": [{"date": "d"}]})
    assert load_state(path)["seen"] == {"X": "d"}


# ---- SDI report -------------------------------------------------------------

def test_sdi_report_lists_only_new_with_evidence_columns():
    q = SearchQuery(name="t", keywords=[["x"]])
    md = render_sdi_report(q, [_c("US-9-B2")], seen_total=10,
                           run_date="2026-06-13", total_hits=3)
    assert "新着 1 件" in md
    assert "US-9-B2" in md
    assert "決定論" in md and "意味" in md


def test_sdi_report_zero_new_is_explicit():
    q = SearchQuery(name="t", keywords=[["x"]])
    md = render_sdi_report(q, [], seen_total=10, run_date="2026-06-13", total_hits=3)
    assert "変更なし" in md            # silence is reported, not omitted


# ---- report types -----------------------------------------------------------

def test_report_type_framing():
    rows = json.load(open(EXPORT, encoding="utf-8"))
    fto = SearchQuery(name="t", keywords=[["wireless power"]], report_type="fto")
    md = render_search_report(fto, rank_rows(rows, fto), total_rows=len(rows))
    assert "FTO" in md and "--legal" in md
    pa = SearchQuery(name="t", keywords=[["wireless power"]], report_type="prior-art")
    md = render_search_report(pa, rank_rows(rows, pa), total_rows=len(rows))
    assert "先行技術調査" in md
    generic = SearchQuery(name="t", keywords=[["wireless power"]])
    assert "調査ログ" in render_search_report(generic, [], 0)


# ---- CLI end-to-end (subprocess, fixture export) ----------------------------

def test_sdi_monitor_first_run_then_no_change(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "sdi_monitor.py"), BRIEF,
           "--from-export", EXPORT, "--state-dir", str(tmp_path / "state"),
           "--out-dir", str(tmp_path / "out"), "--run-date", "2026-06-13"]
    r1 = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    assert r1.returncode == 0, r1.stderr
    assert "初回ベースライン" in r1.stdout
    r2 = subprocess.run(cmd[:-1] + ["2026-06-20"], capture_output=True, text=True,
                        encoding="utf-8", env=env)
    assert r2.returncode == 0, r2.stderr
    assert "新着 0 件" in r2.stdout
    report = (tmp_path / "out" / "sdi_wireless-power-fod.md").read_text(encoding="utf-8")
    assert "変更なし" in report
    state = json.loads((tmp_path / "state" / "wireless-power-fod.json").read_text(encoding="utf-8"))
    assert len(state["runs"]) == 2
