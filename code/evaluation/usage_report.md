# Usage report

Generated from `code/.cache/ledger.jsonl` for run `20261024T200014Z-4a5374`, the run that produced the submitted `output.csv` (518 requests in `dataset/deletion_requests.csv`).

- Provider: Google Gemini Developer API, free tier (no billing account, no paid features).
- Model requested: gemini-3.5-flash-lite; model versions returned: gemini-3.5-flash-lite.
- Actual cost: **₹0**. The list-price column is a hypothetical paid-tier equivalent (input $0.30 / 1M, output and thinking $2.50 / 1M tokens) for transparency only.
- Token counts are the provider's `usageMetadata` (prompt, candidates, thoughts, total). The provider's total already includes thinking tokens.

## Requests sent to the provider by this run

| Scope | Requests | Failed | Input tokens | Output tokens | Thinking tokens | Total tokens | Total per request | List-price equivalent | Unknown token fields |
|---|---|---|---|---|---|---|---|---|---|
| **all models, this run** | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 | $0.0000 | none |

## Responses reused from the cache

This run reused 60 cached responses (identical model, prompt, schema, configuration and input). They cost nothing in this run; the table attributes them to the runs that originally made the requests.

| Scope | Requests | Failed | Input tokens | Output tokens | Thinking tokens | Total tokens | Total per request | List-price equivalent | Unknown token fields |
|---|---|---|---|---|---|---|---|---|---|
| gemini-3.5-flash-lite · intent · from run 20261024T153423Z-0977d9 | 38 | 0 | 34,416 | 21,492 | 0 | 55,908 | 107.9 | $0.0641 | none |
| gemini-3.5-flash-lite · investigator · from run 20261024T174154Z-ee5e05 | 8 | 0 | 15,166 | 628 | 219 | 16,013 | 30.9 | $0.0067 | none |
| gemini-3.5-flash-lite · investigator:screen · from run 20261024T174154Z-ee5e05 | 2 | 0 | 2,304 | 165 | 0 | 2,469 | 4.8 | $0.0011 | none |
| gemini-3.5-flash-lite · vision · from run 20261024T153107Z-ca5b40 | 11 | 0 | 15,800 | 3,083 | 0 | 18,883 | 36.5 | $0.0124 | none |
| gemini-3.5-flash-lite · vision:restore · from run 20261024T164853Z-fcd3b5 | 1 | 0 | 1,105 | 89 | 0 | 1,194 | 2.3 | $0.0006 | none |
| **all reused responses** | 60 | 0 | 68,791 | 25,457 | 219 | 94,467 | 182.4 | $0.0848 | none |

## Overall: tokens behind the submitted output

| Scope | Requests | Failed | Input tokens | Output tokens | Thinking tokens | Total tokens | Total per request | List-price equivalent | Unknown token fields |
|---|---|---|---|---|---|---|---|---|---|
| **this run + reused responses** | 60 | 0 | 68,791 | 25,457 | 219 | 94,467 | 182.4 | $0.0848 | none |

Average per evaluation request: 182.4 tokens, 0.116 provider requests; list-price equivalent $0.000164 per request, actual ₹0.
