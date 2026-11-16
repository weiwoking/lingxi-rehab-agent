import { FileText } from "lucide-react";
import type { ReactNode } from "react";

type EmptyStateSize = "compact" | "standard" | "large";

export function EmptyState({
  title,
  description,
  icon,
  size = "standard"
}: {
  title: string;
  description?: string;
  icon?: ReactNode;
  size?: EmptyStateSize;
}) {
  return (
    <div className={`empty-state-panel ${size}`}>
      <div>{icon ?? <FileText size={24} />}</div>
      <strong>{title}</strong>
      {description && <p>{description}</p>}
    </div>
  );
}
