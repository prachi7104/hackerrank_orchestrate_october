# Experiment log

Development measurements on the 32 public samples with `python code/evaluation/main.py`.
The request-authorisation decision is taken from the sample answers (`--intent oracle`; all 32
samples authorise deletion), so these numbers measure the deterministic core only. They are
development fit, not hidden-test accuracy.

Format: observed failure → hypothesis → changed mechanism → measured result → keep/revert.

| # | Observed failure | Change | status | exact | step P / R / F1 | Decision |
|---|---|---|---|---|---|---|
| E0 | — (first end-to-end run) | parsers → identity → links → latest state → rules → ordered steps | 0.938 | 0.250 | 0.974 / 0.777 / 0.865 | baseline |
| E1 | INCOMPLETE rows missing the primary-account future check; support_001 (PDF) unseen; handcrafted COMPLETE rows lack account absence proof | add the future `VERIFY_RECORD_ABSENT` for a linked primary account; read PDF text layers; treat a complete account-profile table capture as absence proof | 0.969 | 0.250 | 0.975 / 0.793 / 0.874 | keep |
| E2 | resources with no visible observation got system `unknown`; PDF identifiers not recognised as direct identifiers | system fallback from the resource prefix; repeated fields keyed `field#n` | 0.969 | 0.375 | 0.964 / 0.829 / 0.891 | keep (bug fix) |
| E3 | case_31 cites E51 (bundle with an empty account collection), we cited E90 | snapshot-bundle profile collections count as absence proof; cite the latest citable observation | 0.969 | 0.406 | 0.970 / 0.834 / 0.897 | keep |

| E4 | authorisation came from the answer key | model classifier (`gemini-3.5-flash-lite`, prompt `authz-v1`, batches of 10, verbatim-quote and id validation) replaces the oracle | 0.969 | 0.406 | 0.970 / 0.834 / 0.897 | keep — all 32 samples classified DELETION, identical to oracle; 4 requests, 4,859 tokens |

Custom authorisation cases (`code/evaluation/intent_cases.py`, 12 hand-written texts: negation,
German/Korean/Portuguese/French, mixed intent, an instruction-like injection): 12/12 correct, 2
requests (one 503 retried successfully). The public samples contain no non-deletion request, so
this small set is the only evidence on false authorisation; it shares an author with the prompt.

| E5 | screenshots unread; catalog links unused | screenshot transcription (11 calls, `shot-v1`) validated in code + data-catalog links as stateless link evidence | 0.969 | 0.344 | 0.947 / 0.834 / 0.887 | regression: case_07 gained object_19 and case_17 gained tombstone_17 from February/March captures |
| E6 | E5 regression: pre-request captures treated as current state | state must come from observations at or after `requested_at`; earlier captures may still link resources | 0.969 | 0.406 | 0.970 / 0.834 / 0.897 | keep — regression removed, screenshots retained |
| E7 | first full candidate: case_33 COMPLETE with no provable steps (validator blocked it) | PDF text also resolves catalog aliases (statement NST-2026-0718-1842 → export_1842) | 0.969 | 0.406 | 0.970 / 0.834 / 0.897 | keep — samples unchanged; candidate validator errors 1 → 0 |

### Review round (24 Oct evening): oracle authorisation, screenshots from cache

| # | Question / failure | Change | exact | step P / R / F1 | Decision |
|---|---|---|---|---|---|
| E8 | Does the post-request state rule hide records that genuinely persist? (`temporal_check.py`) | variants: rule / exempt retained, archived and tombstoned categories / no rule | 13 / 12 / 11 | 0.897 / 0.882 / 0.875 F1 | keep the rule as an inferred convention (not an official rule); the exemption only adds tombstone_17 to case_17, which the answer omits; at most 2 evaluation rows differ |
| E9 | Review: ownership must not be inferred from workflow timing or status alone | primary accounts linked only through deletion-worker timing no longer produce steps | 13 → 8 | 0.970 / 0.834 / 0.897 → 0.974 / 0.782 / 0.868 | applied as directed; cost recorded for the architecture decision |
| E10 | grounding check: backup "verified restore-blocked" from a listing screenshot (evaluation case_55) | flows and archives take state only from observations that report enabled/restore status | 8 | unchanged | keep (bug fix) |
| E11 | readback logs put lookup_key on the `*_started` line with the resource id; it was discarded | carry lookup keys from every line of a trace | 8 → 7 | 0.871 → 0.869 F1 | keep; evidence-based account links in the evaluation set 129 → 166; one sample (case_17) gained the subject's *previous* account |
| E12 | case_17: account seen only in March belongs to an earlier request | primary-account links need evidence observed after `requested_at`; complete-profile absence proves only the primary account | 7 → 8 | 0.974 / 0.788 / 0.871 | keep |

Account-ownership policies on the E12 code: timing never used 8/32, F1 0.871; timing used only for
uncited post-remediation checks 10/32, F1 0.890; timing used as ownership 13/32, F1 0.903 (21/21
sample timing links correct). Decision pending (Prachi).

