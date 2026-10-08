import json

from filedrawer.qsf import parse_qsf, Codebook


def test_demo_qsf_questions_and_columns(codebook):
    tags = {q.tag: q for q in codebook.questions}
    assert tags["imm_att"].type == "Matrix"
    assert tags["imm_att"].columns == ["imm_att_1", "imm_att_2", "imm_att_3"]
    assert [r.label for r in tags["imm_att"].rows][1].startswith("Immigration levels")
    assert len(tags["imm_att"].choices) == 7
    assert tags["open_comment"].free_text and tags["open_comment"].columns == ["open_comment"]
    assert tags["pid3"].choices[0].label == "Democrat"
    assert tags["treat_text"].columns == [] and tags["treat_text"].in_randomizer


def test_demo_qsf_arms_from_randomizer(codebook):
    assert codebook.arms.source == "flow_embedded"
    assert codebook.arms.column == "condition"
    assert codebook.arms.values == ["control", "treatment"]
    assert "Economists" in codebook.arms.stimuli["treatment"]
    assert "Census" in codebook.arms.stimuli["control"]
    assert codebook.embedded_data == ["condition"]


def test_free_text_columns(codebook):
    assert codebook.free_text_columns() == ["open_comment"]


def test_roundtrip_and_markdown(codebook, tmp_path):
    p = tmp_path / "cb.json"
    codebook.save(p)
    cb2 = Codebook.load(p)
    assert [q.tag for q in cb2.questions] == [q.tag for q in codebook.questions]
    md = cb2.to_markdown()
    assert "imm_att_2" in md and "Democrat" in md


def test_blocks_as_dict_and_recodes(tmp_path):
    qsf = {
        "SurveyEntry": {"SurveyID": "SV_x", "SurveyName": "x"},
        "SurveyElements": [
            {"Element": "BL", "Payload": {"1": {"ID": "BL_a", "Description": "A",
                                               "BlockElements": [{"Type": "Question", "QuestionID": "QID1"}]}}},
            {"Element": "FL", "Payload": {"Flow": [{"Type": "Block", "ID": "BL_a"}]}},
            {"Element": "SQ", "Payload": {"QuestionID": "QID1", "DataExportTag": "q1", "QuestionType": "MC",
                                          "Selector": "MAVR", "QuestionText": "<b>Pick</b> all",
                                          "Choices": {"1": {"Display": "a"}, "2": {"Display": "b", "TextEntry": "true"}},
                                          "ChoiceOrder": [2, 1], "RecodeValues": {"1": "10", "2": "20"}}},
        ],
    }
    p = tmp_path / "x.qsf"
    p.write_text(json.dumps(qsf))
    cb = parse_qsf(p)
    q = cb.questions[0]
    assert q.text == "Pick all"
    assert q.columns == ["q1_2", "q1_1", "q1_2_TEXT"]
    assert {c.id: c.recode for c in q.choices} == {"2": "20", "1": "10"}
    assert cb.arms.source == "none"
    assert "q1_2_TEXT" in cb.free_text_columns()
