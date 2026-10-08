"""Qualtrics QSF (survey schema) -> Codebook.

Scope: SQ (questions: MC, TE, Matrix, Slider, DB, Timing), BL (blocks), FL (flow:
BlockRandomizer, Branch, EmbeddedData), plus survey entry metadata. Column names
are derived from DataExportTag following Qualtrics export rules and reconciled
against the CSV header by tidy.py.

The codebook is the only schema-level artifact that is shown to an LLM. It
contains question text and choice labels, never responses.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str | None) -> str:
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class Choice:
    id: str
    label: str
    recode: str | None = None
    text_entry: bool = False


@dataclass
class Question:
    qid: str
    tag: str
    type: str
    selector: str
    text: str
    block: str | None = None
    in_randomizer: bool = False
    free_text: bool = False
    columns: list[str] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)
    rows: list[Choice] = field(default_factory=list)   # Matrix statements
    sub_selector: str | None = None

    @property
    def is_display_only(self) -> bool:
        return self.type in ("DB", "Timing")


@dataclass
class Arms:
    column: str | None = None
    values: list[str] = field(default_factory=list)
    stimuli: dict[str, str] = field(default_factory=dict)
    source: str = "none"          # flow_embedded | inferred | pap | convention | none
    blocks: dict[str, list[str]] = field(default_factory=dict)   # arm -> [qids in that block]


@dataclass
class Codebook:
    survey: dict[str, Any]
    questions: list[Question]
    arms: Arms
    embedded_data: list[str] = field(default_factory=list)
    columns: list[dict[str, Any]] = field(default_factory=list)   # filled by tidy.py

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Codebook":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        qs = []
        for q in d["questions"]:
            q = dict(q)
            q["choices"] = [Choice(**c) for c in q.get("choices", [])]
            q["rows"] = [Choice(**c) for c in q.get("rows", [])]
            qs.append(Question(**q))
        return cls(survey=d["survey"], questions=qs, arms=Arms(**d["arms"]),
                   embedded_data=d.get("embedded_data", []), columns=d.get("columns", []))

    def question_by_tag(self, tag: str) -> Question | None:
        for q in self.questions:
            if q.tag == tag:
                return q
        return None

    def column_question(self, col: str) -> Question | None:
        for q in self.questions:
            if col in q.columns:
                return q
        return None

    def free_text_columns(self) -> list[str]:
        cols = []
        for q in self.questions:
            if q.type == "TE":
                cols += q.columns
            for c in q.choices:
                if c.text_entry:
                    cols.append(f"{q.tag}_{c.id}_TEXT")
        return cols

    def closed_columns(self) -> list[str]:
        """Export columns of closed-ended questions (choices or matrix rows); never their _TEXT entries."""
        cols = []
        for q in self.questions:
            if q.type != "TE" and (q.choices or q.rows):
                cols += [c for c in q.columns if not c.endswith("_TEXT")]
        return cols

    def to_markdown(self) -> str:
        lines = [f"# Codebook: {self.survey.get('name', '')}", "",
                 f"Source: {self.survey.get('source', 'qsf')}", ""]
        if self.arms.column:
            lines += [f"**Arms** (`{self.arms.column}`, source: {self.arms.source}): "
                      + ", ".join(self.arms.values), ""]
        lines += ["| column | qid | type | label | values |", "|---|---|---|---|---|"]
        rows = self.columns or [
            {"name": c, "qid": q.qid, "kind": "question", "label": q.text[:80],
             "values": {ch.recode or ch.id: ch.label for ch in q.choices}}
            for q in self.questions for c in q.columns]
        for c in rows:
            vals = c.get("values") or {}
            vs = "; ".join(f"{k}={v}" for k, v in list(vals.items())[:8])
            if len(vals) > 8:
                vs += "; …"
            lines.append(f"| `{c['name']}` | {c.get('qid','')} | {c.get('kind','')} | "
                         f"{str(c.get('label',''))[:80].replace('|','/')} | {vs.replace('|','/')} |")
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------------

def _choices_from_payload(p: dict) -> list[Choice]:
    choices = p.get("Choices") or {}
    recodes = p.get("RecodeValues") or {}
    order = p.get("ChoiceOrder") or list(choices.keys())
    out = []
    for cid in order:
        cid = str(cid)
        c = choices.get(cid) or choices.get(int(cid)) if isinstance(choices, dict) else None
        if c is None:
            continue
        label = strip_html(c.get("Display", "")) if isinstance(c, dict) else strip_html(str(c))
        text_entry = bool(isinstance(c, dict) and c.get("TextEntry"))
        out.append(Choice(id=cid, label=label, recode=str(recodes.get(cid)) if cid in recodes else None,
                          text_entry=text_entry))
    return out


def _answers_from_payload(p: dict) -> list[Choice]:
    answers = p.get("Answers") or {}
    recodes = p.get("RecodeValues") or {}
    order = p.get("AnswerOrder") or list(answers.keys())
    out = []
    for aid in order:
        aid = str(aid)
        a = answers.get(aid)
        if a is None:
            continue
        label = strip_html(a.get("Display", "")) if isinstance(a, dict) else strip_html(str(a))
        out.append(Choice(id=aid, label=label, recode=str(recodes.get(aid)) if aid in recodes else None))
    return out


def _derive_columns(q: Question) -> list[str]:
    t, sel, tag = q.type, q.selector, q.tag
    if t in ("DB",):
        return []
    if t == "Timing":
        return [f"{tag}_First Click", f"{tag}_Last Click", f"{tag}_Page Submit", f"{tag}_Click Count"]
    if t == "TE":
        if sel == "FORM":
            return [f"{tag}_{c.id}" for c in q.choices] or [tag]
        return [tag]
    if t == "MC":
        if sel in ("MAVR", "MAHR", "MACOL", "MSB"):
            cols = [f"{tag}_{c.id}" for c in q.choices]
        else:
            cols = [tag]
        cols += [f"{tag}_{c.id}_TEXT" for c in q.choices if c.text_entry]
        return cols
    if t == "Matrix":
        if q.sub_selector in ("MultipleAnswer",):
            return [f"{tag}_{r.id}_{c.id}" for r in q.rows for c in q.choices]
        return [f"{tag}_{r.id}" for r in q.rows] or [tag]
    if t == "Slider":
        return [f"{tag}_{c.id}" for c in q.choices] or [tag]
    return [tag]


def _walk_flow(flow: list, randomizer_depth: int, out: dict) -> None:
    """Collect block ids under randomizers, embedded-data fields, and arm embedded data."""
    for el in flow or []:
        t = el.get("Type")
        if t == "Block" or t == "Standard":
            bid = el.get("ID")
            if bid:
                out["block_order"].append(bid)
                if randomizer_depth > 0:
                    out["randomized_blocks"].append(bid)
                    out["arm_blocks"].append({"block": bid, "embedded": dict(out["_pending_ed"])})
        elif t == "BlockRandomizer":
            out["randomizers"].append({"subset": el.get("SubSet"), "even": el.get("EvenPresentation"),
                                       "n_children": len(el.get("Flow") or [])})
            for child in el.get("Flow") or []:
                out["_pending_ed"] = {}
                ctype = child.get("Type")
                if ctype == "EmbeddedData":
                    for ed in child.get("EmbeddedData") or []:
                        fld, val = ed.get("Field"), ed.get("Value")
                        if fld:
                            out["embedded_fields"].add(fld)
                            if val is not None:
                                out["arm_embedded"].append({"field": fld, "value": str(val)})
                    continue
                if ctype == "Group":
                    # group = embedded data + block(s) together
                    for sub in child.get("Flow") or []:
                        if sub.get("Type") == "EmbeddedData":
                            for ed in sub.get("EmbeddedData") or []:
                                fld, val = ed.get("Field"), ed.get("Value")
                                if fld:
                                    out["embedded_fields"].add(fld)
                                    if val is not None:
                                        out["_pending_ed"][fld] = str(val)
                                        out["arm_embedded"].append({"field": fld, "value": str(val)})
                    _walk_flow(child.get("Flow"), randomizer_depth + 1, out)
                    out["_pending_ed"] = {}
                    continue
                _walk_flow([child], randomizer_depth + 1, out)
            out["_pending_ed"] = {}
        elif t == "EmbeddedData":
            for ed in el.get("EmbeddedData") or []:
                if ed.get("Field"):
                    out["embedded_fields"].add(ed["Field"])
        elif t == "Branch":
            out["branches"].append(json.dumps(el.get("BranchLogic", {}))[:500])
            _walk_flow(el.get("Flow"), randomizer_depth, out)
        elif t == "Group":
            _walk_flow(el.get("Flow"), randomizer_depth, out)


def parse_qsf(path: str | Path) -> Codebook:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = raw.get("SurveyEntry", {})
    elements = raw.get("SurveyElements", [])

    questions: dict[str, Question] = {}
    blocks: dict[str, dict] = {}
    flow_info: dict[str, Any] = {"block_order": [], "randomized_blocks": [], "arm_blocks": [],
                                 "randomizers": [], "embedded_fields": set(), "arm_embedded": [],
                                 "branches": [], "_pending_ed": {}}

    for el in elements:
        et = el.get("Element")
        p = el.get("Payload")
        if et == "SQ" and isinstance(p, dict):
            qid = p.get("QuestionID") or el.get("PrimaryAttribute")
            qtype = p.get("QuestionType", "")
            q = Question(
                qid=qid, tag=p.get("DataExportTag") or qid, type=qtype,
                selector=p.get("Selector", ""), sub_selector=p.get("SubSelector"),
                text=strip_html(p.get("QuestionText", "")),
                free_text=(qtype == "TE"),
            )
            if qtype == "Matrix":
                q.rows = _choices_from_payload(p)       # statements
                q.choices = _answers_from_payload(p)    # scale points
            else:
                q.choices = _choices_from_payload(p)
            q.columns = _derive_columns(q)
            questions[qid] = q
        elif et == "BL":
            items = p.values() if isinstance(p, dict) else (p or [])
            for b in items:
                if not isinstance(b, dict):
                    continue
                bid = b.get("ID")
                blocks[bid] = {"description": b.get("Description", ""),
                               "qids": [e.get("QuestionID") for e in b.get("BlockElements", [])
                                        if e.get("Type") == "Question"]}
        elif et == "FL" and isinstance(p, dict):
            _walk_flow(p.get("Flow", []), 0, flow_info)

    for bid, b in blocks.items():
        for qid in b["qids"]:
            if qid in questions:
                questions[qid].block = b["description"] or bid
                if bid in flow_info["randomized_blocks"]:
                    questions[qid].in_randomizer = True

    # Arms: prefer embedded data set inside the randomizer
    arms = Arms()
    arm_ed = flow_info["arm_embedded"]
    if arm_ed:
        fields = {e["field"] for e in arm_ed}
        # pick the field with most distinct values
        best = max(fields, key=lambda f: len({e["value"] for e in arm_ed if e["field"] == f}))
        arms.column = best
        arms.values = sorted({e["value"] for e in arm_ed if e["field"] == best})
        arms.source = "flow_embedded"
        for ab in flow_info["arm_blocks"]:
            v = ab["embedded"].get(best)
            if v is not None:
                arms.blocks[v] = blocks.get(ab["block"], {}).get("qids", [])
    elif flow_info["randomized_blocks"]:
        arms.source = "inferred"
        for bid in flow_info["randomized_blocks"]:
            desc = blocks.get(bid, {}).get("description") or bid
            arms.values.append(desc)
            arms.blocks[desc] = blocks.get(bid, {}).get("qids", [])
        arms.column = "arm"

    # stimuli: descriptive-text (DB) question in each arm block
    for arm, qids in arms.blocks.items():
        for qid in qids:
            q = questions.get(qid)
            if q and q.type == "DB" and q.text:
                arms.stimuli[arm] = q.text
                break

    cb = Codebook(
        survey={"name": entry.get("SurveyName", ""), "id": entry.get("SurveyID", ""),
                "source": "qsf", "n_blocks": len(blocks),
                "randomizers": flow_info["randomizers"], "branches": flow_info["branches"]},
        questions=[questions[k] for k in questions],
        arms=arms,
        embedded_data=sorted(flow_info["embedded_fields"]),
    )
    return cb
