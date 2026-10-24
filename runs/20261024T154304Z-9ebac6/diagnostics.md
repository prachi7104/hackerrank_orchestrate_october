# Diagnostic report (candidate output, not the final submission)

Development diagnostics only. Nothing here estimates hidden-test accuracy.

- rows: 518; validator errors: 0
- status: {'INCOMPLETE': 354, 'COMPLETE': 154, 'NOT_APPLICABLE': 10}
- NOT_APPLICABLE request types: {'CORRECTION': 3, 'MARKETING_OPT_OUT': 2, 'EXPORT': 1, 'ACCOUNT_SUPPORT': 1, 'ACCESS': 2, 'OTHER': 1}
- steps per row: min 0, median 6, max 14, total 2818
- operations: {'REPORT_REMAINING_RECORD': 362, 'DELETE_RECORD': 362, 'VERIFY_RECORD_ABSENT': 604, 'VERIFY_RECORD_PRESENT': 311, 'REPORT_ACTIVE_DATA_FLOW': 113, 'DISABLE_DATA_FLOW': 113, 'VERIFY_DATA_FLOW_DISABLED': 146, 'REPORT_EXPOSED_PII': 220, 'REDACT_RECORD': 147, 'VERIFY_RECORD_REDACTED': 185, 'VERIFY_RESTORE_BLOCKED': 59, 'ANONYMIZE_RECORD': 73, 'VERIFY_RECORD_ANONYMIZED': 73, 'REPORT_RESTORE_RISK': 25, 'BLOCK_RESTORE': 25}

## Primary-account linkage (deletion cases)
- {'none': 11, 'direct': 129, 'activity': 167, 'ambiguous': 201}
- deletion cases with no account_service step: 212 (first 20: ['case_33', 'case_35', 'case_39', 'case_45', 'case_46', 'case_47', 'case_48', 'case_49', 'case_50', 'case_51', 'case_52', 'case_53', 'case_55', 'case_56', 'case_57', 'case_58', 'case_59', 'case_60', 'case_61', 'case_62'])

## Linked resources whose state could not be established (no post-request observation)
- by system: {'export_service': 1, 'security_service': 1, 'support_platform': 1, 'account_service': 3}

## Evidence coverage
- distinct evidence ids cited: 71; manifest files never cited: 23
- most cited: [('evidence_25', 27), ('evidence_36', 27), ('evidence_62', 27), ('evidence_83', 26), ('evidence_73', 26), ('evidence_34', 26), ('evidence_91', 25), ('evidence_84', 25), ('evidence_13', 24), ('evidence_79', 24)]

## Screenshots
- files: 11; extraction ok: 11; accepted records: 24; rejected: 7

## Operational failures
- request classification: 0 (generation refuses to write otherwise)
- screenshot extraction failures: []

## API usage (this run, from the ledger)
- model: gemini-3.5-flash-lite; {"attempts": 0, "failed_attempts": 0, "cache_hits": 49, "input": 0, "output": 0, "thinking": 0, "total": 0, "unknown_token_fields": 0, "latency_s": 0.0}
- actual cost: 0 (Free tier); list-price equivalent is computed in usage_report.md from the final run
