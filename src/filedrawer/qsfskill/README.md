# QSF Generator — Claude Skill

A Claude skill that generates Qualtrics Survey Format (`.qsf`) files that import cleanly on the first try. Built from real Qualtrics exports, not documentation.

## What It Does

Give Claude a set of survey questions and this skill encodes them into the exact JSON structure Qualtrics expects — correct element types, field names, choice encodings, and validation blocks. No manual JSON editing, no import errors.

### Supported Question Types

| Type | Qualtrics Encoding |
|---|---|
| Single-select | MC / SAVR |
| Multi-select | MC / MAVR |
| NPS (0–10) | MC / NPS |
| Text entry | TE / SL or ML |
| Matrix / Likert | Matrix / Likert |
| Rank order | RO / DND |
| Descriptive text | DB / TB |
| Dropdown | MC / DL |
| Slider | Slider / HSLIDER |

## Repo Structure

```
qsf-generator/
├── SKILL.md                          # Main skill file (Claude reads this)
├── references/
│   └── qualtrics-encoding.md         # Exact JSON shapes per question type
└── README.md
```

## Installation

### Claude Projects (claude.ai)

Upload `SKILL.md` and `references/qualtrics-encoding.md` as project knowledge files.

### Claude Code / Cursor

Clone this repo into your skill directory:

```bash
# If using Claude Code's skill system
git clone https://github.com/YOUR_USERNAME/qsf-generator.git .claude/skills/qsf-generator

# Or wherever your tool reads custom skills from
git clone https://github.com/YOUR_USERNAME/qsf-generator.git
```

Then reference the skill path in your project configuration.

### MCP / Custom Setup

Point your skill loader at `SKILL.md` as the entry point. The skill expects `references/qualtrics-encoding.md` to be accessible at a relative path from `SKILL.md`.

## How It Works

1. **You describe your survey** — questions, choices, logic, blocks
2. **Claude reads `SKILL.md`** to understand the workflow and rules
3. **Claude reads `references/qualtrics-encoding.md`** for exact JSON shapes
4. **A Python script generates the `.qsf`** — never hand-written JSON
5. **Validation runs automatically** — checks all required elements, field types, encoding rules
6. **You get a `.qsf` file** ready to upload to Qualtrics

## Contributing

The reference file (`references/qualtrics-encoding.md`) is the core of this skill. To improve it:

1. Export a working `.qsf` from Qualtrics that uses the question type you want to document
2. Extract the relevant JSON shapes and note any required fields
3. Add the encoding to `qualtrics-encoding.md` with examples
4. Test by having Claude generate a QSF using the new encoding and importing it

## License

MIT
