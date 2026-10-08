"""The optional PII model layer: which columns it reads (text, mostly distinct values), when it flags them, that its
report never carries cell values, and that asking for it without the extra installed fails loudly."""
import os

import pandas as pd
import pytest

from filedrawer import pii_model as PM


class FakeModel:
    """Stands in for GLiNER2: 'finds' a person in any value with two capitalised words, an email in any value with @."""

    def extract_entities(self, text, labels, threshold=0.5):
        ents = {}
        words = text.split()
        if len(words) >= 2 and all(w[:1].isupper() for w in words[:2]):
            ents["person"] = [words[0] + " " + words[1]]
        if "@" in text:
            ents["email"] = [text]
        return {"entities": ents}


def frame(n=120):
    first = ["Maria", "James", "Lucia", "Daniel", "Sofia", "Kevin", "Ana", "Robert", "Camila", "Tyler"]
    last = ["Hernandez", "Smith", "Lopez", "Johnson", "Garcia", "Nguyen", "Martinez", "Brown"]
    return pd.DataFrame({
        "respondent": [f"{first[i % 10]} {last[i % 8]}{i}" for i in range(n)],           # names under an innocent header
        "contact": [f"user{i}@example.org" for i in range(n)],
        "party": ["Democrat", "Republican", "Independent"] * (n // 3),                # few distinct values: never read
        "arm": [f"T{i % 34}" for i in range(n)],                                          # short codes: never read
        "age": [str(18 + i % 70) for i in range(n)],                                      # numeric: never read
        "slogan": [f"plan {i} works" for i in range(n)],                                  # distinct text, no PII
        "StartDate": [f"2023-11-{1 + i % 28:02d} 10:{i % 60:02d}:00" for i in range(n)],  # timestamps: never read
        "EndDate": [f"2023-11-{1 + i % 28:02d}T11:{i % 60:02d}:05Z" for i in range(n)],
    })


def test_only_distinct_text_columns_are_read():
    cols = PM.candidates(frame())
    assert set(cols) == {"respondent", "contact", "slogan"}
    assert all(len(v) <= PM.SAMPLE for v in cols.values())


def test_names_and_contacts_flagged_categoricals_never_read():
    flagged = PM.scan_frame(frame(), model=FakeModel())
    assert flagged == {"respondent": "model:person", "contact": "model:email"}


def test_rule_flagged_columns_are_skipped_and_reasons_carry_no_values():
    flagged = PM.scan_frame(frame(), exclude={"contact"}, model=FakeModel())
    assert set(flagged) == {"respondent"}
    assert all(r.startswith("model:") and "@" not in r and "Maria" not in r for r in flagged.values())


def test_requested_but_not_installed_fails_loudly(monkeypatch):
    monkeypatch.setattr(PM, "available", lambda: False)
    with pytest.raises(RuntimeError, match="pii-model"):
        PM.scan_frame(frame())


@pytest.mark.skipif(not PM.available() or not os.environ.get("FD_TEST_PII_MODEL"),
                    reason="real model: pip install 'filedrawer[pii-model]' and set FD_TEST_PII_MODEL=1")
def test_real_model_on_fictional_values():
    flagged = PM.scan_frame(frame())
    assert "respondent" in flagged and "contact" in flagged
    assert not {"party", "arm", "age", "slogan"} & set(flagged)
