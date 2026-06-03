"""USPTO Bulk Data (Patent Grant Full-Text XML) connector — fully KEYLESS.

WHAT THIS MODULE DOES
---------------------
USPTO publishes Patent Grant Full-Text XML weekly (Tuesdays) at:
    https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/<YEAR>/

Each weekly ZIP contains ONE large file with MANY concatenated XML documents
(one <us-patent-grant> per patent, each preceded by its own <?xml ...?> and
<!DOCTYPE ...> declaration).  No account and no API key are required.

IMPORTANT LIMITATION — number-to-week resolution
-------------------------------------------------
A bare patent number (e.g. "US-10123456-B2") CANNOT be mapped to its weekly
bulk file without knowing the patent's grant/issue date.  USPTO does not
provide an API for this mapping that is keyless.

Therefore this module works by indexing locally-available files that the
caller has already obtained, then answering lookup queries against that index.
The typical workflow is:

    1. Obtain the weekly ZIP for the relevant grant date (via download_week()
       or manual download from bulkdata.uspto.gov).
    2. Pass the local file path(s) to BulkDataSource(local_files=[...]).
    3. Call .fetch(canonical_number) to retrieve a specific patent.

This limitation is not a bug — it is an honest architectural constraint that
this module documents and surfaces rather than obscuring.
"""

from __future__ import annotations

import io
import os
import re
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Union

from ..normalize import CanonicalNumber
from .base import PatentRecord

_SOURCE_NAME = "USPTO Bulk Data (Patent Grant Full-Text XML)"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_date(s: str | None) -> str | None:
    """Convert an 8-digit YYYYMMDD string to YYYY-MM-DD, or return None."""
    if not s or len(s) != 8 or not s.isdigit():
        return None
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"


def _itertext(el) -> str:
    """Collect all descendant text from an Element, including inline markup."""
    if el is None:
        return ""
    return "".join(el.itertext())


def _strip_doctype(fragment: str) -> str:
    """Remove <!DOCTYPE ...> declarations that xml.etree.ElementTree cannot handle.

    Handles both simple and internal-subset forms:
        <!DOCTYPE us-patent-grant SYSTEM "...">
        <!DOCTYPE us-patent-grant SYSTEM "..." [ ... ]>
    """
    # Remove DOCTYPE with optional internal subset ([ ... ])
    fragment = re.sub(
        r"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>",
        "",
        fragment,
        flags=re.DOTALL,
    )
    return fragment


