import csv
import json

from filedrawer import pii


def test_scan_reads_only_header_and_first_rows(demo_csv, codebook, monkeypatch):
    seen = []
    orig = pii._read_head

    def spy(path, max_lines=4):
        rows = orig(path, max_lines)
        seen.append(len(rows))
        return rows
    monkeypatch.setattr(pii, "_read_head", spy)
    rep = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns())
    assert seen == [3 + pii.PROSE_ROWS]      # header + 2 qualtrics meta rows + first PROSE_ROWS data rows
    assert rep.rows_read == 3 + pii.PROSE_ROWS and rep.qualtrics_header_rows == 3


def test_flags_known_free_text_and_values(demo_csv, codebook):
    rep = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns())
    assert rep.flagged["IPAddress"] == "known_identifier_column"
    assert rep.flagged["open_comment"] == "free_text_question"
    assert "RecipientEmail" in rep.to_drop
    assert "pid3" not in rep.flagged and "Finished" not in rep.flagged
    assert "StartDate" not in rep.flagged and "EndDate" not in rep.flagged
    # report never carries cell values
    dumped = json.dumps(rep.to_dict())
    assert "CANARY" not in dumped and "example.com" not in dumped


def test_value_pattern_detection(tmp_path):
    p = tmp_path / "plain.csv"
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["contact", "score", "note"])
        w.writerow(["someone@uni.edu", "3", "fine"])
        w.writerow(["other@uni.edu", "4", "ok"])
    rep = pii.scan(p)
    assert rep.flagged == {"contact": "value_pattern:email"}
    assert rep.qualtrics_header_rows == 1


def test_bypass_and_whitelist(demo_csv, codebook):
    rep = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns(), allow_pii=True)
    assert rep.to_drop == [] and "IPAddress" in rep.kept_with_override
    rep2 = pii.scan(demo_csv, free_text_columns=codebook.free_text_columns(), keep=["RecordedDate"])
    assert "RecordedDate" not in rep2.to_drop and rep2.kept_with_override == ["RecordedDate"]
    assert "IPAddress" in rep2.to_drop


def _write(p, header, rows):
    with open(p, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return p


def test_free_text_detected_without_qsf(tmp_path):
    # a chat transcript, an open answer that is short in row 1 but prose later, and closed-ended columns
    p = _write(tmp_path / "chat.csv", ["treatment", "chat", "recall", "strength", "label"], [
        ["1", "User:\nWhy does it cost so much\nBot:\nGood question", "cut taxes", "5", "Strongly agree"],
        ["0", "User:\nhi", "Infrastructure spending creates jobs and keeps the trains running safely", "3", "Agree"],
        ["1", "User:\nok", "less", "6", "Neither agree nor disagree"],
    ])
    rep = pii.scan(p)
    assert rep.flagged == {"chat": "value_pattern:free_text", "recall": "value_pattern:free_text"}
    assert "Infrastructure" not in json.dumps(rep.to_dict())          # reasons only, never values


def test_closed_ended_long_labels_are_exempt(tmp_path):
    label = "I did not leave my home, except for necessities such as food and medicine."
    p = _write(tmp_path / "labels.csv", ["behaviour", "comment"], [[label, label]])
    rep = pii.scan(p, closed_columns=["behaviour"])
    assert rep.flagged == {"comment": "value_pattern:free_text"}


def test_short_values_are_not_prose():
    for v in ["Strongly agree", "Neither agree nor disagree", "4-year College Degree", "3", "Democrat", ""]:
        assert not pii.looks_like_prose(v), v
    assert pii.looks_like_prose("I think we should spend more on trains.")
    assert pii.looks_like_prose("line one\nline two")
    assert not pii.looks_like_prose("C(arm_code, Treatment(reference='T0'))[T.T1]")     # model terms are not prose
    assert not pii.looks_like_prose("post_ap ~ C(arm_code, Treatment(reference='T0')) + pre_ap")


def test_platform_ids_and_ipv6_are_caught_by_value_even_when_renamed(tmp_path):
    """Prolific ids, MTurk worker ids and IPv6 addresses are found by their values, so a renamed column is still
    dropped; party id (pid3), numeric codes and ordinary words are not."""
    rows = [["code_a", "code_b", "src", "pid3", "q5", "word"],
            ["5f3c9a1b2d4e6f708192a3b4", "A1Z2X3C4V5B6N7", "2001:db8:85a3:0:0:8a2e:370:7334", "2", "1043", "Democrat"],
            ["60a1b2c3d4e5f60718293a4b", "A3KLM9PQ2RST4U", "fe80:0:0:0:202:b3ff:fe1e:8329", "1", "2210", "Republican"],
            ["61b2c3d4e5f6071829304a5b", "AZ8Y7X6W5V4U3T", "2001:db8:0:0:1:0:0:1", "3", "877", "Independent"]]
    p = tmp_path / "ids.csv"
    with open(p, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    flagged = pii.scan(p).flagged
    assert flagged.get("code_a") == "value_pattern:prolific_id"
    assert flagged.get("code_b") == "value_pattern:mturk_id"
    assert flagged.get("src") == "value_pattern:ipv6"
    assert not {"pid3", "q5", "word"} & set(flagged)


def test_platform_id_headers():
    for col in ("pid", "PID", "prolific_pid", "mturk_worker", "turkID"):
        assert pii.HEADER_PATTERNS["id"].search(col), col
    for col in ("pid3", "rapid", "paid"):
        assert not pii.HEADER_PATTERNS["id"].search(col), col


def test_cloudresearch_and_qualtrics_ids_are_caught_by_value(tmp_path):
    """CloudResearch Connect participant ids (32 upper-case hex), assignment/project UUIDs and Qualtrics response ids
    are dropped by value under any column name (formats checked against real Connect exports)."""
    rows = [["c1", "c2", "c3", "agree"],
            ["9A3F0C21B7D84E5F6A7B8C9D0E1F2A3B", "1f2e3d4c-5b6a-4789-9abc-def012345678", "R_1aB2cD3eF4gH5iJ", "Agree"],
            ["0B4E1D32C8E95F607B8C9D0E1F2A3B4C", "2a3b4c5d-6e7f-4a8b-9cde-f01234567890", "R_9zY8xW7vU6tS5rQ", "Disagree"],
            ["1C5F2E43D9FA6071C9D0E1F2A3B4C5D6", "3b4c5d6e-7f80-4b9c-8def-012345678901", "R_2kL3mN4oP5qR6sT", "Agree"]]
    p = tmp_path / "cr.csv"
    with open(p, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    flagged = pii.scan(p).flagged
    assert flagged.get("c1") == "value_pattern:cloudresearch_id"
    assert flagged.get("c2") == "value_pattern:uuid"
    assert flagged.get("c3") == "value_pattern:qualtrics_response_id"
    assert "agree" not in flagged
