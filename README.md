# Ghost Data Still Exists — deletion-audit agent

HackerRank Orchestrate, October 2026. For each request in `dataset/deletion_requests.csv` the
system decides whether the person authorised deletion, reconstructs what remains of their data at
`audit_at` from the supplied evidence, and writes `output.csv` (`case_id,deletion_status,steps_json`).
The challenge contract is in [`problem_statement.md`](./problem_statement.md).

## Design in one paragraph

A deterministic engine does everything that can be computed exactly; a model is used only where
meaning has to be read. The engine parses every evidence file (JSON exports and snapshot bundles,
a handover text file, six log formats, SQLite snapshots, PDF text) into one table of resource
observations, resolves the subject's current and historical identifiers, links resources to the
subject only through admissible evidence, takes each resource's state at `audit_at`, applies
`deletion_rules.csv`, and emits ordered findings, remediation and verification steps. Gemini
(`gemini-3.5-flash-lite`, free tier) classifies whether each request authorises deletion,
transcribes the 11 screenshots into records that code then validates, and runs a bounded,
tool-using investigator on the few cases with an open semantic or conflicting-evidence question.
Every investigator answer is verified in code against the evidence it actually retrieved before it
can change a row.

## Setup

Python 3.10 or newer.

```bash
python -m venv .venv
.venv/bin/pip install -r code/requirements.txt          # Windows: .venv\Scripts\pip install -r code/requirements.txt
```

Model calls need a free-tier Gemini Developer API key in the environment or in a repo-root `.env`
(never committed):

```text
GEMINI_API_KEY=...
```

## Run

```bash
python code/main.py --promote          # classify, audit, validate; writes runs/<run_id>/ and, if valid, output.csv
python code/validate.py output.csv     # structural and semantic report for any output file
python code/evaluation/main.py         # development evaluation on the 32 labelled samples (same decision path)
python -m pytest code/tests            # contract, ownership and model-boundary tests (no API calls)
```

`code/main.py` writes each candidate to `runs/<run_id>/` (`output.csv`, `manifest.json` with input
and output hashes, `traces.jsonl` with the evidence behind every row, `diagnostics.md`). It refuses
to write an output when a request cannot be classified, and it refuses to serialise a case with no
admissible evidence (`UNRESOLVED_EVIDENCE`) unless a fallback is explicitly approved with
`--gap-fallback ... --fallback-approval "<approver, date>"`; such rows are recorded as
noncompliant exceptions in the manifest.

Rows are written in numeric `case_id` order, the order of `dataset/deletion_requests.csv`.

## Code map (`code/`)

| Module | Role |
|---|---|
| `schema.py` | output contract: statuses, operations, ordering, strict step parsing |
| `evidence.py` | parsers for every evidence format → `Observation` table |
| `identity.py` | current and historical identifiers of a subject |
| `linking.py` | primary-account ownership: DIRECT → CONDITIONALLY_INFERRED → UNRESOLVED |
| `core.py` | the decision function `audit_case` (state at `audit_at`, policy, steps, evidence-gap states) |
| `intent.py` | batched request-authorisation classifier (model boundary, strict validation) |
| `vision.py` | screenshot transcription and code-side validation |
| `agent.py` | bounded investigator: read-only tools, forced submit, code verification of answers |
| `grounding.py` | checks each cited step against what the cited evidence says |
| `llm.py` | Gemini client: append-only ledger, rate and daily-budget control, versioned cache |
| `pipeline.py`, `main.py`, `validate.py` | shared run path, generation entry point, validator |
| `prompts/` | all model prompts |
| `evaluation/` | scorer, experiments, reports |

## Rules the engine enforces

- Evidence is usable only if its file was captured at or before `audit_at`; state must come from an
  observation made after `requested_at`. Workflow statuses and activity results are claims.
- Ownership of a resource needs a record carrying one of the subject's identifiers. A primary
  account may be *inferred* only from the deletion worker's operations on the subject's own
  evidenced records, never from `reported_status`; an inferred account appears only as an uncited
  future check.
- A record whose own contents name another person is excluded from the subject's case.
- Every cited step is checked for subject, resource, state and time (`grounding.py`).

## Reports and limitations

`code/evaluation/evaluation_report.md` (accuracy on the samples, custom evaluation cases, failures
and improvements), `code/evaluation/usage_report.md` (model calls, tokens and cost of the run that
produced `output.csv`) and `code/evaluation/experiments.md` (every measured change). Sample scores
are development measurements on 32 labelled cases, not an estimate of hidden-test accuracy. Known
limitations, including cases without admissible evidence, are listed in the evaluation report.

Cost: all model calls use the Gemini free tier (₹0).
