# PC-only 正式运行结构

## 当前源码事实

正式 `pc_local` 链路为：

```text
未修改 P4
  -> UDP 6006，86 列融合 CSV
  -> 公共后端 8001 内部 PcUdpIngestService
  -> /ws/live 持续显示真实 IMU+sEMG
  -> 患者人工开始/结束训练
  -> 冻结 DATA_SEQxxxxxxxxxx.csv
  -> 本地评分服务 8020 Job API
  -> 患者与医生读取同一 session/report
```

固定端口：

| 入口 | 地址 |
|---|---|
| 单一前端 | `127.0.0.1:5173` |
| 公共后端 | `0.0.0.0:8001` |
| 评分服务 | `0.0.0.0:8020` |
| 设备数据入口 | `0.0.0.0:6006/UDP` |

评分服务不接收实时 UDP。后端不读取 P4 SD 卡，不启动视觉服务。`device` 模式保留原行为；只有 `pc_local` 模式创建 UDP 接收器，不依赖 `P4_BASE_URL`。

## 状态边界

数据流状态为 `WAITING / RECEIVING / STALE`（API 使用小写值）。训练状态为 `IDLE / RECORDING / FINALIZING / SCORING / COMPLETED / INVALID / FAILED`。

数据到达不创建会话。断流只改变数据流状态，不结束训练。患者点击开始后才缓存，点击结束后停止缓存，但实时图表继续接收后续真实数据。

## 已废弃方案

`POST /api/pc-stream/frames`、HTTP JSON frame envelope、三帧自动开始、断流自动正常结束、医生控制训练和患者选择任务均不再属于当前源码。
