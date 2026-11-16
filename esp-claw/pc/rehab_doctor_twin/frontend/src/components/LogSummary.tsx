export function LogSummary({ count, type, paused }: { count: number; type: string; paused: boolean }) {
  return (
    <section className="log-summary">
      <div><span>当前类型</span><strong>{type}</strong></div>
      <div><span>页面记录</span><strong>{count}</strong></div>
      <div><span>刷新状态</span><strong>{paused ? "已暂停" : "2秒刷新"}</strong></div>
      <div><span>记录状态</span><strong>{count > 0 ? "已载入" : "等待数据"}</strong></div>
    </section>
  );
}