### Three-tier ownership (review decision: DIRECT → CONDITIONALLY_INFERRED → UNRESOLVED)

`ownership_variants.py`, 32 samples (oracle authorisation), screenshots from cache, no API calls.

| Policy | exact (hand / gen) | step P / R / F1 | resource recall | account verifications missing | accounts correct / wrong / unresolved | unsupported citations (samples / eval) | eval rows changed vs A |
|---|---|---|---|---|---|---|---|
| A evidence only | 8/32 (1 / 7) | 0.974 / 0.788 / 0.871 | 0.685 | 21 | 11 / 0 / 21 | 0 / 0 | — |
| B status-compatible timing, future checks only | 10/32 (2 / 8) | 0.975 / 0.819 / 0.890 | 0.739 | 15 | 17 / 0 / 15 | 0 / 0 | 116 |
| C status-compatible timing as ownership (experimental) | 13/32 (4 / 9) | 0.970 / 0.845 / 0.903 | 0.793 | 9 | 23 / 0 / 9 | 6 / 45 | 161 |
| D conditional inference, future checks only | 10/32 (3 / 7) | 0.975 / 0.803 / 0.881 | 0.712 | 18 | 14 / 0 / 18 | 0 / 0 | 23 |

- E13: `marketing_001` (case_63) excluded: the records about it name only `sj.park@naver.com`; the
  subject link is a data-catalog claim. Unsupported evaluation citations 1 → 0.
- E14: D first used the whole request lifecycle (requested_at → reported_at + 15 min) as the
  window: 1 evaluation inference, 321 cases with competing candidates — no better than A. Revised
  to anchor on the subject's own evidenced operation history (the earliest deletion-worker
  operation, after `requested_at`, on a non-account resource tied to the subject), with
  exclusivity across requests and no use of `reported_status` or `reported_result`. Window
  sensitivity (samples correct/wrong; evaluation inferred): 60 min 3/0, 6; 120 min 3/0, 23;
  180 min 3/0, 24; 240 min 3/0, 24; 360 min 3/0, 24. Chosen: 180 (flat region).
- D evaluation tiers: DIRECT 166, CONDITIONALLY_INFERRED 24, UNRESOLVED 318 (no evidenced
  operation history 87, competing candidates 229, candidate fits another request 2).
- Selection: D passes the gate (adds 3 sample / 24 evaluation account checks over A with 0 wrong,
  0 unsupported, 0 contradicted) and replaces B, which needs status compatibility. D is the default.
- E15: grounding/validator found flows and archives with unknown state producing uncited
  "verified" steps (case_55); unknown state now yields no step. Two evaluation cases (case_55,
  case_63) now have no provable resource at all and are blocked by the validator — open decision.

### Investigator on cases with no provable resource (`agent_blocked.py`, prompt inv-v2)
10 attempts (1 classifier batch for the two texts, 9 investigator incl. one screenshot question),
16,481 tokens. case_55: account question abstained; NO_EVIDENCE answered `backup_9287|PRESENT`
from a screenshot re-read. The first verifier accepted it and the grounding check then rejected the
resulting "restore blocked" step (existence says nothing about restore). Verifier fixed: a backup
needs a restore state and a flow needs a flow state. Replay from cache (0 calls): rejected. The model
also emitted a malformed argument (`backup_9287<span>`). case_63: the model submitted no
resolutions (treated as abstention on the account, marketing_001 conflict and no-evidence questions).
Both rows remain without provable resources; fallback policy pending (Prachi).

### Evidence-gap policy (Prachi's decision, 24 Oct) — E16
General rule (`core.evidence_gap`), applied only when an authorised deletion has no finding and no
verification after all regular rules:
- RESTORE_INFERRED: a linked backup archive shown existing in an admissible capture (after the
  request, before the audit) on a screen that also prints completed restores of that platform
  (`vision.restore_capability`, 1 request, prompt restore-v1, lines kept only if they name a
  backup id and "restore") → restore-risk finding citing that capture, recorded as inferred.
  Evaluation: case_55 / backup_9287 / evidence_32, basis "backup_9286 restored - Completed;
  backup_9281 restored - Completed"; not proven: restore access of this specific archive. The
  grounding checker is unchanged and reports this citation as unsupported.
- UNRESOLVED_EVIDENCE: otherwise the row is serialised fail-closed as INCOMPLETE with `[]` and
  declared a contract exception. Evaluation: case_63 (marketing_001 excluded as contradicted).
- Validator now separates structural errors (grading-blocking) from semantic errors; grounding is
  checked in `code/main.py`. Candidate 20261024T164935Z-0835b3: structural 0, semantic 1
  (case_63), grounding unsupported 1 (case_55), both declared, undeclared 0.
- Measured cost on samples (model authorisation): case_01, case_02 and case_04 (handcrafted, gold
  COMPLETE) also have no provable resource under policy D and become INCOMPLETE `[]`; status
  accuracy 0.969 → 0.875; exact 10/32, step P/R/F1 0.975 / 0.803 / 0.881 unchanged (they had no
  steps either way). Before the policy they were COMPLETE `[]` (status right, semantically invalid).

