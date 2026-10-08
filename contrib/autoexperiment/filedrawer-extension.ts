// Bridge used by filedrawer (https://github.com/yrvelez/filedrawer) to validate and build extension proposals.
//
//   node --import tsx scripts/filedrawer-extension.ts schema
//   node --import tsx scripts/filedrawer-extension.ts validate <proposal.json>
//   node --import tsx scripts/filedrawer-extension.ts build <proposal.json> <record.json>
//
// Every command prints exactly one JSON object on stdout. `build` creates an UNPUBLISHED Qualtrics draft through the
// Qualtrics MCP server configured in autoexperiment's settings (credentials stay in that server's .env); it never
// activates the survey or creates a distribution link. The build record is written to <record.json> after every
// operation, so an interrupted build resumes without duplicating anything.
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { zodToJsonSchema } from 'zod-to-json-schema';
import {
  ExtensionProposalSchema,
  designErrors,
  type BuildRecord,
} from '../shared/model.js';

const [, , cmd, file, recordFile] = process.argv;
const out = (o: unknown) => process.stdout.write(JSON.stringify(o) + '\n');

function load(path: string) {
  const raw = JSON.parse(readFileSync(path, 'utf8'));
  const parsed = ExtensionProposalSchema.safeParse(raw);
  if (!parsed.success)
    return {
      proposal: null,
      errors: parsed.error.issues.map(
        (i) => `${i.path.join('.') || '(root)'}: ${i.message}`,
      ),
    };
  return { proposal: parsed.data, errors: designErrors(parsed.data.design) };
}

if (cmd === 'schema') {
  out(zodToJsonSchema(ExtensionProposalSchema, 'ExtensionProposal'));
} else if (cmd === 'validate' && file) {
  const { errors } = load(file);
  out({ ok: errors.length === 0, errors });
} else if (cmd === 'build' && file && recordFile) {
  const { proposal, errors } = load(file);
  if (!proposal || errors.length) {
    out({ ok: false, errors });
    process.exit(1);
  }
  const { Qualtrics, buildSurvey, surveyUrl } = await import(
    '../server/qualtrics.js'
  );
  const record: BuildRecord = existsSync(recordFile)
    ? JSON.parse(readFileSync(recordFile, 'utf8'))
    : {
        id: randomUUID(),
        revisionId: proposal.id,
        status: 'building',
        operations: {},
        blockIds: {},
        questionIds: {},
        errors: [],
        warnings: [],
        artifacts: [],
      };
  const save = () => writeFileSync(recordFile, JSON.stringify(record, null, 1));
  const client = new Qualtrics();
  const log: string[] = [];
  try {
    await client.connect();
    await buildSurvey(proposal.design, record, client, save, (s: string) =>
      log.push(s),
    );
    record.surveyUrl = surveyUrl(record.surveyId!);
    record.status = 'complete';
    save();
    out({
      ok: true,
      surveyId: record.surveyId,
      surveyUrl: record.surveyUrl,
      versionId: record.versionId,
      warnings: record.warnings,
      log: log.slice(-20),
    });
  } catch (e: any) {
    record.status = 'failed';
    record.errors.push(String(e?.message ?? e));
    save();
    out({ ok: false, errors: record.errors, log: log.slice(-20) });
    process.exitCode = 1;
  } finally {
    await client.close();
  }
} else {
  out({
    ok: false,
    errors: [
      'usage: filedrawer-extension.ts schema | validate <proposal.json> | build <proposal.json> <record.json>',
    ],
  });
  process.exitCode = 2;
}
