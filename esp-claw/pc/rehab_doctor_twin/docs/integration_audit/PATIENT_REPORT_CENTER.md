# Patient Report Center

## Routes

- `/patient`: real-time patient training page.
- `/patient/reports`: independent patient report center.

The real-time training page no longer embeds the historical report list or full report preview. When a formal report is ready, it shows a lightweight `查看训练报告` entry that links to `/patient/reports?session_id=<session_id>`.

## Showcase Records

Two deterministic frontend showcase fixtures are provided:

- `SHOWCASE_DRINK_001`
- `SHOWCASE_BOOK_001`

They are marked:

- `record_kind=showcase`
- `source_mode=showcase`
- `source_origin=system_showcase_fixture`
- `formal=false`
- `prediction_eligible=false`

They are used only for page display in logs, Agent trace, reports, action reconstruction, and patient report history. They do not create CSV, UDP frames, fake sensor waves, formal sessions, or score jobs.

## Patient Report Visibility

The patient report center hides task id, task name, internal score task name, side, and planned target reps. It reads the same report shape as the doctor side for report metrics, scores, action summaries, compensation, fatigue, feedback, training time, and duration.

## Empty State

When no report is available, `/patient/reports` displays `暂无正式训练报告` and a short completion hint.
