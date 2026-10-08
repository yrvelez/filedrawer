# Qualtrics QSF Encoding Reference

Authoritative encoding patterns extracted from real Qualtrics exports. Every JSON shape in this file has been validated against successful imports.

## Table of Contents

1. [Top-Level QSF Structure](#1-top-level-qsf-structure)
2. [SurveyEntry Object](#2-surveyentry-object)
3. [Required SurveyElements](#3-required-surveyelements)
4. [Question Types — Exact JSON Shapes](#4-question-types)
   - [MC/SAVR — Single Select](#mcsavr--single-select)
   - [MC/MAVR — Multi Select](#mcmavr--multi-select)
   - [MC/NPS — Net Promoter Score](#mcnps--net-promoter-score)
   - [MC/DL — Dropdown](#mcdl--dropdown)
   - [TE/SL — Text Entry Single Line](#tesl--text-entry-single-line)
   - [TE/ML — Text Entry Multi Line](#teml--text-entry-multi-line)
   - [Matrix/Likert — Likert Grid](#matrixlikert--likert-grid)
   - [RO/DND — Rank Order Drag and Drop](#rodnd--rank-order)
   - [DB/TB — Descriptive Text Block](#dbtb--descriptive-text-block)
5. [Block Encoding](#5-block-encoding)
6. [Survey Flow](#6-survey-flow)
7. [Display Logic](#7-display-logic)
8. [Skip Logic](#8-skip-logic)
9. [Validation Patterns](#9-validation-patterns)
10. [Common Mistakes](#10-common-mistakes)

---

## 1. Top-Level QSF Structure

```json
{
  "SurveyEntry": { ... },
  "SurveyElements": [ ... ]
}
```

That's it. Two keys. `SurveyEntry` is metadata, `SurveyElements` is an array of typed element objects.

---

## 2. SurveyEntry Object

```json
{
  "SurveyID": "SV_XXXXXXXXXXXXXXX",
  "SurveyName": "My Survey Name",
  "SurveyDescription": "Description text or null",
  "SurveyOwnerID": "UR_XXXXXXXXXXXXXXX",
  "SurveyBrandID": "firstcaribbean",
  "DivisionID": null,
  "SurveyLanguage": "EN",
  "SurveyActiveResponseSet": "RS_XXXXXXXXXXXXXXX",
  "SurveyStatus": "Inactive",
  "SurveyStartDate": "0000-00-00 00:00:00",
  "SurveyExpirationDate": "0000-00-00 00:00:00",
  "SurveyCreationDate": "2026-01-01 00:00:00",
  "CreatorID": "UR_XXXXXXXXXXXXXXX",
  "LastModified": "2026-01-01 00:00:00",
  "LastAccessed": "0000-00-00 00:00:00",
  "LastActivated": "0000-00-00 00:00:00",
  "Deleted": null
}
```

**Notes:**
- `SurveyID`, `SurveyActiveResponseSet`, and `SurveyOwnerID` all get reassigned on import — placeholder values are fine
- `SurveyBrandID` should match the user's Qualtrics brand if known
- Use `"Inactive"` for status — never import as active

---

## 3. Required SurveyElements

Every QSF must contain these element types. Missing any will cause import failure or broken rendering.

### BL — Survey Blocks
```json
{
  "SurveyID": "SV_XXX",
  "Element": "BL",
  "PrimaryAttribute": "Survey Blocks",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": [ ...array of block objects... ]
}
```

### FL — Survey Flow
```json
{
  "SurveyID": "SV_XXX",
  "Element": "FL",
  "PrimaryAttribute": "Survey Flow",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": { ...flow object... }
}
```

### PL — Preview Link
```json
{
  "SurveyID": "SV_XXX",
  "Element": "PL",
  "PrimaryAttribute": "Preview Link",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": {
    "PreviewType": "Brand",
    "PreviewID": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
  }
}
```

### PROJ — Project
```json
{
  "SurveyID": "SV_XXX",
  "Element": "PROJ",
  "PrimaryAttribute": "CORE",
  "SecondaryAttribute": null,
  "TertiaryAttribute": "1.1.0",
  "Payload": {
    "ProjectCategory": "CORE",
    "SchemaVersion": "1.1.0"
  }
}
```

### QC — Question Count
```json
{
  "SurveyID": "SV_XXX",
  "Element": "QC",
  "PrimaryAttribute": "Survey Question Count",
  "SecondaryAttribute": "20",
  "TertiaryAttribute": null,
  "Payload": null
}
```
Note: `Payload` is `null`. Count goes in `SecondaryAttribute` as a string.

### RS — Response Set
```json
{
  "SurveyID": "SV_XXX",
  "Element": "RS",
  "PrimaryAttribute": "RS_XXXXXXXXXXXXXXX",
  "SecondaryAttribute": "Default Response Set",
  "TertiaryAttribute": null,
  "Payload": null
}
```

### SCO — Scoring
```json
{
  "SurveyID": "SV_XXX",
  "Element": "SCO",
  "PrimaryAttribute": "Scoring",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": {
    "ScoringCategories": [],
    "ScoringCategoryGroups": [],
    "ScoringSummaryCategory": null,
    "ScoringSummaryAfterQuestions": 0,
    "ScoringSummaryAfterSurvey": 0,
    "DefaultScoringCategory": null,
    "AutoScoringCategory": null
  }
}
```

### SO — Survey Options
```json
{
  "SurveyID": "SV_XXX",
  "Element": "SO",
  "PrimaryAttribute": "Survey Options",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": {
    "BackButton": "false",
    "SaveAndContinue": "true",
    "SurveyProtection": "PublicSurvey",
    "BallotBoxStuffingPrevention": "false",
    "NoIndex": "Yes",
    "SecureResponseFiles": "true",
    "SurveyExpiration": "None",
    "SurveyTermination": "DefaultMessage",
    "Header": "",
    "Footer": "",
    "ProgressBarDisplay": "None",
    "PartialData": "+1 week",
    "ValidationMessage": null,
    "PreviousButton": "",
    "NextButton": "",
    "SurveyTitle": "Survey Title Here",
    "SkinLibrary": "firstcaribbean",
    "SkinType": "templated",
    "Skin": {
      "brandingId": null,
      "templateId": "*base",
      "overrides": null
    },
    "NewScoring": 1,
    "SurveyMetaDescription": "Short description."
  }
}
```

**Notes on SO:**
- `SkinLibrary` should match the brand (`"firstcaribbean"` or `"Qualtrics"`)
- Qualtrics adds many additional fields on export (like `EOSMessage`, `CollectGeoLocation`, `AvailableLanguages`) — these are optional for import
- Brand overrides (logo URL, colors) can be included in `Skin.overrides` if known

### STAT — Survey Statistics
```json
{
  "SurveyID": "SV_XXX",
  "Element": "STAT",
  "PrimaryAttribute": "Survey Statistics",
  "SecondaryAttribute": null,
  "TertiaryAttribute": null,
  "Payload": {
    "MobileCompatible": true,
    "ID": "Survey Statistics"
  }
}
```

### SQ — Survey Questions
One per question. See Section 4 for payloads.
```json
{
  "SurveyID": "SV_XXX",
  "Element": "SQ",
  "PrimaryAttribute": "QID1",
  "SecondaryAttribute": "First 80 chars of question text...",
  "TertiaryAttribute": null,
  "Payload": { ...question payload... }
}
```

### Elements Qualtrics Auto-Generates (optional for import)
- `QG` — Quota Group (auto-created on import)
- `QGO` — Quota Group Order (auto-created on import)
- `QO` — Quota (auto-created on import)

You can omit these — Qualtrics creates them automatically.

---

## 4. Question Types

### MC/SAVR — Single Select

The workhorse. Used for almost all single-choice questions.

```json
{
  "QuestionText": "How appealing was the offer?",
  "DataExportTag": "Q4",
  "QuestionType": "MC",
  "Selector": "SAVR",
  "SubSelector": "TX",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "How appealing was the offer?",
  "Choices": {
    "1": {"Display": "Very appealing"},
    "2": {"Display": "Somewhat appealing"},
    "3": {"Display": "Neutral"},
    "4": {"Display": "Somewhat unappealing"},
    "5": {"Display": "Not at all appealing"}
  },
  "ChoiceOrder": ["1", "2", "3", "4", "5"],
  "Validation": {"Settings": {"Type": "None"}},
  "Language": [],
  "NextChoiceId": 6,
  "NextAnswerId": 1,
  "QuestionID": "QID6"
}
```

**With "Other (please specify)":**
```json
"10": {
  "Display": "Other (please specify)",
  "TextEntry": "true",
  "TextEntrySize": "Small"
}
```

**With ForceResponse ON:**
```json
"Validation": {
  "Settings": {
    "ForceResponse": "ON",
    "Type": "None"
  }
}
```

**With ForceResponse OFF (preferred — just omit the key):**
```json
"Validation": {"Settings": {"Type": "None"}}
```

---

### MC/MAVR — Multi Select

Identical to SAVR except `Selector` is `"MAVR"`.

```json
{
  "QuestionText": "Select all that apply.",
  "DataExportTag": "Q7",
  "QuestionType": "MC",
  "Selector": "MAVR",
  "SubSelector": "TX",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "Select all that apply.",
  "Choices": {
    "1": {"Display": "Option A"},
    "2": {"Display": "Option B"},
    "3": {"Display": "Option C"}
  },
  "ChoiceOrder": ["1", "2", "3"],
  "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
  "Language": [],
  "NextChoiceId": 4,
  "NextAnswerId": 1,
  "QuestionID": "QID9"
}
```

---

### MC/NPS — Net Promoter Score

**Critical difference: `Choices` is an ARRAY, not a dict.** Also requires `ColumnLabels`.

```json
{
  "QuestionText": "How likely are you to recommend us?<br><br><em>0 = Not at all likely &nbsp; | &nbsp; 10 = Extremely likely</em>",
  "DataExportTag": "Q16",
  "QuestionType": "MC",
  "Selector": "NPS",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "How likely are you to recommend us?...",
  "Choices": [
    {"Display": "0"},
    {"Display": "1"},
    {"Display": "2"},
    {"Display": "3"},
    {"Display": "4"},
    {"Display": "5"},
    {"Display": "6"},
    {"Display": "7"},
    {"Display": "8"},
    {"Display": "9"},
    {"Display": "10"}
  ],
  "ChoiceOrder": ["0","1","2","3","4","5","6","7","8","9","10"],
  "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
  "Language": [],
  "NextChoiceId": 11,
  "NextAnswerId": 1,
  "QuestionID": "QID24",
  "DefaultChoices": false,
  "GradingData": [],
  "ColumnLabels": [
    {"Display": "Not at all likely", "IsLabelDefault": true},
    {"Display": "Extremely likely", "IsLabelDefault": true}
  ]
}
```

**Key differences from standard MC:**
- No `SubSelector`
- `Choices` is an array of `{Display}` objects (not a keyed dict)
- `ChoiceOrder` is zero-indexed: `["0","1",...,"10"]`
- Requires `ColumnLabels` array with exactly 2 items (endpoints)
- Requires `DefaultChoices: false` and `GradingData: []`

---

### MC/DL — Dropdown

Same as SAVR but with `Selector: "DL"`. Good for long option lists (countries, etc).

```json
{
  "QuestionType": "MC",
  "Selector": "DL",
  "SubSelector": "TX",
  ...rest identical to SAVR...
}
```

---

### TE/SL — Text Entry Single Line

```json
{
  "QuestionText": "Please share any comments.",
  "DataExportTag": "Q15",
  "QuestionType": "TE",
  "Selector": "SL",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "Please share any comments.",
  "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
  "Language": [],
  "NextChoiceId": 1,
  "NextAnswerId": 1,
  "QuestionID": "QID23",
  "SearchSource": {"AllowFreeResponse": "false"}
}
```

**Key fields:**
- No `Choices`, `ChoiceOrder`, or `SubSelector`
- Include `SearchSource: {"AllowFreeResponse": "false"}`

**For email validation:**
```json
"Validation": {
  "Settings": {
    "ForceResponse": "ON",
    "Type": "ContentType",
    "ContentType": "ValidEmail"
  }
}
```

---

### TE/ML — Text Entry Multi Line

Same as TE/SL but with `Selector: "ML"`. No `SearchSource` needed.

```json
{
  "QuestionType": "TE",
  "Selector": "ML",
  ...rest same as SL minus SearchSource...
}
```

---

### Matrix/Likert — Likert Grid

`Choices` = rows (items being rated), `Answers` = columns (scale points).

```json
{
  "QuestionText": "How likely would you be to accept if the following were different?",
  "DefaultChoices": false,
  "DataExportTag": "Q10",
  "QuestionID": "QID27",
  "QuestionType": "Matrix",
  "Selector": "Likert",
  "SubSelector": "SingleAnswer",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {
    "QuestionDescriptionOption": "UseText",
    "TextPosition": "inline",
    "ChoiceColumnWidth": 25,
    "RepeatHeaders": "none",
    "WhiteSpace": "OFF",
    "MobileFirst": true,
    "Autoscale": {
      "XScale": {
        "Name": "likelyUnlikely",
        "Type": "likert",
        "Reverse": false
      }
    }
  },
  "QuestionDescription": "How likely would you be to accept if the following were different?...",
  "Choices": {
    "1": {"Display": "Lower interest rate"},
    "2": {"Display": "Higher loan amount"},
    "3": {"Display": "Longer repayment period"}
  },
  "ChoiceOrder": ["1", "2", "3"],
  "Answers": {
    "1": {"Display": "Extremely unlikely"},
    "2": {"Display": "Somewhat unlikely"},
    "3": {"Display": "Neither likely nor unlikely"},
    "4": {"Display": "Somewhat likely"},
    "5": {"Display": "Extremely likely"}
  },
  "AnswerOrder": ["1", "2", "3", "4", "5"],
  "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
  "GradingData": [],
  "Language": [],
  "NextChoiceId": 4,
  "NextAnswerId": 6,
  "ChoiceDataExportTags": false
}
```

**Key fields:**
- `SubSelector` is `"SingleAnswer"` (not `"TX"`)
- `Configuration` requires `TextPosition`, `ChoiceColumnWidth`, `MobileFirst`, `RepeatHeaders`, `WhiteSpace`, and `Autoscale`
- Common `Autoscale.XScale.Name` values: `"likelyUnlikely"`, `"agreement"`, `"satisfaction"`, `"frequency"`, `"quality"`, `"importance"`
- `Answers` + `AnswerOrder` define the column headers (scale points)
- `ChoiceDataExportTags: false`
- `DefaultChoices: false` and `GradingData: []`

---

### RO/DND — Rank Order

```json
{
  "QuestionText": "Rank your top 3 preferences.",
  "DefaultChoices": false,
  "DataExportTag": "Q13",
  "QuestionID": "QID21",
  "QuestionType": "RO",
  "Selector": "DND",
  "SubSelector": "TX",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "Rank your top 3 preferences.",
  "Choices": {
    "1": {"Display": "Email"},
    "2": {"Display": "SMS"},
    "3": {"Display": "WhatsApp"}
  },
  "ChoiceOrder": ["1", "2", "3"],
  "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
  "GradingData": [],
  "Language": [],
  "NextChoiceId": 4,
  "NextAnswerId": 1
}
```

**Note:** To limit how many items can be ranked, configure within Qualtrics after import. The QSF encoding doesn't natively carry `NumChoices` in a way that reliably imports.

---

### DB/TB — Descriptive Text Block

For intro text, instructions, consent notices — anything non-interactive.

```json
{
  "QuestionText": "Welcome to this survey. Please read carefully before proceeding.",
  "DefaultChoices": false,
  "DataExportTag": "Q25",
  "QuestionID": "QID25",
  "QuestionType": "DB",
  "Selector": "TB",
  "DataVisibility": {"Private": false, "Hidden": false},
  "Configuration": {"QuestionDescriptionOption": "UseText"},
  "QuestionDescription": "Welcome to this survey. Please read carefully before proceeding.",
  "ChoiceOrder": [],
  "Validation": {"Settings": {"Type": "None"}},
  "GradingData": [],
  "Language": [],
  "NextChoiceId": 4,
  "NextAnswerId": 1
}
```

**Key:** No `Choices` dict, no `SubSelector`. `ChoiceOrder` is empty array. HTML is allowed in `QuestionText`.

---

## 5. Block Encoding

```json
[
  {
    "Type": "Default",
    "Description": "Block 1 — Screening",
    "ID": "BL_xxxxxxxxxxxxx",
    "BlockElements": [
      {"Type": "Question", "QuestionID": "QID1"},
      {"Type": "Page Break"},
      {"Type": "Question", "QuestionID": "QID2"}
    ]
  },
  {
    "Type": "Standard",
    "Description": "Block 2 — Demographics",
    "ID": "BL_demographics",
    "BlockElements": [
      {"Type": "Question", "QuestionID": "QID3"},
      {"Type": "Question", "QuestionID": "QID4"}
    ]
  },
  {
    "Type": "Trash",
    "Description": "Trash / Unused Questions",
    "ID": "BL_trash01"
  }
]
```

**Rules:**
- First block must be `"Type": "Default"` — this is the entry block
- All other content blocks use `"Type": "Standard"`
- Always include a `"Type": "Trash"` block at the end (even if empty, no `BlockElements` key)
- Page breaks between questions: `{"Type": "Page Break"}`
- Block IDs can be any string but must be unique

### Skip Logic in Blocks

Skip logic lives on the `BlockElements` entry, not in the question payload:
```json
{
  "Type": "Question",
  "QuestionID": "QID1",
  "SkipLogic": [
    {
      "SkipLogicID": 1,
      "ChoiceLocator": "q://QID1/SelectableChoice/2",
      "Condition": "Selected",
      "SkipToDestination": "ENDOFSURVEY",
      "Locator": "q://QID1/SelectableChoice/2",
      "SkipToDescription": "Skip description text",
      "Description": "Condition: <strong>Choice 2</strong> <strong>Is Selected</strong>. Skip To: <strong>End of Survey</strong>.",
      "QuestionID": "QID1"
    }
  ]
}
```

`SkipToDestination` values: `"ENDOFSURVEY"`, `"ENDOFBLOCK"`, or a `QID`.

---

## 6. Survey Flow

```json
{
  "Flow": [
    {"ID": "BL_xxxxxxxxxxxxx", "Type": "Block", "FlowID": "FL_2"},
    {"ID": "BL_demographics", "Type": "Block", "FlowID": "FL_3"},
    {"ID": "BL_closing", "Type": "Block", "FlowID": "FL_4"}
  ],
  "Properties": {"Count": 4},
  "FlowID": "FL_1",
  "Type": "Root"
}
```

**Rules:**
- `Properties.Count` = number of flow items + 1
- `FlowID` starts at `"FL_2"` and increments (root is `"FL_1"`)
- Only list non-Trash blocks
- Order matches the intended survey progression

---

## 7. Display Logic

Goes inside the question payload. Controls whether a question is shown based on previous answers.

```json
"DisplayLogic": {
  "0": {
    "0": {
      "LogicType": "Question",
      "QuestionID": "QID2",
      "QuestionIsInLoop": "no",
      "ChoiceLocator": "q://QID2/SelectableChoice/1",
      "Operator": "Selected",
      "QuestionIDFromLocator": "QID2",
      "LeftOperand": "q://QID2/SelectableChoice/1",
      "Type": "Expression",
      "Description": "<span class=\"ConjDesc\">If</span> <span class=\"QuestionDesc\">Question text</span> <span class=\"LeftOpDesc\">Yes</span> <span class=\"OpDesc\">Is Selected</span> "
    },
    "Type": "If"
  },
  "Type": "BooleanExpression",
  "inPage": false
}
```

**Locator format:** `q://QID{n}/SelectableChoice/{choice_number}`

---

## 8. Skip Logic

See Section 5 (Block Encoding) — skip logic is defined on `BlockElements`, not in the question payload.

---

## 9. Validation Patterns

**No forced response (preferred encoding when OFF):**
```json
"Validation": {"Settings": {"Type": "None"}}
```

**Forced response ON:**
```json
"Validation": {"Settings": {"ForceResponse": "ON", "Type": "None"}}
```

**Email validation:**
```json
"Validation": {"Settings": {"ForceResponse": "ON", "Type": "ContentType", "ContentType": "ValidEmail"}}
```

**Minimum choices (multi-select):**
```json
"Validation": {"Settings": {"ForceResponse": "OFF", "ForceResponseType": "ON", "Type": "MinChoices", "MinChoices": "1"}}
```

Note: `ForceResponseType` is valid but only used alongside advanced validation types like `MinChoices`. Never include it with basic `"Type": "None"`.

---

## 10. Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| `ChoiceOrder` with integers `[1,2,3]` | May work but inconsistent with Qualtrics convention | Always use strings `["1","2","3"]` |
| Using `Type` instead of `QuestionType` | Question won't render | Always use `QuestionType` |
| Missing `DataVisibility` | Import may fail | Always include `{"Private": false, "Hidden": false}` |
| Missing `Configuration` | Import may fail | Always include `{"QuestionDescriptionOption": "UseText"}` |
| NPS with dict Choices | Broken rendering | NPS uses an array: `[{"Display":"0"}, ...]` |
| NPS without ColumnLabels | Missing endpoint labels | Include 2-item array with `IsLabelDefault: true` |
| Matrix without Autoscale | May import but renders poorly | Include full Configuration block |
| Full question text in QuestionDescription | Works but doesn't match Qualtrics convention | Truncate to ~100 chars + `...` |
| `ForceResponseType` with `Type: "None"` | Invalid field combo | Only use with advanced validation types |
| Missing Trash block | Import may fail | Always include even if empty |
| Omitting `SearchSource` on TE/SL | Works but doesn't match native encoding | Include `{"AllowFreeResponse": "false"}` |
| Hard-coding SurveyID/RS IDs | Wastes effort | Qualtrics reassigns all IDs on import — use any placeholder |
| Missing `STAT` element | Import may fail | Always include with `MobileCompatible: true` |

---

## Python Builder Template

Use this as the starting skeleton for any QSF generation script:

```python
import json
import uuid

SURVEY_ID = "SV_placeholder"
OWNER_ID = "UR_placeholder"
BRAND_ID = "firstcaribbean"  # or use user's brand
RS_ID = "RS_placeholder"

def truncate_desc(text, max_len=100):
    """Truncate QuestionDescription to match Qualtrics convention."""
    clean = text.replace("<strong>", "").replace("</strong>", "")
    clean = clean.replace("<em>", "").replace("</em>", "")
    clean = clean.replace("<br>", " ").replace("<br/>", " ")
    clean = clean.replace("&nbsp;", " ").replace("&amp;", "&")
    if len(clean) > max_len:
        return clean[:max_len - 3] + "..."
    return clean

def mc_single(qid, tag, text, choices, force=False, has_other=False):
    choice_dict = {}
    for i, c in enumerate(choices, 1):
        choice_dict[str(i)] = {"Display": c}
    if has_other:
        last = str(len(choices))
        choice_dict[last]["TextEntry"] = "true"
        choice_dict[last]["TextEntrySize"] = "Small"
    val = {"Settings": {"ForceResponse": "ON", "Type": "None"}} if force else {"Settings": {"Type": "None"}}
    return {
        "QuestionText": text,
        "DataExportTag": tag,
        "QuestionType": "MC",
        "Selector": "SAVR",
        "SubSelector": "TX",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionDescription": truncate_desc(text),
        "Choices": choice_dict,
        "ChoiceOrder": [str(i) for i in range(1, len(choices) + 1)],
        "Validation": val,
        "Language": [],
        "NextChoiceId": len(choices) + 1,
        "NextAnswerId": 1,
        "QuestionID": qid
    }

def mc_multi(qid, tag, text, choices):
    q = mc_single(qid, tag, text, choices)
    q["Selector"] = "MAVR"
    return q

def mc_nps(qid, tag, text):
    return {
        "QuestionText": text,
        "DataExportTag": tag,
        "QuestionType": "MC",
        "Selector": "NPS",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionDescription": truncate_desc(text),
        "Choices": [{"Display": str(i)} for i in range(11)],
        "ChoiceOrder": [str(i) for i in range(11)],
        "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
        "Language": [],
        "NextChoiceId": 11,
        "NextAnswerId": 1,
        "QuestionID": qid,
        "DefaultChoices": False,
        "GradingData": [],
        "ColumnLabels": [
            {"Display": "Not at all likely", "IsLabelDefault": True},
            {"Display": "Extremely likely", "IsLabelDefault": True}
        ]
    }

def te_single(qid, tag, text, force=False):
    val = {"Settings": {"ForceResponse": "ON", "Type": "None"}} if force else {"Settings": {"Type": "None"}}
    return {
        "QuestionText": text,
        "DataExportTag": tag,
        "QuestionType": "TE",
        "Selector": "SL",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionDescription": truncate_desc(text),
        "Validation": val,
        "Language": [],
        "NextChoiceId": 1,
        "NextAnswerId": 1,
        "QuestionID": qid,
        "SearchSource": {"AllowFreeResponse": "false"}
    }

def matrix_likert(qid, tag, text, rows, cols, scale_name="likelyUnlikely"):
    choice_dict = {str(i): {"Display": r} for i, r in enumerate(rows, 1)}
    answer_dict = {str(i): {"Display": c} for i, c in enumerate(cols, 1)}
    return {
        "QuestionText": text,
        "DefaultChoices": False,
        "DataExportTag": tag,
        "QuestionID": qid,
        "QuestionType": "Matrix",
        "Selector": "Likert",
        "SubSelector": "SingleAnswer",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {
            "QuestionDescriptionOption": "UseText",
            "TextPosition": "inline",
            "ChoiceColumnWidth": 25,
            "RepeatHeaders": "none",
            "WhiteSpace": "OFF",
            "MobileFirst": True,
            "Autoscale": {
                "XScale": {
                    "Name": scale_name,
                    "Type": "likert",
                    "Reverse": False
                }
            }
        },
        "QuestionDescription": truncate_desc(text),
        "Choices": choice_dict,
        "ChoiceOrder": [str(i) for i in range(1, len(rows) + 1)],
        "Answers": answer_dict,
        "AnswerOrder": [str(i) for i in range(1, len(cols) + 1)],
        "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
        "GradingData": [],
        "Language": [],
        "NextChoiceId": len(rows) + 1,
        "NextAnswerId": len(cols) + 1,
        "ChoiceDataExportTags": False
    }

def rank_order(qid, tag, text, choices):
    choice_dict = {str(i): {"Display": c} for i, c in enumerate(choices, 1)}
    return {
        "QuestionText": text,
        "DefaultChoices": False,
        "DataExportTag": tag,
        "QuestionID": qid,
        "QuestionType": "RO",
        "Selector": "DND",
        "SubSelector": "TX",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionDescription": truncate_desc(text),
        "Choices": choice_dict,
        "ChoiceOrder": [str(i) for i in range(1, len(choices) + 1)],
        "Validation": {"Settings": {"ForceResponse": "OFF", "Type": "None"}},
        "GradingData": [],
        "Language": [],
        "NextChoiceId": len(choices) + 1,
        "NextAnswerId": 1
    }

def desc_block(qid, tag, text):
    return {
        "QuestionText": text,
        "DefaultChoices": False,
        "DataExportTag": tag,
        "QuestionID": qid,
        "QuestionType": "DB",
        "Selector": "TB",
        "DataVisibility": {"Private": False, "Hidden": False},
        "Configuration": {"QuestionDescriptionOption": "UseText"},
        "QuestionDescription": truncate_desc(text),
        "ChoiceOrder": [],
        "Validation": {"Settings": {"Type": "None"}},
        "GradingData": [],
        "Language": [],
        "NextChoiceId": 4,
        "NextAnswerId": 1
    }

# ... define questions, blocks, flow, assemble, write to file ...
```
