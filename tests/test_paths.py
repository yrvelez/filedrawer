"""Local paths never reach a package's logs: inside the study they become relative, elsewhere a bare file name."""
import json

from filedrawer.llm.client import RequestLog
from filedrawer.paths import scrub


def test_paths_inside_and_outside_the_study(tmp_path):
    study = tmp_path / "study"
    msg = (f"pap: loaded author-supplied /Users/someone/Projects/x/pap_labeled.json (skipped); "
           f"wrote {study}/data/clean.csv and {study}.")
    out = scrub(msg, study)
    assert "/Users/" not in out and str(tmp_path) not in out
    assert "pap_labeled.json (skipped)" in out and "wrote data/clean.csv and .." in out


def test_windows_and_traceback_paths():
    out = scrub('File "C:\\Users\\someone\\study\\scripts\\04_exploratory.py", line 3 and /home/someone/x.py:12')
    assert "someone" not in out and "04_exploratory.py" in out and "x.py:12" in out


def test_relative_paths_and_urls_are_left_alone():
    s = "see results/H1.csv and https://github.com/owner/repo/tree/main/data"
    assert scrub(s) == s


def test_request_log_is_scrubbed(tmp_path):
    log = tmp_path / "study" / "provenance" / "llm_log.jsonl"
    RequestLog(log).write({"request": {"messages": [{"content": "Traceback: /Users/someone/Projects/a/b.py"}]}})
    rec = json.loads(log.read_text())
    assert rec["request"]["messages"][0]["content"] == "Traceback: b.py"
