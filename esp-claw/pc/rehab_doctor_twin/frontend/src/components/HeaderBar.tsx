import { Activity, FileText, Play, RotateCcw, Wifi, WifiOff } from "lucide-react";
import type { RehabSnapshot } from "../contracts/realtimeMessages";

interface Props {
  snapshot: RehabSnapshot | null;
  wsStatus: "connecting" | "open" | "closed";
  lastMessageAt: number | null;
  latencyMs: number | null;
  onStart: () => void;
  onReset: () => void;
  onOpenReport: () => void;
}

export function HeaderBar({ snapshot, wsStatus, lastMessageAt, latencyMs, onStart, onReset, onOpenReport }: Props) {
  const delayed = lastMessageAt ? Date.now() - lastMessageAt > 2000 : true;
  const interrupted = lastMessageAt ? Date.now() - lastMessageAt > 5000 : false;
  const reportReady = snapshot?.report_ready ?? false;
  const running = ["PREPARING", "RUNNING", "UPLOADING", "SCORING"].includes(snapshot?.status ?? "");
  return (
    <header className="header-bar">
      <div className="brand">
        <div className="brand-mark"><Activity size={24} /></div>
        <div>
          <h1>灵犀智伴-脑卒中上肢康复医生端数字孪生系统</h1>
          <span>LINGXI REHAB DIGITAL TWIN</span>
        </div>
      </div>
      <div className="status-strip">
        <span className={`badge ${wsStatus === "open" ? "ok" : "warn"}`}>{wsStatus === "open" ? <Wifi size={15} /> : <WifiOff size={15} />} WebSocket {wsStatus === "open" ? "已连接" : "连接中"}</span>
        <span className="badge">会话 {snapshot?.session_id ?? "待启动"}</span>
        <span className={`badge ${interrupted ? "danger" : delayed ? "warn" : "ok"}`}>延迟 {latencyMs ?? 0} ms</span>
        <span className="badge">最后消息 {lastMessageAt ? new Date(lastMessageAt).toLocaleTimeString() : "--"}</span>
      </div>
      <div className="actions">
        <button onClick={onReset} className="icon-btn"><RotateCcw size={16} /> 重置</button>
        <button onClick={onStart} className="icon-btn primary" disabled={running}><Play size={16} /> 开始训练</button>
        <button onClick={onOpenReport} className="icon-btn report" disabled={!reportReady}><FileText size={16} /> 生成专业报告</button>
      </div>
    </header>
  );
}
