"""QSF export of extension designs: the skill's checks, a round trip through filedrawer's own QSF reader, the server download."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT / "tests" / "fixtures" / "mock" / "extensions"


def _proposal(turn=0) -> dict:
    return json.loads((MOCK / f"{turn}.json").read_text())["tool_calls"][0]["args"]["value"]


@pytest.mark.parametrize("turn", [0, 1, 2])
def test_design_converts_to_valid_qsf_and_parses_back(tmp_path, turn):
    from filedrawer import qsfwrite as W
    from filedrawer.qsf import parse_qsf
    p = _proposal(turn)
    errs, notes = W.write_qsf(p, tmp_path / "x.qsf", "Demo")
    assert errs == []
    qsf = json.loads((tmp_path / "x.qsf").read_text())
    assert set(qsf) == {"SurveyEntry", "SurveyElements"} and qsf["SurveyEntry"]["SurveyStatus"] == "Inactive"
    kinds = {e["Element"] for e in qsf["SurveyElements"]}
    assert set(W.REQUIRED) <= kinds
    cb = parse_qsf(tmp_path / "x.qsf")
    n_q = sum(len(b["questions"]) for b in p["design"]["blocks"])
    assert len(cb.questions) == n_q
    assert cb.arms.column == "condition" and len(cb.arms.values) == len(p["design"]["conditions"])
    fl = next(e for e in qsf["SurveyElements"] if e["Element"] == "FL")["Payload"]
    types = set()
    def walk(n):
        types.add(n["Type"])
        for c in n.get("Flow", []):
            walk(c)
    walk(fl)
    assert {"BlockRandomizer", "Group", "EmbeddedData", "Branch", "EndSurvey"} <= types
    sq = {e["Payload"]["DataExportTag"]: e["Payload"] for e in qsf["SurveyElements"] if e["Element"] == "SQ"}
    assert sq["policy_support"]["RecodeValues"]["7"] == "7" and sq["consent"]["Validation"]["Settings"]["ForceResponse"] == "ON"


def test_piping_and_media_markers(tmp_path):
    from filedrawer import qsfwrite as W
    p = _proposal(0)
    blk = p["design"]["blocks"][1]
    blk["questions"][0]["text"] = "Look at this: {{ed:post1}}"
    arm_node = next(n for n in p["design"]["flow"] if n["type"] == "group")
    arm_node["children"].insert(0, "media1")
    p["design"]["flow"].append({"id": "media1", "type": "embedded", "label": "", "children": [], "blockId": "", "field": "post1",
                                "value": "[IMAGE: a street scene]", "questionId": "", "choiceId": "", "operator": "equals"})
    qsf, notes = W.design_to_qsf(p)
    assert W.qsf_errors(qsf) == []
    text = json.dumps(qsf)
    assert "${e://Field/post1}" in text and "IMAGE TO SUPPLY: a street scene" in text
    assert notes and "post1" in notes[0]


def test_checks_catch_broken_files():
    from filedrawer import qsfwrite as W
    qsf, _ = W.design_to_qsf(_proposal(1))
    qsf["SurveyElements"] = [e for e in qsf["SurveyElements"] if e["Element"] != "SO"]
    assert "missing element SO" in W.qsf_errors(qsf)
    qsf2, _ = W.design_to_qsf(_proposal(1))
    sq = next(e for e in qsf2["SurveyElements"] if e["Element"] == "SQ" and e["Payload"]["QuestionType"] == "MC")
    sq["Payload"]["ChoiceOrder"] = [1, 2]
    assert any("ChoiceOrder" in e for e in W.qsf_errors(qsf2))


def test_server_qsf_route(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(ROOT))
    import server.app as app
    from filedrawer import qsfwrite as W
    qsf, _ = W.design_to_qsf(_proposal(0))
    rec = {"id": "0123456789ab", "slug": "demo", "folder_url": "https://github.com/x/demo/tree/main"}
    monkeypatch.setattr(app, "load_record", lambda rid: rec if rid == "0123456789ab" else None)
    seen = {}
    def fake_fetch(url, timeout=10, max_bytes=0):
        seen["url"] = url
        return json.dumps(qsf)
    monkeypatch.setattr(app.fdharvest, "fetch_text", fake_fetch)
    sent = {}
    class H:
        _qsf = app.Handler._qsf
        def _send(self, status, body, ctype, extra=None, head_only=False): sent.update(status=status, ctype=ctype, extra=extra, body=body)
        def _json(self, status, obj, extra=None, head_only=False): sent.update(status=status, obj=obj)
    H()._qsf("0123456789ab/mechanism")
    assert sent["status"] == 200 and "attachment" in sent["extra"]["Content-Disposition"]
    assert seen["url"] == "https://raw.githubusercontent.com/x/demo/main/extensions/mechanism.qsf"
    H()._qsf("../../etc/passwd")
    assert sent["status"] == 404



def test_matrix_matches_real_export_shape():
    from filedrawer import qsfwrite as W
    p = _proposal(0)
    p["design"]["blocks"][-2]["questions"].append({"id": "mx", "type": "Matrix", "text": "Rate the following.", "tag": "mx", "multiple": False,
        "required": False, "choices": [{"id": "a", "text": "Low", "recode": "1"}, {"id": "b", "text": "High", "recode": "2"}],
        "rows": [{"id": "r1", "text": "Useful"}], "sourceQuestionId": ""})
    qsf, _ = W.design_to_qsf(p)
    mx = next(e["Payload"] for e in qsf["SurveyElements"] if e["Element"] == "SQ" and e["Payload"]["QuestionType"] == "Matrix")
    assert "Autoscale" not in mx["Configuration"] and "RecodeValues" not in mx
    assert W.qsf_errors(qsf) == []
