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
