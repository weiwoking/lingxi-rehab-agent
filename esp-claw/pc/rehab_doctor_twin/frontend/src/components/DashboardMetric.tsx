import type { ReactNode } from "react";

export function DashboardMetric({ label, value, detail }: { label: string; value: ReactNode; detail: string }) {
  return (
    <div className="dashboard-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

