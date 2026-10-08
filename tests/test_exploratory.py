"""Exploratory agent: one writable script, a last turn reserved for record_result, salvage of a clean run that
was never recorded, and reg.reestimate (the registered specification on any subset, with or without a moderator)."""
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from filedrawer import exec as fexec
from filedrawer.agents import exploratory as EXP
from filedrawer.llm.agent import Agent
from filedrawer.tools import ToolRegistry

from test_multiarm import make_data, make_pap, run_scripts


def test_only_the_exploratory_script_is_writable(tmp_path):
    reg = ToolRegistry(tmp_path, allowed=("write_file", "run_script", "record_result"), writable=(EXP.SCRIPT,))
    assert reg.dispatch("write_file", {"path": "scripts/debug_formula.py", "content": "print(1)"}).startswith("ERROR")
    assert not (tmp_path / "scripts" / "debug_formula.py").exists()
    assert reg.dispatch("write_file", {"path": EXP.SCRIPT, "content": "print('E1 x: y')"}).startswith("wrote")


class Scripted:
    """A provider that replays tool calls and remembers which tools each call offered."""
    name = "scripted"

    def __init__(self, calls):
        self.calls, self.offered, self.messages = list(calls), [], []

    def chat(self, agent, messages, model, max_tokens, tools, temperature, meta):
        self.offered.append(sorted(t["function"]["name"] for t in tools or []))
        self.messages.append(list(messages))
        name, args = self.calls.pop(0)
        tc = SimpleNamespace(id=f"c{len(self.offered)}", name=name, args=args)
        return SimpleNamespace(content="", tool_calls=[tc], empty=False, finish_reason="tool_calls",
                               to_message=lambda: {"role": "assistant", "content": ""})


def test_last_turn_is_reserved_for_record_result(tmp_path):
    (tmp_path / "scripts").mkdir()
    tools = ToolRegistry(tmp_path, allowed=("write_file", "run_script", "record_result"), writable=(EXP.SCRIPT,))
    write = ("write_file", {"path": EXP.SCRIPT, "content": "print('hi')"})
    prov = Scripted([write, write, write, ("record_result", {"key": "exploratory", "value": [{"id": "E1"}], "final": True})])
    agent = Agent("exploratory", prov, "m", "sys", tools, max_turns=4)
    agent.finish_turn = True
    res = agent.run("task")
    assert res.stopped == "final" and res.records["exploratory"] == [{"id": "E1"}]
    assert prov.offered[0] == ["record_result", "run_script", "write_file"] and prov.offered[-1] == ["record_result"]
    assert "last turn" in prov.messages[-1][-1]["content"]
    assert "[2 turns left]" in prov.messages[2][-1]["content"]


def test_other_agents_keep_every_tool_to_the_end(tmp_path):
    tools = ToolRegistry(tmp_path, allowed=("write_file", "record_result"))
    write = ("write_file", {"path": "scripts/a.py", "content": "x"})
    prov = Scripted([write, write])
    Agent("registered", prov, "m", "sys", tools, max_turns=2).run("task")
    assert prov.offered[-1] == ["record_result", "write_file"]


def test_salvage_rebuilds_records_from_a_clean_unrecorded_run(tmp_path):
    for d in ("scripts", "results", "figures"):
        (tmp_path / d).mkdir()
    (tmp_path / "results" / "E1_items.csv").write_text("a\n1\n")
    (tmp_path / "results" / "E2_attention.csv").write_text("a\n1\n")
    (tmp_path / "figures" / "E1_items.png").write_bytes(b"")
    script = ("print('E1 item effects: all three items rise by 0.04-0.06')\n"
              "print('E2 attention robustness: estimate 0.044 vs 0.046 on passers')\n"
              "print('E3 by ideology: interaction 0.01')\n")                       # E3 wrote no table: skipped
    tools = ToolRegistry(tmp_path, allowed=("write_file", "run_script", "record_result"), writable=(EXP.SCRIPT,))
    tools.write_file(EXP.SCRIPT, script)
    assert EXP.salvage(tmp_path, tools.runs.get(EXP.SCRIPT)) == []              # never run
    tools.run_script(EXP.SCRIPT)
    out = EXP.salvage(tmp_path, tools.runs.get(EXP.SCRIPT))
    assert [e["id"] for e in out] == ["E1", "E2"]
    assert out[0]["title"] == "Item effects" and out[0]["figure"] == "figures/E1_items.png" and out[0]["salvaged"]
    assert out[1]["finding"].startswith("estimate 0.044") and out[1]["figure"] is None
    tools.write_file(EXP.SCRIPT, script + "raise SystemExit(1)\n")              # a later edit voids the clean run
    assert EXP.salvage(tmp_path, tools.runs.get(EXP.SCRIPT)) == []


@pytest.fixture(scope="module")
def study(tmp_path_factory):
    s = tmp_path_factory.mktemp("expl") / "study"
    run_scripts(s, make_pap(), make_data())
    return s


def _run(study, body):
    (study / "scripts" / "04_exploratory.py").write_text(
        "import importlib.util, pathlib, json\nimport pandas as pd\n"
        "_spec = importlib.util.spec_from_file_location('reg', pathlib.Path(__file__).with_name('03_registered.py'))\n"
        "reg = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(reg)\n"
        "df = pd.read_csv(reg.ROOT / 'data' / 'clean.csv')\n" + body, encoding="utf-8")
    res = fexec.run_script(study, "scripts/04_exploratory.py", 300)
    assert res["exit_code"] == 0, res["stderr_tail"]
    return json.loads(res["stdout_tail"].strip().splitlines()[-1])


def test_reestimate_reproduces_the_registered_arms(study):
    rows = _run(study, "print(reg.reestimate('H1', df).to_json(orient='records'))")
    arms = pd.read_csv(study / "results" / "H1_arms.csv")
    assert len(rows) == len(arms) == 11
    for r, (_, a) in zip(rows, arms.iterrows()):
        assert r["arm"] == a["arm_label"]
        assert r["estimate"] == pytest.approx(a["estimate"], abs=1e-10)
        assert r["std_error"] == pytest.approx(a["std_error"], abs=1e-10)


def test_reestimate_on_a_subset_and_by_moderator(study):
    sub = _run(study, "print(reg.reestimate('H1', df[df.gender == 1]).to_json(orient='records'))")
    assert len(sub) == 11 and sub[0]["n"] < 1800
    cat = _run(study, "print(reg.reestimate('H1', df, moderator='gender').to_json(orient='records'))")      # 1/2: levels
    assert len(cat) == 11 and cat[0]["arm"] == "Treatment 1 x gender=2" and ":C(_mod)[T.2]" in cat[0]["term"]
    for mod in ("cov1", "pid3"):                                                                       # numeric: slopes
        num = _run(study, f"print(reg.reestimate('H1', df, moderator='{mod}').to_json(orient='records'))")
        assert len(num) == 11 and num[0]["arm"] == f"Treatment 1 x {mod}" and num[0]["term"].endswith(":_mod")


def test_reestimate_drops_a_covariate_used_as_the_outcome(study):
    rows = _run(study, "h = dict(reg.HYPOTHESES[0], outcome='cov1')\n"
                       "print(reg.reestimate(h, df).to_json(orient='records'))")
    assert len(rows) == 11 and all(r["std_error"] > 1e-4 for r in rows)          # a real placebo, not cov1 on itself
