import type { AlertPayload } from "../contracts/realtimeMessages";

export function AlertBanner({ alert }: { alert: AlertPayload | null }) {
  if (!alert) return null;
  return (
    <div className={`alert-banner ${alert.level}`}>
      <strong>{alert.title}</strong>
      <span>{alert.message}</span>
    </div>
  );
}

