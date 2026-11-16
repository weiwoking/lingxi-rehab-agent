import type { RehabReport } from "../contracts/report";
import type { ClinicianPrescription, PatientTrainingTrend, SafetyIntervention, SessionTaskDecision, TherapistReview } from "../contracts/clinicalWorkspace";
import {
  CURRENT_SUBSCORE_FIELDS,
  type CurrentSubscoreKey,
  currentSubscoreRows,
  finiteNumber,
  formatScore,
  formatSubscore,
  isNoDataText
} from "../utils/currentReportSchema";
import { formatCompensation } from "../utils/reportFieldFormatters";
import { buildRehabRecommendations } from "../utils/reportRecommendation";
import {
  FALLBACK_SCORE_NOTICE,
  isFallbackReport,
  reportCompletedReps,
  reportValidReps,
  scoreStatusLabelForReport
} from "../utils/scoreFallbackDisplay";

export function ReportPreview({
  report,
  note,
  onNoteChange,
  clinicalContext
}: {
  report: RehabReport;
  note: string;
  onNoteChange: (value: string) => void;
  clinicalContext?: {
    prescription: ClinicianPrescription | null;
    decision: SessionTaskDecision | null;
    safety: SafetyIntervention[];
    trend: PatientTrainingTrend | null;
    review: TherapistReview | null;
  };
}) {
  const fallbackUsed = isFallbackReport(report);
  const actionSummaries = fallbackUsed ? [] : (report.action_summaries ?? report.score.action_summaries ?? []);
  const subscoreRows = currentSubscoreRows(report.score.subscores);
  const feedback = report.score.feedback.length ? report.score.feedback : report.summary ? [report.summary] : [];
  const compensationText = metricCompensation(report.score.compensation);
  const rehabRecommendations = buildRehabRecommendations(report).items;

  return (
    <article className="report-document">
      <header>
        <h1>{cleanText(report.title)}</h1>
        {report.sensor_mapping_notice && <p className="report-sensor-notice">{cleanText(report.sensor_mapping_notice)}</p>}
      </header>

      <ReportSection title="患者信息" items={{
        patient_id: report.patient.patient_id,
        患侧: report.patient.affected_side,
        训练任务: report.patient.task,
        难度: report.patient.difficulty,
        训练时间: report.patient.training_time,
        session_id: report.patient.session_id,
        device_id: report.patient.device_id
      }} />

      <ReportSection title="训练完成情况" items={{
        计划次数: report.completion.planned_reps,
        实际完成次数: reportCompletedReps(report),
        有效次数: reportValidReps(report),
        训练总时长: durationText(report.completion.duration_sec),
        暂停次数: report.completion.pause_count,
        是否完整完成: report.completion.fully_completed ? "是" : "否",
        结束方式: report.completion.end_reason
      }} />

      <section>
        <h2>综合评分</h2>
        <div className="report-score"><strong>{formatScore(report.score.final_score)}</strong><span>综合评分</span></div>
        <div className="report-metrics">
          <span>评分状态 {scoreStatusLabelForReport(report)}</span>
          {report.score.data_quality_level && <span>数据质量 {report.score.data_quality_level}</span>}
          {compensationText && <span>代偿评估 {compensationText}</span>}
        </div>
      </section>

      {fallbackUsed && (
        <section className="score-fallback-notice">
          <h2>低置信度提示</h2>
          <p>{FALLBACK_SCORE_NOTICE}</p>
        </section>
      )}

      {subscoreRows.length > 0 && (
        <section>
          <h2>五维动作质量评分</h2>
          <div className="report-metrics">
            {subscoreRows.map((item) => (
              <span key={item.key}>{item.label} {formatSubscore(item.value, item.key)}</span>
            ))}
          </div>
        </section>
      )}

      <section>
        <h2>动作评分明细</h2>
        {actionSummaries.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>rep_index</th>
                <th>final_score</th>
                <th>speed</th>
                <th>ROM</th>
                <th>smoothness</th>
                <th>coordination</th>
                <th>muscle activation</th>
                <th>compensation</th>
                <th>feedback</th>
              </tr>
            </thead>
            <tbody>
              {actionSummaries.map((rep) => {
                const subscores = repSubscores(rep);
                const rowFeedback = rep.feedback ?? [];
                return (
                  <tr key={rep.rep_index}>
                    <td>{rep.rep_index}</td>
                    <td>{formatScore(rep.final_score)}</td>
                    {CURRENT_SUBSCORE_FIELDS.map((key) => (
                      <td key={key}>{formatSubscore(subscores[key], key)}</td>
                    ))}
                    <td>{metricCompensation(rep.compensation ?? report.score.compensation)}</td>
                    <td>{rowFeedback.join("；")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p>未检测到完整动作周期，未生成动作明细。</p>
        )}
      </section>

      {compensationText && <ReportSection title="代偿分析" items={{ 状态: compensationText }} />}
      {rehabRecommendations.length > 0 && (
        <section>
          <h2>康复建议</h2>
          <ul>{rehabRecommendations.map((item) => <li key={item}>{cleanText(item)}</li>)}</ul>
        </section>
      )}
      {feedback.length > 0 && <section><h2>动作反馈</h2><ul>{feedback.map((item) => <li key={item}>{cleanText(item)}</li>)}</ul></section>}

      <section><h2>医生备注</h2><textarea value={note} onChange={(event) => onNoteChange(event.target.value)} placeholder="医生可结合患者实际情况补充专业意见……" /></section>
      <ReportSection title="数据与版本" items={{
        评分版本: report.versions.score_version,
        数据结构: report.versions.schema_version,
        source_mode: report.versions.source_mode,
        source_origin: report.versions.source_origin ?? "pc_udp_6006_ingest",
        raw_score_result_path: report.versions.raw_score_result_path ?? "score_result.json",
        compact_score_result_path: report.versions.compact_score_result_path ?? "score_job_result.json",
        device_id: report.versions.device_id,
        generated_at: report.versions.generated_at
      }} />
      {report.disclaimer && <section><h2>声明</h2><p>{cleanText(report.disclaimer)}</p></section>}
    </article>
  );
}

function ReportSection({ title, items }: { title: string; items: Record<string, unknown> }) {
  const hiddenKeys = new Set(["source", "source_mode", "data_origin", "field_sources"]);
  const entries = Object.entries(items)
    .filter(([key]) => !hiddenKeys.has(key))
    .map(([key, value]) => [key, formatValue(value)] as const)
    .filter(([, value]) => value !== "");
  if (!entries.length) return null;
  return <section><h2>{title}</h2><div className="report-grid">{entries.map(([key, value]) => <p key={key}><span>{key}</span><strong>{cleanText(value)}</strong></p>)}</div></section>;
}

function repSubscores(rep: unknown): Partial<Record<CurrentSubscoreKey, number | null>> {
  const source = rep && typeof rep === "object" ? rep as Record<string, unknown> : {};
  const subscores = source.subscores && typeof source.subscores === "object" ? source.subscores as Record<string, unknown> : {};
  return Object.fromEntries(CURRENT_SUBSCORE_FIELDS.map((key) => [key, finiteNumber(subscores[key])])) as Partial<Record<CurrentSubscoreKey, number | null>>;
}

function durationText(value: unknown) {
  const seconds = finiteNumber(value);
  return seconds === null ? "" : `${Math.round(seconds)}s`;
}

function formatValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "number") return Number.isFinite(value) ? value.toFixed(2) : "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function metricCompensation(value: unknown) {
  const formatted = formatCompensation(value);
  return isNoDataText(formatted) ? "" : formatted;
}

function cleanText(value: string) {
  return value
    .replace(/temporary_unverified_development_template/gi, "待临床复核")
    .replace(/DEMO DATA|DEMO RAW FRAME|presentation mode/gi, "训练记录")
    .replace(/模拟数据|假数据|临时数据|开发模板/g, "训练记录")
    .replace(/未实现|未接入/g, "待确认");
}
