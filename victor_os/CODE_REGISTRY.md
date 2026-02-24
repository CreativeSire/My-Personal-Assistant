# CODE_REGISTRY.md

Version: `v1`

## Reason Codes (Terminal/Review Classification)

| Code | Meaning | Routing Impact |
|---|---|---|
| `missing_delivery_date` | Delivery date unresolved after extraction/normalization | Review |
| `missing_receiver` | Receiver name/location unresolved | Review |
| `missing_invoice_number` | Invoice number not found | Review |
| `invoice_number_not_6_digits` | Invoice number present but invalid after normalization | Review |
| `schema_extraction_failed` | Extracted payload failed schema/validation checks | Review |
| `unsupported_file_type` | Input extension not supported by contract | Failed item |
| `pipeline_exception` | Runtime/system exception during processing | Failed |
| `ocr_no_text` | OCR produced no usable text evidence | Usually Review unless later pass recovers required fields |
| `hash_identical_duplicate` | Same content already seen in this job | `skipped_duplicate` (non-fatal, not Review/Failed) |

## Warning Codes (Non-Blocking, Affects OK Bucket)

| Code | Meaning | Routing Impact |
|---|---|---|
| `date_best_score_inferred` | Date selected via scoring/inference path | OK/WARNINGS if required fields valid |
| `date_from_stamp_region` | Date chosen from stamp/signature region | OK/WARNINGS if required fields valid |
| `date_ddmmyy_assumed` | Ambiguous short date interpreted with DD/MM/YY policy | OK/WARNINGS if required fields valid |
| `low_confidence_extraction` | Overall extraction confidence below threshold | OK/WARNINGS if required fields valid |

## Registry Rules
- Registry is finite and versioned.
- Any new reason/warning code requires:
  - explicit addition in this file,
  - routing impact declaration,
  - acceptance test update.
