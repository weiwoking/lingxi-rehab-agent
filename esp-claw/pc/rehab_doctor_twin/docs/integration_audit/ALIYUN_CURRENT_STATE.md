# Aliyun Current State

审计时间：2026-07-23

## 当前事实

| 区域 | 发现 | 结论 |
| --- | --- | --- |
| ESP-Claw `app_config` | 存在通用 LLM backend 配置，历史 preset `qwen`/`qwen_compatible` 指向 `https://dashscope.aliyuncs.com/compatible-mode/v1` | 阿里云百炼/DashScope 可作为通用 LLM 后端 |
| HTTP 设置 UI | 前端文案/外链包含 Aliyun Bailian | 只是配置入口，不是康复数据同步服务 |
| PC 视觉服务 | FastAPI title 为 `Aliyun Vision Interpreter` | 名称保留，不代表启动时访问阿里云 |
| PC 评分服务 | `cloud_interface` 命名较多 | 这里的 cloud 更像接口层命名，本机服务运行不依赖阿里云 |
| 治疗师 PC UI | README 明确“无真实硬件、无阿里云、无真实评分服务时稳定运行”并列出“未接阿里云” | 当前医生端没有阿里云集成 |
| 本轮环境恢复 | 未读取密钥、未登录、未请求阿里云 | 云侧状态未被改变 |

## 未发现的内容

| 项 | 结果 |
| --- | --- |
| 康复训练 Session 上传到阿里云的独立服务 | 未发现 |
| 医生端实时 WebSocket 通过阿里云转发 | 未发现 |
| 评分结果写入阿里云数据库/对象存储 | 未发现 |
| P4 scan result 经阿里云 pipeline 处理的本地 launcher | 未发现；现有 scan README 指向本地 `:8030` 服务 |
| 本机阿里云康复业务密钥配置 | 未发现，也未尝试访问 |

## 准确表述

当前可以说：ESP-Claw 具备通用 LLM 后端配置能力，可配置到 DashScope；医生端和康复闭环当前未接阿里云；PC 视觉/评分服务可在本机离线局域网方式启动。

当前不能说：阿里云已经承担康复数据存储、医生端同步、评分计算、真实设备转发或患者档案管理。
