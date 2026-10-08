"""First-line PII detection.

Reads ONLY the header row and the first few data rows of a CSV (skipping the two Qualtrics
metadata rows when present), entirely locally. Identifier patterns are checked on the first data
row; the prose check looks at the first PROSE_ROWS rows, because one respondent's short answer
should not hide a free-text column. The resulting PiiReport contains column names and reason
codes, never cell values.

Free text is caught two ways: open-ended questions named in the QSF, and values that read like
prose (chat transcripts, open answers), so a CSV without a QSF is still covered. Columns the QSF
marks as closed-ended are exempt from the prose check (long choice labels are not free text).
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Default identifier columns in Qualtrics / panel exports (mirrors the ID_COLS list used in
# the author's earlier replication packages, plus a few common panel ids).
KNOWN_ID_COLS = [
    "StartDate", "EndDate", "Status", "IPAddress", "Progress", "Duration (in seconds)",
    "RecordedDate", "ResponseId", "RecipientLastName", "RecipientFirstName", "RecipientEmail",
    "ExternalReference", "LocationLatitude", "LocationLongitude", "DistributionChannel",
    "participantId", "projectId", "assignmentId", "workerId", "hitId", "aid", "PROLIFIC_PID", "SESSION_ID",
    "STUDY_ID", "mTurkCode", "rid", "psid", "caseid", "ip", "latitude", "longitude",
]
# Qualtrics system columns that are harmless to keep (not identifiers). Start and end times are
# kept for timing and attention checks.
SAFE_SYSTEM_COLS = {"Finished", "UserLanguage", "Progress", "Duration (in seconds)", "Status", "DistributionChannel",
                    "StartDate", "EndDate"}

HEADER_PATTERNS = {
    "email": re.compile(r"e-?mail", re.I),
    "phone": re.compile(r"\b(phone|mobile|cell|tel)\b", re.I),
    "ip": re.compile(r"\bip(address)?\b", re.I),
    "name": re.compile(r"(^|_)(first|last|full)?_?name($|_)", re.I),
    "geo": re.compile(r"(latitude|longitude|\bzip(code)?\b|postal|street|address)", re.I),
    "dob": re.compile(r"(\bdob\b|date_?of_?birth|birth_?date)", re.I),
    "ssn": re.compile(r"\bssn\b|social_?security", re.I),
    "id": re.compile(r"(^|_)(worker|participant|respondent|panel|user|prolific)_?id($|_)|^(prolific_?)?pid$|prolific|mturk|^turk_?id$|^worker$", re.I),
}
VALUE_PATTERNS = {
    "email": re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$"),
    "ip": re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    "phone": re.compile(r"^\+?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$"),
    "url": re.compile(r"^https?://", re.I),
    "ssn": re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    # recruitment-platform ids and IPv6, which name-based rules miss when a column is renamed
    "prolific_id": re.compile(r"^[0-9a-f]{24}$"),
    "mturk_id": re.compile(r"^A(?=[0-9A-Z]*\d)(?=[0-9A-Z]*[B-Z])[0-9A-Z]{11,20}$"),
    "ipv6": re.compile(r"^(?=.*:.*:)[0-9a-fA-F:]{7,39}$"),
    # CloudResearch Connect: participantId is 32 upper-case hex characters; assignmentId and projectId are UUIDs
    "cloudresearch_id": re.compile(r"^[0-9A-F]{32}$"),
    "uuid": re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "qualtrics_response_id": re.compile(r"^R_[A-Za-z0-9]{15,17}$"),
}
PROSE_ROWS = 5
_LETTER = re.compile(r"[A-Za-z]")


def looks_like_prose(val: str) -> bool:
    """A cell value that reads like writing rather than a code or a short label: a line break,
    eight or more words, or five or more words with sentence punctuation. Words are separated by
    whitespace, so a formula or model term such as C(arm, Treatment(reference='T0')) is not prose."""
    words = [w for w in val.split() if _LETTER.search(w)]
    return "\n" in val.strip() or len(words) >= 8 or (len(words) >= 5 and re.search(r"[.?!]", val) is not None)


@dataclass
class PiiReport:
    header: list[str]
    flagged: dict[str, str] = field(default_factory=dict)     # column -> reason code
    rows_read: int = 0
    qualtrics_header_rows: int = 1
    allow_pii: bool = False
    keep: list[str] = field(default_factory=list)

    @property
    def to_drop(self) -> list[str]:
        if self.allow_pii:
            return []
        return [c for c in self.flagged if c not in self.keep]

    @property
    def kept_with_override(self) -> list[str]:
        if self.allow_pii:
            return list(self.flagged)
        return [c for c in self.flagged if c in self.keep]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["to_drop"] = self.to_drop
        d["kept_with_override"] = self.kept_with_override
        return d


def _read_head(path: Path, max_lines: int = 4) -> list[list[str]]:
    """Read at most `max_lines` physical records (header + metadata + first data row)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row in reader:
            rows.append(row)
            if len(rows) >= max_lines:
                break
    return rows


