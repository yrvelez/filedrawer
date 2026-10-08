"""Optional second PII layer: a small local model (GLiNER2-PII) reads a sample of cells in the columns the rules kept.

Install with `pip install 'filedrawer[pii-model]'` and turn on with `--pii-model` (or `pii: {model: true}`). The model
runs on this machine, on CPU by default; no data leaves it, and the report names columns and entity types, never
cell values.

Where it helps: a column of names or contact details under an innocent header, which header and value rules cannot
see. Where it does not: short categorical or numeric values (party labels, arm codes, ages), which it misreads as
people or postal codes. So only text columns whose values are mostly distinct are read (the benchmark on four real
studies: every false alarm was a column with a handful of distinct values).
"""
from __future__ import annotations

import re
from functools import lru_cache

MODEL_ID = "fastino/gliner2-privacy-filter-PII-multi"
LABELS = ("person", "email", "phone_number", "street_address", "date_of_birth", "government_id", "ip_address",
          "account_id", "payment_card")
SAMPLE = 40                  # cells read per column
THRESHOLD = 0.5              # entity confidence
FLAG_SHARE = 0.30            # share of sampled cells with an entity that flags the column
MIN_DISTINCT = 20            # a column needs this many distinct values ...
MIN_DISTINCT_SHARE = 0.5     # ... and this share of its non-empty values distinct, to be read at all
INSTALL_HINT = "pip install 'filedrawer[pii-model]'"
_NUMERIC = re.compile(r"^\s*[-+]?\d+(\.\d+)?\s*$")
_CODE = re.compile(r"^[A-Za-z]{0,3}[-_]?\d{1,4}[A-Za-z]?$")     # arm / condition / item codes such as T12, AB3, Q7b
_TIMESTAMP = re.compile(r"^\d{1,4}[-/]\d{1,2}[-/]\d{1,4}([ T]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*([AaPp][Mm])?(Z|[+-]\d{2}:?\d{2})?)?$")   # survey start/end times


def available() -> bool:
    try:
        import gliner2  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=2)
def _load(model_id: str):
    from gliner2 import GLiNER2
    return GLiNER2.from_pretrained(model_id)


def _entities(model, text: str, threshold: float) -> list[str]:
    r = model.extract_entities(text, list(LABELS), threshold=threshold)
    return [k for k, v in ((r or {}).get("entities") or {}).items() if v]


def candidates(df, exclude=()) -> dict[str, list[str]]:
    """Columns worth reading, with their sampled distinct values: text (not numeric), mostly distinct values.
    Returns {column: up to SAMPLE distinct non-empty values}."""
    out = {}
    skip = set(exclude)
    for col in df.columns:
        if col in skip:
            continue
        vals = [str(v).strip() for v in df[col].tolist() if v is not None and str(v).strip() and str(v).strip().lower() not in ("nan", "none", "na")]
        if not vals:
            continue
        head = vals[:200]
        if sum(bool(_NUMERIC.match(v) or _CODE.match(v) or _TIMESTAMP.match(v)) for v in head) > 0.8 * len(head):
            continue
        distinct = list(dict.fromkeys(vals))
        if len(distinct) < MIN_DISTINCT or len(distinct) < MIN_DISTINCT_SHARE * len(vals):
            continue
        out[col] = distinct[:SAMPLE]
    return out


def scan_frame(df, exclude=(), model=None, model_id: str = MODEL_ID, threshold: float = THRESHOLD,
               flag_share: float = FLAG_SHARE) -> dict[str, str]:
    """{column: reason} for the kept columns the model finds personal information in, e.g. 'model:person'.
    `exclude` are columns already flagged by the rules. `model` may be passed in (tests); otherwise it is loaded."""
    cols = candidates(df, exclude)
    if not cols:
        return {}
    if model is None:
        if not available():
            raise RuntimeError(f"the PII model layer is turned on but not installed: {INSTALL_HINT}")
        model = _load(model_id)
    flagged = {}
    for col, vals in cols.items():
        hits, kinds = 0, {}
        for v in vals:
            found = _entities(model, v[:1000], threshold)
            if found:
                hits += 1
                for k in found:
                    kinds[k] = kinds.get(k, 0) + 1
        if vals and hits / len(vals) >= flag_share:
            top = sorted(kinds, key=lambda k: -kinds[k])[:2]
            flagged[col] = "model:" + "+".join(top)
    return flagged
