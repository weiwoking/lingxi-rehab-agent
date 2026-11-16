import { ArrowLeft, Printer } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { EmptyState } from "../components/EmptyState";
import type { RehabReport } from "../contracts/report";
import { DRINK_TASK_NAME, PATIENT_ID, getPublicRecordLabel, isShowcaseSession, showcaseReports } from "../data/showcaseRecords";
import { fetchSessionReport } from "../services/reportApi";
import {
  CURRENT_SUBSCORE_FIELDS,
  type CurrentSubscoreKey,
  currentSubscoreRows,
  finiteNumber,
  formatScore,
  formatSubscore,
  isNoDataText
} from "../utils/currentReportSchema";
import { loadDoctorNote } from "../utils/doctorNotes";
import { formatCompensation } from "../utils/reportFieldFormatters";
import { buildRehabRecommendations } from "../utils/reportRecommendation";
import {
  FALLBACK_SCORE_NOTICE,
  isFallbackReport,
  reportCompletedReps,
  reportValidReps,
  scoreStatusLabelForReport
} from "../utils/scoreFallbackDisplay";

export function PrintableReportPage() {
  const { recordId = "" } = useParams();
  const [report, setReport] = useState<RehabReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setReport(null);

    if (!recordId) {
      setLoading(false);
      setError("缺少训练记录编号，无法生成打印报告。");
      return;
    }

    if (isShowcaseSession(recordId)) {
      setLoading(false);
      setReport(showcaseReports[recordId] ?? null);
      if (!showcaseReports[recordId]) setError("该训练记录暂无报告。");
      return;
    }

    fetchSessionReport(PATIENT_ID, recordId)
      .then((value) => {
        if (cancelled) return;
        setReport(value);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setError("该训练记录的正式报告暂不可用，未生成打印内容。");
        setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [recordId]);

  const actionRows = useMemo(() => makeActionRows(report), [report]);
  const note = recordId ? loadDoctorNote(recordId) : "";
  const label = report ? reportLabel(recordId) : "训练记录";
  const fallbackUsed = isFallbackReport(report);
  const reportSubscores = report ? currentSubscoreRows(report.score.subscores) : [];
  const compensationText = report ? metricCompensation(report.score.compensation) : "";
  const rehabRecommendations = report ? buildRehabRecommendations(report).items : [];

  return (
    <main className="printable-report-page">
      <nav className="print-toolbar no-print">
        <Link to={recordId ? `/doctor/reports?session_id=${encodeURIComponent(recordId)}` : "/doctor/reports"}>
          <ArrowLeft size={16} />
          返回报告
        </Link>
        <button type="button" onClick={() => window.print()} disabled={!report}>
          <Printer size={16} />
          打印 / 保存为 PDF
        </button>
      </nav>

      {loading ? (
        <EmptyState title="正在读取报告" description="请稍候，报告加载完成后再打印。" />
      ) : !report ? (
        <EmptyState title="无法打印该记录" description={error || "该训练记录暂无可用报告。"} />
      ) : (
        <article className="print-sheet">
          <header>
            <h1>灵犀智伴脑卒中上肢康复训练评估报告</h1>
            <p>ESP-Claw 多传感器康复系统</p>
          </header>

          <section className="print-grid">
            <PrintFact label="患者编号" value={report.patient.patient_id || PATIENT_ID} />
            <PrintFact label="训练编号" value={label} />
            {formatDate(report.patient.training_time) && <PrintFact label="训练日期" value={formatDate(report.patient.training_time)} />}
            {formatDurationSeconds(report.completion.duration_sec) && <PrintFact label="训练时长" value={formatDurationSeconds(report.completion.duration_sec)} />}
            <PrintFact label="动作类型" value={report.patient.task || DRINK_TASK_NAME} />
            <PrintFact label="完成次数" value={`${reportCompletedReps(report)} 次`} />
            <PrintFact label="有效次数" value={`${reportValidReps(report)} 次`} />
            <PrintFact label="报告状态" value={scoreStatusLabelForReport(report)} />
          </section>

          {fallbackUsed && (
            <section className="score-fallback-notice print-section avoid-break">
              <strong>低置信度提示</strong>
              <p>{FALLBACK_SCORE_NOTICE}</p>
            </section>
          )}

          <section className="print-section">
            <h2>综合评估</h2>
            <div className="print-grid">
              <PrintFact label="综合评分" value={formatScore(report.score.final_score)} />
              {report.score.data_quality_level && <PrintFact label="数据质量" value={report.score.data_quality_level} />}
              <PrintFact label="完成情况" value={`${reportCompletedReps(report)} / ${report.completion.planned_reps || reportCompletedReps(report)} 次`} />
              <PrintFact label="有效动作" value={`${reportValidReps(report)} 次`} />
              {compensationText && <PrintFact label="代偿评估" value={compensationText} />}
            </div>
          </section>

          {reportSubscores.length > 0 && (
            <section className="print-section avoid-break">
              <h2>五维动作质量评分</h2>
              <div className="print-grid">
                {reportSubscores.map((item) => (
                  <PrintFact key={item.key} label={item.label} value={formatSubscore(item.value, item.key)} />
                ))}
              </div>
            </section>
          )}

          <section className="print-section avoid-break">
            <h2>动作明细</h2>
            <table>
              <thead>
                <tr>
                  <th>次数</th>
                  <th>综合</th>
                  <th>速度</th>
                  <th>ROM</th>
                  <th>平滑</th>
                  <th>协调</th>
                  <th>肌肉激活</th>
                  <th>代偿</th>
                </tr>
              </thead>
              <tbody>
                {actionRows.length ? actionRows.map((row) => (
                  <tr key={row.rep_index}>
                    <td>第 {row.rep_index} 次</td>
                    <td>{formatScore(row.final_score)}</td>
                    {CURRENT_SUBSCORE_FIELDS.map((key) => (
                      <td key={key}>{formatSubscore(row.subscores[key], key)}</td>
                    ))}
                    <td>{row.compensation}</td>
                  </tr>
                )) : (
                  <tr>
                    <td colSpan={8}>未检测到完整动作周期，未生成动作明细。</td>
                  </tr>
                )}
              </tbody>
            </table>
          </section>

          {rehabRecommendations.length > 0 && (
            <section className="print-section avoid-break">
              <h2>康复建议</h2>
              {rehabRecommendations.map((item) => (
                <p key={item}>• {item}</p>
              ))}
            </section>
          )}

          {note && (
            <section className="print-section avoid-break">
              <h2>医生备注</h2>
              <p>{note}</p>
            </section>
          )}

          <footer>
            <span>医生签名：___________</span>
            <span>日期：____ 年 __ 月 __ 日</span>
          </footer>
        </article>
      )}
    </main>
  );
}

function PrintFact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <p>
      <span>{label}</span>
      <strong>{value}</strong>
    </p>
  );
}

interface PrintActionRow {
  rep_index: number;
  final_score: number | null;
  subscores: Partial<Record<CurrentSubscoreKey, number | null>>;
  compensation: string;
}

function makeActionRows(report: RehabReport | null): PrintActionRow[] {
  if (!report || isFallbackReport(report)) return [];
  return (report.action_summaries ?? report.score.action_summaries ?? []).map((item) => {
    const record = item as unknown as Record<string, unknown>;
    const subscores = record.subscores && typeof record.subscores === "object" ? record.subscores as Record<string, unknown> : {};
    return {
      rep_index: item.rep_index,
      final_score: item.final_score,
      subscores: Object.fromEntries(CURRENT_SUBSCORE_FIELDS.map((key) => [key, finiteNumber(subscores[key])])) as PrintActionRow["subscores"],
      compensation: metricCompensation(item.compensation ?? report.score.compensation)
    };
  });
}

function reportLabel(recordId: string) {
  return isShowcaseSession(recordId) ? getPublicRecordLabel(recordId) : "训练记录";
}

function formatDate(value: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatDurationSeconds(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${Math.round(value)} 秒`;
}

function metricCompensation(value: unknown) {
  const formatted = formatCompensation(value);
  return isNoDataText(formatted) ? "" : formatted;
}
