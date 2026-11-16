import { ClipboardList, Database, FileClock, Home, LayoutDashboard, Stethoscope } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

const links = [
  { path: "overview", label: "总览", icon: LayoutDashboard },
  { path: "logs", label: "数据日志", icon: Database },
  { path: "reconstruction", label: "动作还原", icon: FileClock },
  { path: "sessions", label: "Session记录", icon: ClipboardList },
  { path: "reports", label: "训练报告", icon: ClipboardList },
  { path: "prescription", label: "任务库", icon: Stethoscope }
];

export function PatientWorkspaceLayout() {
  return (
    <div className="workspace-shell">
      <header className="workspace-nav formal-nav">
        <div className="workspace-brand">
          <strong>灵犀智伴</strong>
          <span>医生工作台</span>
        </div>
        <div className="workspace-patient">
          <strong>P-0217</strong>
          <span>近期训练记录</span>
        </div>
        <nav>
          {links.map(({ path, label, icon: Icon }) => (
            <NavLink key={path} to={`/doctor/${path}`}>
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>
        <NavLink className="role-return" to="/">
          <Home size={15} />
          角色入口
        </NavLink>
      </header>
      <Outlet />
    </div>
  );
}
