# Detect v5-B 笔记本 Scan 8030 导入报告

## 结论

v5-B 实验候选已按 ZIP 原字节导入，Scan 8030 已切换到该模型，正式 CUDA worker 三张指定回归帧为 `3/3`，offline preflight 为 `ready=true`。Vision 8000、Scan 8030 本机 health 与 vision-console 均为 HTTP 200；当前 PC 运行态为 `READY_FOR_NEW_REAL_SCAN`。

当前 Windows 移动热点接口 `192.168.137.1` 尚未出现，因此原 Launcher 正在等待热点 health，而本机两个服务已在 `0.0.0.0:8000/8030` 运行。开始 P4 扫描前请打开原有 Windows 移动热点；Codex 未修改网络配置，也未执行 P4 操作。

## 实施结果

- 迁移 ZIP：`C:\Users\Weiwo\Downloads\rehab_scan_detect_v5b_exp_20260807_150118.zip`
- ZIP SHA-256：`9442ef66f1faed885f3764c61781fa4ed32601fcb6444dd87dc2afe3afc1c39a`
- Active Detect：`C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt`
- Active Detect SHA-256：`74ef3ac06f7b623731284dd36d983ea94207d0254525729303c1057715f613ce`
- 模型身份：Detect、COCO80 exact、sports ball class id 32、Ultralytics 8.4.93、CUDA FP32。
- 全局 conf：`0.20`；`algorithm_profile.json` 未重写且 SHA 未变。
- imgsz `640`、Detect batch `11`、device `cuda:0`、FP32、现有 NMS 均未修改。
- Segment：`C:\ec\_deltar_p4_pipeline\models\yolo11n-seg.pt`，SHA `55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152`，未修改。
- Person-Follow：`C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt`，SHA `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`，未修改。

## 三帧最小回归

输入使用正式 Scan 解码 RGB 文件，而不是带标注的 `hard_gate_reference_*` 参考图。运行参数为现有 CUDA FP32 worker、imgsz 640、conf 0.20、batch 设置 11 与现有 NMS。

| Scan / frame | sports ball confidence | bbox xyxy | 结果 |
|---|---:|---|---|
| `scan_20260806_080807_000/frame_000002` | 0.9047756791 | `[196.2885, 174.5360, 310.3211, 288.3437]` | PASS |
| `scan_20260806_083851_000/frame_000000` | 0.6571184397 | `[18.9576, 176.6523, 130.1018, 286.3927]` | PASS |
| `scan_20260806_083851_000/frame_000007` | 0.2116331458 | `[444.4984, 171.8098, 555.4258, 282.8837]` | PASS |

结果：`3/3`。

## Preflight 与运行态

- Offline preflight：`ready=true`、`status=ready`、CUDA available、`processing_mode=local_only`、`runtime_network_required=false`、网络尝试 0。
- Preflight Detect 路径/SHA 指向 v5-B；Segment 路径/SHA 保持不变。
- Vision 8000：HTTP 200 / `ok`，仍加载原 Person-Follow 模型。
- Scan 8030 本机：HTTP 200 / `ok`，health 明确回报 v5-B Detect 与原 Segment。
- vision-console：HTTP 200，`http://127.0.0.1:8030/vision-console`。
- Scan 8030 热点地址：当前不可达，因为 `192.168.137.1` 接口未启用；启动 P4 扫描前必须打开原有 Windows 移动热点。

## Gate2 sports ball

当前状态：`GATE2_SPORTS_BALL_READY`。只读核验显示 PC whitelist、selected objects、P4 package、P4 receiver 与现有球类任务映射均已支持 `sports ball`。本任务未修改 Gate2 或 P4。

## 回滚

脚本：`C:\ec\_codex_reports\detect_v5b_import_20260807_01\Restore-Pre-V5B-Scan-Detect.ps1`

默认只做 dry-run，已通过；仅 `-Apply` 才恢复本轮开始前的 Epoch10 Detect path/SHA/conf，8000 或 8030 正在监听时拒绝写入，不删除 v5-B，不触碰 Segment 或 Person-Follow。

## 新的 11 帧现场扫描

1. 笔记本插电并开启性能模式，打开原有 Windows 移动热点，确认 `192.168.137.1` 已出现；保持当前 Launcher 与双服务运行。
2. 打开 `http://127.0.0.1:8030/vision-console`，确认 Vision 8000、Scan 8030 在线。
3. 摆放最终蓝白黄排球，至少一个角度保证球体完整可见。
4. 从 P4 仅启动一次新的 11 帧扫描，等待控制台终态，不要重复触发。
5. 必须使用全新的 `scan_id`；至少一个完整球帧 Raw Detect 为 `sports ball`、confidence >= 0.20 且 bbox 合理。非球帧不应出现明显球误报，cup/bottle 与其他 COCO 对象应正常。
6. 若 Raw Detect 正确但 UI/P4 package 异常，保留新 `scan_id`，不要换模型，下一轮只读审查 Segment/merge/Gate；若 Raw Detect 全部失败，也保留新 `scan_id`，不要降阈值或立即回滚。

## 完成确认

TASK_ID：IMPORT_DETECT_V5B_AND_READY_FOR_REAL_SCAN  
迁移ZIP路径：C:\Users\Weiwo\Downloads\rehab_scan_detect_v5b_exp_20260807_150118.zip  
ZIP SHA：9442ef66f1faed885f3764c61781fa4ed32601fcb6444dd87dc2afe3afc1c39a  
ZIP验证：通过  
v5-B模型导入：是  
v5-B模型路径：C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt  
v5-B模型SHA：74ef3ac06f7b623731284dd36d983ea94207d0254525729303c1057715f613ce  
模型task：detect  
COCO80 schema：通过  
sports ball class id：32  
当前active Detect：C:\ec\_deltar_p4_pipeline\models\rehab_scan_yolo11n_coco80_volleyball_detect_v5b_exp.pt  
当前conf：0.20  
known regression笔记本：3/3  
Segment修改：否  
Person-Follow修改：否  
NMS修改：否  
imgsz修改：否  
batch/device/precision修改：否  
源码修改：否  
Gate2修改：否  
Gate2 sports ball状态：READY  
P4修改：否  
DELTAR修改：否  
geometry修改：否  
Launcher修改：否  
回滚脚本：C:\ec\_codex_reports\detect_v5b_import_20260807_01\Restore-Pre-V5B-Scan-Detect.ps1  
回滚dry-run：通过  
Vision 8000：ok（HTTP 200）  
Scan 8030：ok（本机 HTTP 200；热点接口启用后供 P4 使用）  
vision-console：ok（HTTP 200）  
P4真实扫描：未由Codex执行  
实验状态：READY_FOR_NEW_REAL_SCAN  
REPORT路径：C:\ec\_codex_reports\detect_v5b_import_20260807_01\REPORT.md  
Evidence路径：C:\Users\Weiwo\Downloads\detect_v5b_import_20260807_01_context.zip
