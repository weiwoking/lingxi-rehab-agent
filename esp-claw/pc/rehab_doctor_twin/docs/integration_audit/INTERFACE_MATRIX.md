# Interface Matrix

审计时间：2026-07-23

## PC 视觉服务 `:8000`

| 接口 | 方法 | 请求 | 响应/合同 | 消费方 |
| --- | --- | --- | --- | --- |
| `/api/v1/vision/health` | GET | 无 | `status`, `model_ready`, model/config info | 启动器、本机诊断 |
| `/api/v1/vision/fuse` | POST multipart | `image` RGB565；`tof` JSON `TofFrame` | 视觉/ToF 融合结果 | ESP perception fusion |
| `/api/v1/person-follow/detect` | POST multipart | `image` JPEG 640x360；`metadata` `person_follow_request.v1` | `person_follow.v1` 或 `person_follow_error.v1` | ESP `rehab_person_follow` |
| `/person-follow-view` | GET | 无 | HTML viewer | 调试查看 |
| `/api/v1/person-follow/latest/*.jpg|json` | GET | 无 | 最近一帧原图/标注/metadata | 调试查看 |

## PC 评分服务 `:8020`

| 接口 | 方法 | 请求 | 响应/合同 | 访问策略 |
| --- | --- | --- | --- | --- |
| `/api/v1/score/health` | GET | 无 | `service=rehab_score_service`, `status=ready|degraded`, assets/runtime info | loopback/RFC1918 |
| `/api/v1/score/jobs` | POST | JSON `task.json`，headers `X-Session-Id`, `X-CSV-Size`, `X-CSV-SHA256` | `rehab_score_job_response_v1` | loopback/RFC1918 |
| `/api/v1/score/jobs/{job_id}/csv` | PUT | `text/csv` frozen 86 列，Content-Length 必填 | `rehab_score_job_status_v1` | loopback/RFC1918 |
| `/api/v1/score/jobs/{job_id}` | GET | job id | job state | loopback/RFC1918 |
| `/api/v1/score/jobs/{job_id}/result` | GET | job id | compact `rehab_score_result_v1` | loopback/RFC1918 |
| `/api/v1/score/jobs/{job_id}/retry` | POST | job id | retry accepted | loopback/RFC1918 |
| `/score_trial` | POST | 单次已提取特征 JSON | 旧 public score result，保留 `ROM_score` | POST loopback only |
| `/process_task_directory*` | POST | 本地任务目录路径 | full score result 或 NDJSON | POST loopback only |

冻结结果字段必须保留：`schema_version`, `session_id`, `task_name`, `task_number`, `valid`, `invalid_reason`, `average_score`, `max_score`, `min_score`, `repetition_count`, `target_repetition_count`, `consistency_score`, `data_quality_level`, `warnings`, `compensation_detected`, `compensation_action_count`, `score_version`, `model_version`, `completed_at`, `pc_job_id`。

## ESP Capability

| Capability | 输入 | 输出重点 | 规则 |
| --- | --- | --- | --- |
| `rehab_training_start_selected` | `{}` | accepted/code/session/task | 日常开始训练只走 selected task |
| `rehab_training_start` | `{task_name}` | start result | 调试/受控调用保留 |
| `rehab_training_stop` | `{reason}` | stop result | 停止当前唯一 Recorder，不带 session id |
| `rehab_training_status` | `{}` | current session、last completed、传感器质量 | 当前状态只信本轮 tool_result |
| `rehab_score_query` | `{session_id?}` | persisted score/status | 只读 SD，不重新提交、不重算 |
| `rehab_fatigue_status` | `{}` | fatigue gate/status | 不读取评分历史 |
| `rehab_system_status` | `{}` | mode/state/components | `real` 只是模式标志 |
| `rehab_demo_mode_set` | `{mode}` | changed/generation | 不初始化硬件或云 |
| `person_follow_start/stop/status` | `{}` | person-follow worker 状态 | Agent 不逐帧控制 |

## 治疗师 UI Backend `:8000`

| 接口 | 方法 | 用途 | 当前数据源 |
| --- | --- | --- | --- |
| `/health` | GET | gateway health | demo store |
| `/api/demo/scenarios` | GET | 演示场景列表 | `demo/` |
| `/api/competition-demo/start`, `/api/demo/start`, `/api/demo/reset` | POST | 启动/重置演示 | demo engine |
| `/api/state/current` | GET | 当前快照 | session store |
| `/api/model/status` | GET | GLB/DRACO 状态 | frontend public assets |
| `/api/patients*` | GET | 患者、会话、报告、日志、导出 | backend data/runtime logs |
| `/api/report/current` | GET | 当前报告 | session store |
| `/ws/live` | WebSocket | 实时消息 | demo engine；未来 Adapter |

WebSocket 消息类型保持：`rehab.state`, `rehab.telemetry`, `rehab.emg`, `rehab.score`, `rehab.alert`, `rehab.event`, `rehab.report_ready`, `device.status`。前端按 `session_id` 和 `seq` 过滤迟到、重复、乱序消息。

## 接口兼容风险

1. 视觉 8000 和医生后端 8000 冲突，真实集成前必须做启动编排。
2. 医生端 `rehab.score.payload.score.subscores.ROM_score` 大小写不能改。
3. ESP 评分桥严格校验 PC result 身份和字段，不能给“近似字段名”。
4. 旧评分 POST 接口只允许 loopback，不能作为 P4 局域网入口替代 job API。
5. 追人 request/response schema 是 `person_follow_request.v1` / `person_follow.v1`，不能混用 `/api/v1/vision/fuse` 响应。