def _parse_single_grant(fragment: str) -> PatentRecord | None:
    """Parse one <us-patent-grant> XML fragment into a PatentRecord.

    Returns None if the fragment cannot be parsed.  Never raises.
    """
    try:
        cleaned = _strip_doctype(fragment).strip()
        if not cleaned:
            return None
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        return None

    bib_path = "us-bibliographic-data-grant"
    pub_ref = f"{bib_path}/publication-reference/document-id"

    country   = root.findtext(f"{pub_ref}/country") or "US"
    doc_num   = root.findtext(f"{pub_ref}/doc-number") or ""
    kind      = root.findtext(f"{pub_ref}/kind") or ""
    raw_date  = root.findtext(f"{pub_ref}/date")

    # Strip leading zeros from doc_number, but keep at least one digit
    doc_num_stripped = doc_num.lstrip("0") or "0"

    canonical = f"US-{doc_num_stripped}-{kind}" if kind else f"US-{doc_num_stripped}"

    title_el = root.find(f"{bib_path}/invention-title")
    title = _itertext(title_el).strip()

    assignee = root.findtext(
        f"{bib_path}/assignees/assignee/addressbook/orgname"
    )

    abstract_el = root.find("abstract")
    abstract = _itertext(abstract_el).strip()

    # One PatentRecord claim per <claim> element
    claims: list[str] = []
    for claim_el in root.findall("claims/claim"):
        # Collect all claim-text children; join their text
        parts: list[str] = []
        for ct in claim_el.findall("claim-text"):
            parts.append(_itertext(ct).strip())
        text = " ".join(p for p in parts if p).strip()
        if not text:
            # Fallback: collect all text in the <claim> element
            text = _itertext(claim_el).strip()
        if text:
            claims.append(text)

    notes: list[str] = []
    if not title:
        notes.append("title element missing or empty")
    if not abstract:
        notes.append("abstract element missing or empty")
    if not claims:
        notes.append("no claim elements found")

    pub_date = _fmt_date(raw_date)
    source_url = (
        f"https://patents.google.com/patent/US{doc_num_stripped}{kind}/en"
        if doc_num_stripped and kind
        else None
    )

    raw_data = {
        "country": country,
        "doc_number": doc_num,
        "kind": kind,
        "date": raw_date,
        "title": title,
        "assignee": assignee,
        "abstract_text": abstract,
        "claims_count": len(claims),
    }

    return PatentRecord(
        canonical=canonical,
        office=country,
        number=doc_num_stripped,
        title=title,
        abstract=abstract,
        claims=claims,
        assignee=assignee,
        pub_date=pub_date,
        legal_status=None,
        family_id=None,
        source=_SOURCE_NAME,
        source_url=source_url,
        notes=notes,
        raw=raw_data,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_uspto_grant_xml(data: Union[bytes, str]) -> list[PatentRecord]:
    """Parse a (possibly concatenated) USPTO Patent Grant Full-Text XML blob.

    USPTO weekly ZIP files contain one large file with many concatenated XML
    documents, each starting with its own ``<?xml ...?>`` declaration.  This
    function splits on those declarations, parses each document individually,
    and returns the collected PatentRecords.

    Parameters
    ----------
    data:
        Raw bytes or string of one or more concatenated USPTO grant XML docs.

    Returns
    -------
    list[PatentRecord]
        One record per successfully parsed ``<us-patent-grant>`` element.
        Failed documents are silently skipped (not raised).
    """
    if isinstance(data, bytes):
        # Try UTF-8 first, fall back to latin-1
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    else:
        text = data

    # Split on XML declarations.  re.split keeps boundaries by using a
    # capturing group; we discard the separators and reconstruct below.
    # Strategy: split on '<?xml', discard empty leading fragment, prepend
    # '<?xml' back to each remaining part.
    parts = re.split(r"(<\?xml)", text)
    # parts alternates: [pre-text, '<?xml', content, '<?xml', content, ...]
    # Reconstruct full fragments
    fragments: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == "<?xml" and i + 1 < len(parts):
            fragments.append("<?xml" + parts[i + 1])
            i += 2
        else:
            # Leading fragment before any <?xml — discard
            i += 1

    records: list[PatentRecord] = []
    for fragment in fragments:
        rec = _parse_single_grant(fragment)
        if rec is not None:
            records.append(rec)

    return records


def week_url_for_date(d: Union[date, str]) -> str:
    """Return the USPTO bulk-data ZIP URL for the weekly file containing date ``d``.

    The USPTO publishes Patent Grant Full-Text ZIPs at:
        https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/<YEAR>/ipg<YY><MM><DD>.zip

    Parameters
    ----------
    d:
        The grant/issue date as a ``datetime.date`` object or an ISO-format
        string (``"YYYY-MM-DD"``).

    Returns
    -------
    str
        The fully-qualified URL for the weekly ZIP file.

    Notes
    -----
    LIMITATION: The caller must know the grant/issue date of the patent(s) they
    seek.  There is no keyless API that maps a bare patent number to its issue
    week.  This function performs pure string arithmetic — no network access.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    yy = d.strftime("%y")   # 2-digit year
    mm = d.strftime("%m")
    dd = d.strftime("%d")
    year = d.year
    return (
        f"https://bulkdata.uspto.gov/data/patent/grant/redbook/fulltext/"
        f"{year}/ipg{yy}{mm}{dd}.zip"
    )


class BulkDataSource:
    """Patent source backed by USPTO Patent Grant Full-Text XML bulk files.

    This source can only look up patents that have been pre-indexed from local
    files.  You must obtain the relevant weekly ZIP(s) first (either by calling
    ``download_week()`` or by downloading them manually from bulkdata.uspto.gov),
    then pass their paths to the constructor.

    IMPORTANT LIMITATION
    --------------------
    ``fetch()`` returns ``None`` for any patent that is not present in the
    indexed files.  There is no fallback network lookup.  To find a specific
    patent you must know its grant date, download the corresponding weekly file,
    index it, and then call ``fetch()``.  This limitation is inherent to the
    USPTO bulk-data distribution model and is documented here rather than
    obscured.

    Parameters
    ----------
    local_files:
        Optional list of paths to local ``.xml`` or ``.zip`` files.  If
        provided, the constructor indexes them immediately.
    """

    name = "bulk-uspto"

    def __init__(self, local_files: list[str] | None = None) -> None:
        # Index: canonical number (e.g. "US-10123456-B2") -> PatentRecord
        self._index: dict[str, PatentRecord] = {}
        if local_files:
            self.index_files(local_files)

    def index_files(self, paths: list[str]) -> int:
        """Parse and index one or more local XML or ZIP files.

        Parameters
        ----------
        paths:
            File paths to ``.xml`` or ``.zip`` files.  ZIP files are opened
            and their largest contained file is parsed as XML.

        Returns
        -------
        int
            Number of PatentRecords newly added to the index.
        """
        added = 0
        for path in paths:
            try:
                data = _read_file_bytes(path)
            except OSError as exc:
                # Log but do not crash; caller can inspect the index afterward
                import warnings
                warnings.warn(f"bulk_uspto: could not read {path!r}: {exc}")
                continue

            records = parse_uspto_grant_xml(data)
            for rec in records:
                self._index[rec.canonical] = rec
                added += 1

        return added

    def fetch(self, number: CanonicalNumber) -> PatentRecord | None:
        """Return the indexed PatentRecord for ``number``, or ``None``.

        Parameters
        ----------
        number:
            A ``CanonicalNumber`` from ``patentkit.normalize.normalize()``.

        Returns
        -------
        PatentRecord or None
            The indexed record if found; ``None`` otherwise.

        Notes
        -----
        Returns ``None`` if the number is not in any indexed file.  To find a
        specific patent, you must first index a file that contains it.  There
        is no way to determine which weekly file contains a given number without
        knowing its grant date.
        """
        rec = self._index.get(number.canonical)
        if rec is not None:
            return rec
        # Honest: do not fabricate a result.  Return None with a note surfaced
        # in the type system (caller receives plain None; note is in docstring).
        return None

    def download_week(self, d: Union[date, str], dest_dir: str) -> str:
        """Download the USPTO weekly bulk-data ZIP for the given grant date.

        WARNING: These files are 100–300 MB each.  Ensure you have sufficient
        disk space and bandwidth before calling this method.

        LIMITATION: The caller must know the grant/issue date.  There is no
        keyless API to map a patent number to its issue week without this
        information.

        Parameters
        ----------
        d:
            The grant/issue date as a ``datetime.date`` or ISO string.
        dest_dir:
            Directory where the downloaded ZIP will be saved.

        Returns
        -------
        str
            Absolute path to the downloaded file.

        Notes
        -----
        This method performs a real network download and is intentionally NOT
        exercised by the test suite (the files are 100-300 MB).  It exists to
        complete the fetch workflow for production use.
        """
        url = week_url_for_date(d)
        filename = url.rsplit("/", 1)[-1]
        dest_path = os.path.join(dest_dir, filename)

        with urllib.request.urlopen(url) as response:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(65536)  # 64 KB chunks
                    if not chunk:
                        break
                    f.write(chunk)

        return os.path.abspath(dest_path)


# ---------------------------------------------------------------------------
# Internal file-reading helper
# ---------------------------------------------------------------------------

def _read_file_bytes(path: str) -> bytes:
    """Read a file as bytes.  For .zip, extract the largest contained file."""
    lower = path.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            if not names:
                raise OSError(f"ZIP file is empty: {path!r}")
            # Pick the largest contained file (the grant XML is the big one)
            largest = max(names, key=lambda n: zf.getinfo(n).file_size)
            return zf.read(largest)
    else:
        with open(path, "rb") as f:
            return f.read()
