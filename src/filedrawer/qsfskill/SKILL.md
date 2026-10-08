---
name: qsf-generator
description: Generate Qualtrics Survey Format (.qsf) files that import cleanly into Qualtrics without errors. Use this skill whenever the user wants to create a Qualtrics survey, build a QSF file, encode survey questions for Qualtrics, convert a survey spec/questionnaire into an importable file, or mentions .qsf, Qualtrics, or survey import. Also trigger when the user provides survey questions and wants them turned into an uploadable format, or asks to fix/rebuild a broken QSF. This skill covers all supported question types including MC (single/multi/NPS), TE, Matrix, RO, DB, and Slider — with the exact field encoding Qualtrics expects.
---

# QSF Generator

Generate `.qsf` files that Qualtrics accepts on first upload. This skill encodes the exact JSON structure Qualtrics expects — learned from real exports, not documentation.

## When to Use

- User wants to create a Qualtrics survey from a question spec
- User wants to convert questions into a `.qsf` for upload
- User asks to fix or rebuild a broken QSF
- User mentions Qualtrics import, QSF encoding, or survey file generation

## Before You Start

**Always read the reference file first:**
```
/mnt/skills/user/qsf-generator/references/qualtrics-encoding.md
```
This contains the exact JSON shapes for every question type, every required element, and known gotchas. Do not rely on memory — the reference file is the source of truth.

## Workflow

### 1. Gather Inputs

You need:
- **Questions**: Text, choices, type (single-select, multi-select, text entry, matrix, rank order, NPS, descriptive block)
- **Block structure**: How questions group into pages/sections
- **Mandatory flags**: Which questions require a response
- **Account IDs** (optional): If the user provides a sample QSF export, extract `SurveyOwnerID`, `SurveyBrandID`, and other real IDs. If not, generate plausible placeholder IDs.

### 2. Generate the QSF Programmatically

**Always use a Python script** to generate the QSF — never hand-write JSON. This avoids structural mistakes (missing commas, wrong types, inconsistent IDs).

The script should:
1. Define builder functions for each question type (see reference file for exact shapes)
2. Build all questions with correct field encoding
3. Assemble blocks, flow, and all required elements
4. Write to `/mnt/user-data/outputs/` as `.qsf`
5. Run validation checks before finishing

### 3. Validate Before Delivering

Run these checks programmatically after generation:
- All required elements present: `BL`, `FL`, `PL`, `PROJ`, `QC`, `RS`, `SCO`, `SO`, `SQ` (per question), `STAT`
- Every question has `DataVisibility` and `Configuration`
- `ChoiceOrder` values are strings (not integers)
- `QuestionType` is used (never `Type` alone)
- No invalid fields like `ForceResponseType` in basic validation blocks
- `Choices` is a keyed dict `{"1": {...}}` for all types except NPS (which uses an array)
- `QuestionDescription` is truncated to ~100 chars with `...` suffix
- Matrix questions have both `Choices` (rows) and `Answers` (columns)
- NPS questions use `MC/NPS` selector with `ColumnLabels`

### 4. Deliver

Copy the `.qsf` to `/mnt/user-data/outputs/` and present it. Provide a concise summary of what's in the file (question count, block count, types used).

## Question Type Quick Reference

| Survey Need | QuestionType | Selector | SubSelector |
|---|---|---|---|
| Single-select | MC | SAVR | TX |
| Multi-select | MC | MAVR | TX |
| NPS (0–10) | MC | NPS | — |
| Text entry (single line) | TE | SL | — |
| Text entry (multi line) | TE | ML | — |
| Matrix / Likert | Matrix | Likert | SingleAnswer |
| Rank order (drag) | RO | DND | TX |
| Descriptive text block | DB | TB | — |
| Dropdown | MC | DL | TX |
| Slider | Slider | HSLIDER | — |

**Prefer MC/SAVR, MC/MAVR, and TE/SL for maximum import reliability.** The other types (Matrix, RO, NPS, DB) work but require additional fields — see the reference file for exact encoding.

## Critical Rules

1. **QuestionDescription must be truncated** — max ~100 chars, ending with `...`
2. **ChoiceOrder values must be strings** — `["1","2","3"]` not `[1,2,3]`
3. **NPS Choices is an array**, all other types use a keyed dict
4. **NPS needs ColumnLabels** — `[{Display, IsLabelDefault: true}, ...]` for endpoints
5. **Matrix needs Autoscale in Configuration** — include `TextPosition`, `ChoiceColumnWidth`, `MobileFirst`, `RepeatHeaders`, `WhiteSpace`
6. **TE/SL questions need SearchSource** — `{"AllowFreeResponse": "false"}`
7. **Omit ForceResponse when OFF** — just use `{"Settings": {"Type": "None"}}`
8. **Include DefaultChoices and GradingData** on RO, NPS, Matrix, DB types
9. **Qualtrics reassigns IDs on import** — `SurveyID`, `RS`, `PreviewID` all get overwritten, so placeholder values are fine
10. **Page Breaks in blocks** — use `{"Type": "Page Break"}` in `BlockElements` between questions that should be on separate pages
