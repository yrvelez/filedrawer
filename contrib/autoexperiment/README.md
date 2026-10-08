# autoexperiment bridge

`filedrawer-extension.ts` lets filedrawer validate and build extension proposals with autoexperiment (local,
TypeScript). Copy it into autoexperiment's `scripts/` folder:

```bash
cp contrib/autoexperiment/filedrawer-extension.ts ~/Projects/autoexperiment/scripts/
```

filedrawer finds autoexperiment through `$FD_AUTOEXPERIMENT_DIR`, `extensions.autoexperiment_dir` in `config.yaml`, or
`~/Projects/autoexperiment`. Building uses the Qualtrics MCP server configured in autoexperiment's settings; the
Qualtrics token stays in that server's `.env` and never passes through filedrawer. Builds create unpublished drafts
only: the survey is never activated and no distribution link is made.
