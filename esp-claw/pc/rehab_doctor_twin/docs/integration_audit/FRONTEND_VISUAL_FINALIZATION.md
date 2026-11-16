# Frontend Visual Finalization

## Scope

This pass focuses on the PC frontend presentation layer. It keeps the real device, UDP, scoring, prediction, and patient training paths unchanged.

## Public Record Names

The two default records now use one public naming rule:

- SHOWCASE_DRINK_001 is displayed as `训练记录 01`.
- SHOWCASE_DRINK_002 is displayed as `训练记录 02`.

The public UI does not show internal record identifiers. Doctor and patient pages use the same helper for these labels.

## Record Alignment

The overview, data log, Agent trace, doctor report, patient report, and reconstruction pages all consume the same two default drink-water records from `frontend/src/data/showcaseRecords.ts`.

Both records use:

- `task_id = drink_water_right`
- `task = 饮水动作训练`

The former book default record has been removed from the frontend fixture data.

## Technical Field Hiding

The updated pages avoid showing internal record provenance, storage flags, raw score versions, raw JSON, and internal IDs in the visible UI. The fields remain in source data where needed for isolation and filtering.

## Prediction Isolation

The prediction page still queries the prediction API by task tab. Default frontend records are not injected into prediction source sessions by this frontend pass.