### E17 — Prachi rejected the INCOMPLETE `[]` default; gap root cause and alternatives
- `UNRESOLVED_EVIDENCE` is now a separate internal status from the decision function. Serialising
  it requires `code/main.py --gap-fallback ...` (noncompliant, recorded in the manifest); without
  the flag no output.csv is written. Sample scoring counts it as a wrong status.
- Root cause (case_01 / 02 / 04): no resource is tied to the subject by any admissible evidence.
  The gold account ids appear only in deletion-worker activity (account_001: activity_3342, 1 min
  before reported_at, one of 4 worker deletes in the request lifecycle; account_002: activity_3376,
  5 min after reported_at, the only one in its lifecycle) or nowhere (account_004). The gold
  citation `evidence_54` (failover + primary profile tables, captured 2 Sep) proves only that no
  profile carrying the subject's identifiers exists there. case_04's analytics_901 (`evidence_56`)
  has no identifiers; its only link is activity_3463 timing. Policy D has no anchor for any of them.
- `gap_variants.py` (no API calls):

| Variant | status | exact | step P / R / F1 | resource recall | sample unresolved | inferred (samples correct / eval) | unsupported citations (samples / eval) |
|---|---|---|---|---|---|---|---|
| D as is | 0.875 | 10/32 | 0.975 / 0.803 / 0.881 | 0.712 | 01, 02, 04 | 3 / 24 | 0 / 1 (case_55, declared) |
| X1 lifecycle-unique fallback for anchorless cases | 0.875 | 8/32 | 0.974 / 0.788 / 0.871 | 0.685 | 01, 02, 04 | 1 / 18 | 0 / 1 |
| X2 cited absence of an inferred account in gap cases | 0.875 | 10/32 | 0.975 / 0.803 / 0.881 | 0.712 | 01, 02, 04 | 3 / 24 | 0 / 1 |
| X1 + X2 | 0.906 | 9/32 | 0.975 / 0.793 / 0.874 | 0.694 | 01, 04 | 1 / 18 | 1 / 1 |

  X1 adds lifecycle claimants that compete with anchored inferences (exclusivity removes 6
  evaluation and 2 sample inferences). X2 does nothing without X1. X1+X2 recovers case_02's status
  only by citing evidence whose subject link is inferred (grounding flags it) and lowers exact and
  F1. Neither is adopted; both flags remain in code, off.

### Investigator pilot (`agent_pilot.py`, run 20261024T155612Z-8d81f0)
29 attempts (one 503 retried), 46,434 input / 1,612 output / 2,379 thinking tokens. 7 cases
investigated (case_07 skipped at the 30-request cap). Of 9 submitted resolutions: 2 accepted by
code verification (case_10 support_002 → DELETE, matches the answer; case_12 analytics_9914 →
ANONYMIZE, answer says DELETE, output unchanged), 4 rejected (case_26 account_3136 would have been
wrong; case_10/21/22 accounts were right but not supported by retrieved ownership evidence), 3
abstentions (case_25 account; case_63 account and marketing_001 conflict, both justified). Labelled
pilot cases: F1 0.655 → 0.737, exact 0/7 → 0/7, no regressions.

Screenshot extraction check (`code/evaluation/screenshot_check.py`, reference read by hand from the
11 images): 24/24 resource ids, 0 unexpected, 16/16 printed identifiers correct, after one
code-side rule (take the record's own-kind id from linked_ids when the header id is a user id).

## Account-linkage variants (E3 code, all 540 deletion requests)

| Variant | step F1 (samples) | exact | linked direct / activity / ambiguous / none | sample links wrong |
|---|---|---|---|---|
| status-compatible, strict time (default) | 0.897 | 0.406 | 139 / 178 / 209 / 14 | 0 |
| without status compatibility | 0.885 | 0.406 | 139 / 35 / 354 / 12 | 0 |
| status-compatible, record-time visibility | 0.921 | 0.531 | 225 / 154 / 147 / 14 | 0 |
| without status compatibility, record-time | 0.906 | 0.500 | 225 / 38 / 265 / 12 | 0 |

Record-time visibility uses records whose own capture time is ≤ `audit_at` inside files exported
later; it never cites them.

**Decision (Prachi, 24 Oct):** keep the strict rule. Only files whose manifest `captured_at` is at
or before `audit_at` may inform linkage, state or citations. The measured cost on the samples is
step F1 0.921 → 0.897 and 17 → 13 exact cases. `--record-time` remains a development flag for
comparison only; final generation never enables it.

## Known remaining failures (default variant)
- Account ambiguous: case_03, 18, 22, 23, 25, 26, 28, 29 (several cases share `reported_at` and result).
- Anonymised analytics with no identifier anywhere: case_03, 04, 21, 25, 26, 27, 28.
- Retained audit records visible only after `audit_at` or nowhere: case_09, 18, 20, 23, 24, 29, 30.
- Category semantics: case_10 (support replica record → gold DELETE), case_12 (hash-keyed analytics → gold DELETE).
- Handcrafted accounts with no activity match: case_04 (appears in no file), 06, 08.
