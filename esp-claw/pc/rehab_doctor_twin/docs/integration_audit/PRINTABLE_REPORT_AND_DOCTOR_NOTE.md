# Printable Report And Doctor Note

## Printable Report

Added a white A4 report page at:

`/doctor/reports/:recordId/print`

The page is designed for browser printing and browser `Save as PDF`. It uses CSS `@page` with A4 size and hides navigation controls during print through `.no-print`.

## Report Content

The print page shows business-facing fields:

- patient id
- public training record label
- training date and duration
- drink-water action type
- completion count
- report status
- composite score
- IMU DTW
- EMG score
- data quality
- compensation assessment
- fatigue level
- BRS
- deterministic per-repetition action rows
- rehabilitation suggestion
- doctor note

It does not display raw JSON, internal record identifiers, source flags, or debug provenance fields.

## Doctor Note Storage

Doctor notes are stored only in browser localStorage under:

`rehab_doctor_note:<internalRecordId>`

The stored payload contains:

- `record_id`
- `note`
- `updated_at`

This frontend-only note does not modify `score_result.json`, `report.json`, scoring service output, or backend session files.

## Patient View

The patient report page is read-only. It can open the printable report, but it does not expose the doctor-note editor.
