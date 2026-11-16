import type { EmgPayload } from "../contracts/realtimeMessages";
import { semgDisplayLabels } from "../config/sensorTopology";

const channels: Array<keyof EmgPayload> = ["CH1", "CH2", "CH3", "CH4"];

export function MuscleActivationPanel({ emg }: { emg: EmgPayload | null }) {
  return (
    <section className="panel">
      <h2>肌肉激活<span>表面肌电归一化活动强度</span></h2>
      <div className="muscle-list">
        {channels.map((channel) => {
          const item = emg?.[channel];
          const value = Math.round((item?.activation ?? 0) * 100);
          return (
            <div className="muscle-row" key={channel}>
              <div><strong>{semgDisplayLabels[channel]}</strong><span>{channel} 路 信号质量 {item?.quality ?? 0}%</span></div>
              <b>{value}%</b>
              <div className="mini-bar"><i className={item?.primary ? "active" : ""} style={{ width: `${value}%` }} /></div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