def detect_qualtrics_header_rows(rows: list[list[str]]) -> int:
    """1 for a plain CSV; 3 when rows 2-3 are the Qualtrics question-text / ImportId rows."""
    if len(rows) >= 3 and rows[2] and any('"ImportId"' in c for c in rows[2][:5]):
        return 3
    return 1


def scan(path: str | Path, free_text_columns: list[str] | None = None,
         allow_pii: bool = False, keep: list[str] | None = None,
         extra_id_columns: list[str] | None = None,
         closed_columns: list[str] | None = None) -> PiiReport:
    path = Path(path)
    rows = _read_head(path, 3 + PROSE_ROWS)
    if not rows:
        raise ValueError(f"{path} is empty")
    header = rows[0]
    n_meta = detect_qualtrics_header_rows(rows)
    data_rows = rows[n_meta:n_meta + PROSE_ROWS]
    first = data_rows[0] if data_rows else []
    report = PiiReport(header=header, rows_read=n_meta + len(data_rows),
                       qualtrics_header_rows=n_meta, allow_pii=allow_pii, keep=list(keep or []))
    known = {c.lower() for c in KNOWN_ID_COLS + list(extra_id_columns or [])}
    free = set(free_text_columns or [])
    closed = set(closed_columns or [])
    for i, col in enumerate(header):
        reason = None
        if col.lower() in known and col not in SAFE_SYSTEM_COLS:
            reason = "known_identifier_column"
        elif col in free:
            reason = "free_text_question"
        else:
            for code, rx in HEADER_PATTERNS.items():
                if rx.search(col):
                    reason = f"header_pattern:{code}"
                    break
        if reason is None:                    # values: most non-empty sampled cells match an identifier pattern
            vals = [r[i].strip() for r in data_rows if i < len(r) and r[i].strip()]
            for code, rx in VALUE_PATTERNS.items():
                if vals and sum(bool(rx.match(v)) for v in vals) * 2 > len(vals):
                    reason = f"value_pattern:{code}"
                    break
        if reason is None and col not in closed and any(
                i < len(r) and looks_like_prose(r[i]) for r in data_rows):
            reason = "value_pattern:free_text"
        if reason:
            report.flagged[col] = reason
    return report


def assert_no_pii(csv_path: str | Path, extra_free_text: list[str] | None = None) -> list[str]:
    """Re-scan a written data file; return the list of columns that still look like PII."""
    rep = scan(csv_path, free_text_columns=extra_free_text)
    return list(rep.flagged)


def summary_line(rep: PiiReport) -> str:
    if not rep.flagged:
        return "PII scan: no identifier or free-text columns flagged."
    parts = [f"{c} [{r}]" for c, r in rep.flagged.items()]
    action = "KEPT (--allow-pii)" if rep.allow_pii else f"dropped {len(rep.to_drop)}, kept by whitelist {len(rep.kept_with_override)}"
    return f"PII scan (header + first {PROSE_ROWS} rows): flagged {len(rep.flagged)} column(s): " + ", ".join(parts) + f". Action: {action}."
