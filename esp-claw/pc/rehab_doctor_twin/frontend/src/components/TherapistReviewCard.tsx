import { useEffect, useState } from "react";
import type { TherapistReview } from "../contracts/clinicalWorkspace";

export function TherapistReviewCard({ review, onSave }: { review: TherapistReview | null; onSave: (review: TherapistReview) => Promise<void> }) {
  const [draft, setDraft] = useState<TherapistReview | null>(review);
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(review), [review]);

  if (!draft) return <section className="panel clinical-card"><h2>治疗师审核</h2><p className="clinical-muted">审核数据读取中...</p></section>;

  const save = async (decision: TherapistReview["decision"]) => {
    const next = { ...draft, decision };
    setDraft(next);
    setSaving(true);
    try {
      await onSave(next);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="panel clinical-card review-card">
      <header className="clinical-card-head">
        <div><span>临床复核</span><h2>治疗师审核</h2></div>
        <strong>{label(draft.decision)}</strong>
      </header>
      <div className="review-columns">
        <div>
          <h3>智能体建议</h3>
          <p>{draft.agent_suggestion}</p>
          <small>{draft.suggestion_notice}</small>
        </div>
        <div>
          <h3>治疗师确认</h3>
          <textarea value={draft.doctor_note} onChange={(event) => setDraft({ ...draft, doctor_note: event.target.value })} placeholder="请输入医生备注" />
          <input value={draft.confirmed_plan} onChange={(event) => setDraft({ ...draft, confirmed_plan: event.target.value })} />
          <div className="review-actions">
            <button disabled={saving} onClick={() => void save("accepted")}>采纳</button>
            <button disabled={saving} onClick={() => void save("modified")}>修改后确认</button>
            <button disabled={saving} onClick={() => void save("rejected")}>不采纳</button>
          </div>
        </div>
      </div>
    </section>
  );
}

function label(decision: TherapistReview["decision"]) {
  if (decision === "accepted") return "已采纳";
  if (decision === "modified") return "已修改确认";
  if (decision === "rejected") return "未采纳";
  return "待审核";
}
