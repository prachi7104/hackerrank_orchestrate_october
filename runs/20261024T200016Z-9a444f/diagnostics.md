# Diagnostic report (candidate output, not the final submission)

Development diagnostics only. Nothing here estimates hidden-test accuracy.

- rows: 518
- status: {'INCOMPLETE': 354, 'COMPLETE': 153, 'UNRESOLVED_EVIDENCE': 1, 'NOT_APPLICABLE': 10}
- NOT_APPLICABLE request types: {'CORRECTION': 3, 'MARKETING_OPT_OUT': 2, 'EXPORT': 1, 'ACCOUNT_SUPPORT': 1, 'ACCESS': 2, 'OTHER': 1}
- steps per row: min 0, median 6, max 14, total 2710
- operations: {'REPORT_REMAINING_RECORD': 361, 'DELETE_RECORD': 361, 'VERIFY_RECORD_ABSENT': 496, 'VERIFY_RECORD_PRESENT': 311, 'REPORT_ACTIVE_DATA_FLOW': 113, 'DISABLE_DATA_FLOW': 113, 'VERIFY_DATA_FLOW_DISABLED': 146, 'REPORT_EXPOSED_PII': 220, 'REDACT_RECORD': 147, 'VERIFY_RECORD_REDACTED': 185, 'VERIFY_RESTORE_BLOCKED': 59, 'ANONYMIZE_RECORD': 73, 'VERIFY_RECORD_ANONYMIZED': 73, 'REPORT_RESTORE_RISK': 26, 'BLOCK_RESTORE': 26}

## Primary-account linkage (deletion cases)
- {'none': 10, 'direct': 166, 'ambiguous': 171, 'activity': 161}
- deletion cases with no account_service step: 319 (first 20: ['case_33', 'case_35', 'case_39', 'case_45', 'case_47', 'case_48', 'case_49', 'case_50', 'case_52', 'case_53', 'case_54', 'case_55', 'case_56', 'case_57', 'case_58', 'case_59', 'case_60', 'case_61', 'case_62', 'case_63'])

## Linked resources whose state could not be established (no post-request observation)
- by system: {'export_service': 1, 'security_service': 1, 'support_platform': 1, 'backup_service': 1}

## Evidence coverage
- distinct evidence ids cited: 63; manifest files never cited: 31
- most cited: [('evidence_73', 29), ('evidence_25', 27), ('evidence_36', 27), ('evidence_91', 26), ('evidence_79', 26), ('evidence_34', 26), ('evidence_64', 25), ('evidence_13', 24), ('evidence_84', 24), ('evidence_83', 24)]

## Screenshots
- files: 11; extraction ok: 11; accepted records: 24; rejected: 7

## Operational failures
- request classification: 0 (generation refuses to write otherwise)
- screenshot extraction failures: []

## Validation
- structural errors: 0
- semantic errors: 1 (declared exceptions: 2; undeclared: 0)
- grounding: 1033 cited steps supported, 1 unsupported
  - case_63: INCOMPLETE without findings
  - case_55: REPORT_RESTORE_RISK backup_9287 cites evidence_32: evidence_32 does not show the state ACTIVE

## Evidence-gap policy (contract exceptions and inferences, not proof)
- case_55: {"policy": "RESTORE_INFERRED", "inferences": [{"resource_id": "backup_9287", "evidence_id": "evidence_32", "basis": "archive of the subject shown existing; platform restore capability printed on the same screen: backup_9286 restored - Completed; backup_9281 restored - Completed", "not_proven": "restore access of this specific archive"}]}
- case_63: {"policy": "UNRESOLVED_EVIDENCE", "contract_exception": true, "unknown_state": [], "linked": [], "excluded_as_contradicted": {"marketing_001": "records name ['sj.park@naver.com'] but never the subject"}}

## Approved serialisation exceptions (noncompliant; the decision stays UNRESOLVED_EVIDENCE)
- case_63: written as INCOMPLETE with steps []; approval: Prachi Agarwalla, 2026-10-24 23:20 IST (chat approval; semantic contract exception); excluded as contradicted: {"marketing_001": "records name ['sj.park@naver.com'] but never the subject"}

## API usage (this run, from the ledger)
- model: gemini-3.5-flash-lite; {"attempts": 0, "failed_attempts": 0, "cache_hits": 60, "input": 0, "output": 0, "thinking": 0, "total": 0, "unknown_token_fields": 0, "latency_s": 0.0}
- actual cost: 0 (Free tier); list-price equivalent is computed in usage_report.md from the final run
