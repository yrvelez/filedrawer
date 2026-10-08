import json
import os
import textwrap
from pathlib import Path

import pytest

from filedrawer import exec as fexec
from filedrawer.tools import ToolRegistry
from filedrawer.llm.mock import MockProvider
from filedrawer.llm.agent import Agent


@pytest.fixture
def study(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "clean.csv").write_text("a,b\n1,2\n")
    return tmp_path


def test_run_script_scrubs_env_and_reports_files(study, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-secret")
    (study / "scripts" / "s.py").write_text(textwrap.dedent("""
        import os, pathlib
        assert 'OPENROUTER_API_KEY' not in os.environ
        pathlib.Path('results').mkdir(exist_ok=True)
        pathlib.Path('results/t.csv').write_text('x\\n1\\n')
        print('mean=1.0')
    """))
    res = fexec.run_script(study, "scripts/s.py", timeout_s=30)
    assert res["exit_code"] == 0 and "mean=1.0" in res["stdout_tail"]
    assert res["files_created"] == ["results/t.csv"]


def test_timeout_and_traversal(study):
    (study / "scripts" / "slow.py").write_text("import time; time.sleep(30)")
    res = fexec.run_script(study, "scripts/slow.py", timeout_s=1)
    assert res["exit_code"] == 124 and "TIMEOUT" in res["stderr_tail"]
    with pytest.raises(ValueError):
        fexec.safe_relpath(study, "../etc/passwd")


def test_row_dump_guard(study):
    (study / "scripts" / "dump.py").write_text("print('\\n'.join(f'{i},{i*2},{i*3},{i*4},{i*5}' for i in range(60)))")
    res = fexec.run_script(study, "scripts/dump.py", timeout_s=30)
    assert "suppressed" in res["stdout_tail"]


def test_tools_jail(study):
    t = ToolRegistry(study, timeout_s=30)
    assert t.read_file("data/clean.csv").startswith("ERROR")
    assert t.write_file("results/x.csv", "1").startswith("ERROR")
    assert t.write_file("../x.py", "1").startswith("ERROR")
    assert t.write_file("scripts/a.py", "print(1)").startswith("wrote")
    assert t.write_file("scripts/a.py", "print(2)").startswith("wrote")
    assert (study / "provenance" / "script_history" / "a.py.1").read_text() == "print(1)"
    assert "a.py" in t.list_dir("scripts")
    assert t.record_result("k", {"v": 1}, final=True) and t.final and t.records["k"] == {"v": 1}
    assert t.run_script("results/x").startswith("ERROR")


def test_agent_loop_with_mock_fixture(study, tmp_path):
    fx = tmp_path / "fx"
    (fx / "tester").mkdir(parents=True)
    (fx / "body.py").write_text("print('ok')")
    (fx / "tester" / "0.json").write_text(json.dumps({"content": "writing", "tool_calls": [
        {"name": "write_file", "args": {"path": "scripts/go.py", "content": {"$file": "body.py"}}},
        {"name": "run_script", "args": {"path": "scripts/go.py"}}]}))
    (fx / "tester" / "1.json").write_text(json.dumps({"content": "done", "tool_calls": [
        {"name": "record_result", "args": {"key": "out", "value": {"n": 1}, "final": True}}]}))
    log = tmp_path / "log.jsonl"
    prov = MockProvider(fx, log_path=log)
    tools = ToolRegistry(study, timeout_s=30)
    ag = Agent("tester", prov, "mock", "sys", tools, max_turns=5, max_tokens=100)
    res = ag.run("task")
    assert res.stopped == "final" and res.turns == 2 and res.records == {"out": {"n": 1}}
    assert (study / "scripts" / "go.py").read_text() == "print('ok')"
    tool_msgs = [m for m in res.transcript if m["role"] == "tool"]
    assert '"exit_code": 0' in tool_msgs[1]["content"]
    assert len(log.read_text().splitlines()) == 2
    assert prov.usage.by_agent["tester"]["calls"] == 2


def test_agent_max_turns(study, tmp_path):
    fx = tmp_path / "fx2"
    (fx / "loop").mkdir(parents=True)
    for i in range(3):
        (fx / "loop" / f"{i}.json").write_text(json.dumps({"tool_calls": [{"name": "list_dir", "args": {"path": "scripts"}}]}))
    ag = Agent("loop", MockProvider(fx), "mock", "sys", ToolRegistry(study), max_turns=2)
    assert ag.run("t").stopped == "max_turns"

