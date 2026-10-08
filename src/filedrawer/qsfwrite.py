"""Write an importable Qualtrics .qsf from an extension design (autoexperiment's SurveyDesign schema).

No Qualtrics account, API token or MCP server is needed: the file is uploaded by hand (Qualtrics: Create project ->
Survey -> From a file). Question and envelope encoding follow the vendored qsf-generator skill
(qsfskill/references/qualtrics-encoding.md, MIT); flow elements the skill does not cover (randomizers, groups,
embedded data, branches, end of survey) follow real Qualtrics exports.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path

SID = "SV_filedrawer00000"          # Qualtrics reassigns SurveyID, RS and PreviewID on import
RSID = "RS_filedrawer00000"
REQUIRED = ("BL", "FL", "PL", "PROJ", "QC", "RS", "SCO", "SO", "STAT")
MEDIA = re.compile(r"\[(IMAGE|VIDEO|CONJOINT):\s*([^\]]*)\]", re.I)


def _desc(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text).strip()
    return plain if len(plain) <= 100 else plain[:97].rstrip() + "..."


def _el(element: str, primary, payload, secondary=None, tertiary=None) -> dict:
    return {"SurveyID": SID, "Element": element, "PrimaryAttribute": primary, "SecondaryAttribute": secondary,
            "TertiaryAttribute": tertiary, "Payload": payload}


class _Builder:
    def __init__(self, design: dict):
        self.d = design
        self.qid: dict[str, str] = {}            # design question id -> QIDn
        self.choice_n: dict[tuple[str, str], int] = {}   # (question id, choice id) -> Qualtrics choice number
        self.flow_n = 1
        self.notes: list[str] = []
        n = 0
        for b in design["blocks"]:
            for q in b["questions"]:
                n += 1
                self.qid[q["id"]] = f"QID{n}"
                for i, c in enumerate(q.get("choices") or [], 1):
                    self.choice_n[(q["id"], c["id"])] = i

    # -- text ----------------------------------------------------------------------------------------------
    def pipe(self, text: str) -> str:
        text = re.sub(r"\{\{ed:([A-Za-z0-9_]+)\}\}", r"${e://Field/\1}", text)
        return re.sub(r"\{\{answer:([A-Za-z0-9_]+)\}\}",
                      lambda m: f"${{q://{self.qid.get(m.group(1), m.group(1))}/ChoiceTextEntryValue}}", text)

    # -- questions -----------------------------------------------------------------------------------------
    def question(self, q: dict) -> dict:
        qid, text = self.qid[q["id"]], self.pipe(q["text"])
        base = {"QuestionText": text, "DataExportTag": q.get("tag") or q["id"], "DataVisibility": {"Private": False, "Hidden": False},
                "Configuration": {"QuestionDescriptionOption": "UseText"}, "QuestionDescription": _desc(text),
                "Validation": {"Settings": ({"ForceResponse": "ON", "ForceResponseType": "ON", "Type": "None"} if q.get("required")
                                            else {"Type": "None"})},
                "Language": [], "QuestionID": qid}
        t = q["type"]
        if t == "MC":
            choices = q.get("choices") or []
            base.update(QuestionType="MC", Selector="MAVR" if q.get("multiple") else "SAVR", SubSelector="TX",
                        Choices={str(i): {"Display": self.pipe(c["text"])} for i, c in enumerate(choices, 1)},
                        ChoiceOrder=[str(i) for i in range(1, len(choices) + 1)], NextChoiceId=len(choices) + 1, NextAnswerId=1)
            rec = {str(i): str(c.get("recode")) for i, c in enumerate(choices, 1) if str(c.get("recode") or "").strip()}
            if rec:
                base["RecodeValues"] = rec
        elif t == "TE":
            base.update(QuestionType="TE", Selector="SL", NextChoiceId=1, NextAnswerId=1, SearchSource={"AllowFreeResponse": "false"})
        elif t == "Matrix":
            rows, cols = q.get("rows") or [], q.get("choices") or []
            # Shape of a real Qualtrics export: no Autoscale (an invalid scale name fails import with ESDEF10)
            base["Configuration"].update(TextPosition="inline", ChoiceColumnWidth=25, RepeatHeaders="none", WhiteSpace="OFF", MobileFirst=True)
            base.update(QuestionType="Matrix", Selector="Likert", SubSelector="SingleAnswer", DefaultChoices=False, GradingData=[],
                        ChoiceDataExportTags=False,
                        Choices={str(i): {"Display": self.pipe(r["text"])} for i, r in enumerate(rows, 1)},
                        ChoiceOrder=[str(i) for i in range(1, len(rows) + 1)],
                        Answers={str(i): {"Display": c["text"]} for i, c in enumerate(cols, 1)},
                        AnswerOrder=[str(i) for i in range(1, len(cols) + 1)], NextChoiceId=len(rows) + 1, NextAnswerId=len(cols) + 1)
            # Matrix answer codes are the column positions 1..k (no RecodeValues, as in real exports); a design whose
            # recodes differ from positions gets a note so the authors can set them after import.
            if any(str(c.get("recode") or "").strip() not in ("", str(i)) for i, c in enumerate(cols, 1)):
                self.notes.append(f"{q.get('tag') or q['id']}: set the matrix recode values after import "
                                  + ", ".join(f"{c['text']}={c.get('recode')}" for c in cols))
        else:                                            # DB: descriptive text
            base.update(QuestionType="DB", Selector="TB", DefaultChoices=False, ChoiceOrder=[], GradingData=[], NextChoiceId=1, NextAnswerId=1)
            base.pop("DataExportTag")
            base["DataExportTag"] = q["id"]
        return base

    # -- flow ----------------------------------------------------------------------------------------------
    def fid(self) -> str:
        self.flow_n += 1
        return f"FL_{self.flow_n}"

    def node(self, nid: str, nodes: dict, default_block: str) -> dict:
        n = nodes[nid]
        t = n["type"]
        if t == "block":
            bid = f"BL_{n['blockId']}"
            return {"Type": "Block" if n["blockId"] == default_block else "Standard", "ID": bid, "FlowID": self.fid(), "Autofill": []}
        if t == "embedded":
            value = n.get("value", "")
            m = MEDIA.search(value)
            if m:
                self.notes.append(f"{n['field']}: {m.group(1).lower()} stimulus to supply ({m.group(2).strip()[:80]})")
                value = f"[{m.group(1).upper()} TO SUPPLY: {m.group(2).strip()}]"
            return {"Type": "EmbeddedData", "FlowID": self.fid(), "EmbeddedData": [
                {"Description": n["field"], "Type": "Custom", "Field": n["field"], "VariableType": "String", "DataVisibility": [],
                 "AnalyzeText": False, "Value": self.pipe(value)}]}
        if t == "end":
            return {"Type": "EndSurvey", "FlowID": self.fid()}
        fid = self.fid()
        kids = [self.node(c, nodes, default_block) for c in n.get("children", [])]
        if t == "randomizer":
            return {"Type": "BlockRandomizer", "FlowID": fid, "SubSet": 1, "EvenPresentation": True, "Flow": kids}
        if t == "group":
            return {"Type": "Group", "FlowID": fid, "Description": n.get("label") or nid, "Flow": kids}
        if t == "branch":
            if n.get("questionId"):
                qid = self.qid[n["questionId"]]
                k = self.choice_n.get((n["questionId"], n.get("choiceId")), 1)
                loc = f"q://{qid}/SelectableChoice/{k}"
                op = "Selected" if n.get("operator", "selected") == "selected" else "NotSelected"
                expr = {"LogicType": "Question", "QuestionID": qid, "QuestionIsInLoop": "no", "ChoiceLocator": loc, "Operator": op,
                        "QuestionIDFromLocator": qid, "LeftOperand": loc, "Type": "Expression",
                        "Description": f"If {qid} choice {k} is {op.lower()}"}
            else:
                op = "EqualTo" if n.get("operator", "equals") == "equals" else "NotEqualTo"
                expr = {"LogicType": "EmbeddedField", "LeftOperand": n.get("field", ""), "Operator": op, "RightOperand": n.get("value", ""),
                        "_HiddenExpression": False, "Type": "Expression", "Description": f"If {n.get('field')} {op} {n.get('value')}"}
            return {"Type": "Branch", "FlowID": fid, "Description": n.get("label") or "Branch",
                    "BranchLogic": {"0": {"0": expr, "Type": "If"}, "Type": "BooleanExpression"}, "Flow": kids}
        raise ValueError(f"unknown flow node type {t!r}")

    # -- survey --------------------------------------------------------------------------------------------
    def build(self, name: str) -> dict:
        d = self.d
        blocks = d["blocks"]
        default_block = blocks[0]["id"]
        bl = []
        for i, b in enumerate(blocks):
            els = []
            for j, q in enumerate(b["questions"]):
                if j:
                    els.append({"Type": "Page Break"})
                els.append({"Type": "Question", "QuestionID": self.qid[q["id"]]})
            bl.append({"Type": "Default" if i == 0 else "Standard", "Description": b["title"], "ID": f"BL_{b['id']}", "BlockElements": els})
        bl.append({"Type": "Trash", "Description": "Trash / Unused Questions", "ID": "BL_trash"})
        nodes = {n["id"]: n for n in d["flow"]}
        flow = [self.node(r, nodes, default_block) for r in d["roots"]]
        fl = {"Type": "Root", "FlowID": "FL_1", "Flow": flow, "Properties": {"Count": self.flow_n, "RemovedFieldsets": []}}
        sq = [_el("SQ", self.qid[q["id"]], self.question(q), _desc(q["text"])[:80]) for b in blocks for q in b["questions"]]
        now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "SurveyEntry": {"SurveyID": SID, "SurveyName": name[:100], "SurveyDescription": None, "SurveyOwnerID": "UR_filedrawer00000",
                            "SurveyBrandID": "", "DivisionID": None, "SurveyLanguage": "EN", "SurveyActiveResponseSet": RSID,
                            "SurveyStatus": "Inactive", "SurveyStartDate": "0000-00-00 00:00:00", "SurveyExpirationDate": "0000-00-00 00:00:00",
                            "SurveyCreationDate": now, "CreatorID": "UR_filedrawer00000", "LastModified": now,
                            "LastAccessed": "0000-00-00 00:00:00", "LastActivated": "0000-00-00 00:00:00", "Deleted": None},
            "SurveyElements": [
                _el("BL", "Survey Blocks", bl), _el("FL", "Survey Flow", fl),
                _el("PL", "Preview Link", {"PreviewType": "Brand", "PreviewID": str(uuid.uuid4())}),
                _el("PROJ", "CORE", {"ProjectCategory": "CORE", "SchemaVersion": "1.1.0"}, tertiary="1.1.0"),
                _el("QC", "Survey Question Count", None, secondary=str(len(sq))),
                _el("RS", RSID, None, secondary="Default Response Set"),
                _el("SCO", "Scoring", {"ScoringCategories": [], "ScoringCategoryGroups": [], "ScoringSummaryCategory": None,
                                       "ScoringSummaryAfterQuestions": 0, "ScoringSummaryAfterSurvey": 0, "DefaultScoringCategory": None,
                                       "AutoScoringCategory": None}),
                _el("SO", "Survey Options", {"BackButton": "false", "SaveAndContinue": "true", "SurveyProtection": "PublicSurvey",
                                             "BallotBoxStuffingPrevention": "false", "NoIndex": "Yes", "SecureResponseFiles": "true",
                                             "SurveyExpiration": "None", "SurveyTermination": "DefaultMessage", "Header": "", "Footer": "",
                                             "ProgressBarDisplay": "None", "PartialData": "+1 week", "ValidationMessage": None,
                                             "PreviousButton": "", "NextButton": "", "SurveyTitle": name[:100], "SkinLibrary": "Qualtrics",
                                             "SkinType": "templated", "Skin": {"brandingId": None, "templateId": "*base", "overrides": None},
                                             "NewScoring": 1, "SurveyMetaDescription": _desc(d.get("overview", ""))}),
                _el("STAT", "Survey Statistics", {"MobileCompatible": True, "ID": "Survey Statistics"}),
            ] + sq,
        }


def design_to_qsf(proposal: dict, source_title: str = "") -> tuple[dict, list[str]]:
    """Returns (qsf, notes). Notes list the media stimuli the authors must supply after import."""
    b = _Builder(proposal["design"])
    name = f"{proposal['design']['title']}" + (f" (extension of: {source_title})" if source_title else "")
    return b.build(name), b.notes


def qsf_errors(qsf: dict) -> list[str]:
    """The skill's pre-delivery checks plus internal consistency (flow -> blocks, branches -> questions)."""
    errs = []
    if set(qsf) != {"SurveyEntry", "SurveyElements"}:
        errs.append("top level must be exactly SurveyEntry and SurveyElements")
    els = qsf.get("SurveyElements", [])
    kinds = {e.get("Element") for e in els}
    errs += [f"missing element {k}" for k in REQUIRED if k not in kinds]
    sq = [e for e in els if e.get("Element") == "SQ"]
    if not sq:
        errs.append("no questions")
    qids = set()
    for e in sq:
        p = e.get("Payload") or {}
        qids.add(p.get("QuestionID"))
        tag = p.get("QuestionID")
        if "DataVisibility" not in p or "Configuration" not in p:
            errs.append(f"{tag}: DataVisibility and Configuration are required")
        if "QuestionType" not in p:
            errs.append(f"{tag}: QuestionType missing")
        if any(not isinstance(x, str) for x in p.get("ChoiceOrder", [])):
            errs.append(f"{tag}: ChoiceOrder values must be strings")
        if p.get("QuestionType") in ("MC", "Matrix") and not isinstance(p.get("Choices"), dict):
            errs.append(f"{tag}: Choices must be a keyed dict")
        if p.get("QuestionType") == "Matrix" and not p.get("Answers"):
            errs.append(f"{tag}: Matrix needs Answers (columns)")
        if len(p.get("QuestionDescription", "")) > 100:
            errs.append(f"{tag}: QuestionDescription longer than 100 characters")
        if p.get("QuestionType") == "TE" and "SearchSource" not in p:
            errs.append(f"{tag}: TE needs SearchSource")
    qc = next((e for e in els if e.get("Element") == "QC"), {})
    if qc and qc.get("SecondaryAttribute") != str(len(sq)):
        errs.append("QC count does not match the number of questions")
    bl = next((e for e in els if e.get("Element") == "BL"), {}).get("Payload") or []
    block_ids = {b["ID"] for b in bl}
    if bl and bl[0].get("Type") != "Default":
        errs.append("first block must be Default")
    in_blocks = {x["QuestionID"] for b in bl for x in b.get("BlockElements", []) if x.get("Type") == "Question"}
    if in_blocks != qids:
        errs.append("every question must sit in exactly the blocks listed")
    fl = next((e for e in els if e.get("Element") == "FL"), {}).get("Payload") or {}
    ids = []

    def walk(n):
        ids.append(n.get("FlowID"))
        if n.get("Type") in ("Block", "Standard") and n.get("ID") not in block_ids:
            errs.append(f"flow references unknown block {n.get('ID')}")
        if n.get("Type") == "Branch":
            for grp in (n.get("BranchLogic") or {}).values():
                if isinstance(grp, dict):
                    for ex in grp.values():
                        if isinstance(ex, dict) and ex.get("LogicType") == "Question" and ex.get("QuestionID") not in qids:
                            errs.append(f"branch references unknown question {ex.get('QuestionID')}")
        for c in n.get("Flow", []) or []:
            walk(c)
    walk(fl)
    if len(ids) != len(set(ids)):
        errs.append("duplicate FlowIDs")
    if fl and fl.get("Properties", {}).get("Count") != max(int(i.split("_")[1]) for i in ids if i):
        errs.append("flow Properties.Count must equal the highest FlowID number")
    return errs


def write_qsf(proposal: dict, path: Path, source_title: str = "") -> tuple[list[str], list[str]]:
    """Write <path>.qsf; returns (errors, notes). Nothing is written when there are errors."""
    qsf, notes = design_to_qsf(proposal, source_title)
    errs = qsf_errors(qsf)
    if not errs:
        Path(path).write_text(json.dumps(qsf, indent=1, ensure_ascii=False), encoding="utf-8")
    return errs, notes
