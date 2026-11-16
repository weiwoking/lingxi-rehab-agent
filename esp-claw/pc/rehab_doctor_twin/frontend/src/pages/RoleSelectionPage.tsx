import { Activity, Stethoscope } from "lucide-react";
import { Link } from "react-router-dom";

export function RoleSelectionPage() {
  return (
    <main className="role-selection">
      <section>
        <span>ESP-Claw 康复系统</span>
        <h1>灵犀智伴脑卒中上肢康复系统</h1>
        <p>基于多路 IMU、sEMG 与本地动作评分的上肢康复训练平台。</p>
        <div className="role-tags">
          <span>多传感器融合</span>
          <span>实时训练</span>
          <span>动作评分</span>
          <span>医患双端</span>
        </div>
        <div className="role-actions">
          <Link to="/doctor">
            <Stethoscope size={24} />
            <strong>进入医生端</strong>
            <small>查看训练记录、评估报告、康复建议与动作还原。</small>
            <b>进入医生工作台</b>
          </Link>
          <Link to="/patient">
            <Activity size={24} />
            <strong>进入患者训练</strong>
            <small>查看设备数据并完成上肢康复训练。</small>
            <b>开始康复训练</b>
          </Link>
        </div>
      </section>
    </main>
  );
}
