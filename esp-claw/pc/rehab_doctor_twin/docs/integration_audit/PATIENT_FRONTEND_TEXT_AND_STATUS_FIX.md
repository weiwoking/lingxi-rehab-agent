# Patient Frontend Text And Status Fix

## Scope

This note covers only the patient-facing frontend text, JSX/TypeScript syntax repair, and device status display semantics in the doctor PC workspace.

## What Was Damaged

`frontend/src/pages/LiveMonitorPage.tsx` contained real text corruption, not just a UTF-8 BOM. Several Chinese strings were mojibake, and multiple string literals / JSX tags were left unclosed. The same patient/device display chain also had damaged strings in the device status card, device status page, device summary, sensor topology labels, and patient API error text.

## Why This Was Not A BOM Issue

A BOM check can only remove a leading UTF-8 marker. The observed failures were embedded damaged strings such as broken labels, bad `?/p>` text, and unterminated TypeScript/JSX string literals inside the file body. The fix therefore replaced only the affected frontend text and syntax manually, without running broad encoding conversion.

## Text And Syntax Repaired

- Patient page title: `患者训练端`, `上肢康复训练`, `返回角色入口`.
- Runtime labels: `数据状态`, `数据源 IP`, `接收频率`, `最后数据`, `有效 / 无效 / 重复`, `传感器在线`.
- Training controls: `正在生成报告…`, `结束训练`, `开始训练`, `当前训练时长`, `本轮有效行数`.
- Report area: `历史训练报告`, `暂无正式训练报告`, `训练报告`, `完成有效训练后可在此查看正式报告`.
- Helper output: `正在接收真实数据`, `数据已中断`, `服务已就绪，等待真实数据`, `暂无`, `暂无数据`.
- JSX stale warning was restored to a complete `<p className="stream-warning">...</p>` block.
- File-ending whitespace was corrected so `git diff --check` no longer reports a `new blank line at EOF` for `LiveMonitorPage.tsx`.

## Node Status Display

Frontend display now treats backend `connection_status` as authoritative:

- `normal` -> `正常`
- `stale` -> `数据中断`
- `unknown` -> `未知`

Legacy messages without `connection_status` fall back to `device.online === true ? normal : unknown`. The card header, status dot, and detail `运行状态` use the same mapping. `unknown` is not displayed as offline, and `stale` is not displayed with the normal state style.

## sEMG Compatibility

The frontend now treats `semg` and `sEMG` as the same sensor type in the device card, device status page, and device summary calculation by using lowercase comparison.

## What Was Not Changed

This round did not modify backend scoring flow, compact/full score-result separation, UDP protocol, score service, score algorithm, P4/S3, hardware chain, Agent trace logic, report data structure, action reconstruction, or muscle highlighting.

## Static Review

Static text scan results for the touched frontend files:

- damaged text markers: `0`
- Unicode private-use characters: `0`
- patient task-forbidden display terms in `LiveMonitorPage.tsx`: `0`
- forbidden code patterns such as strict `device.sensor_type === "sEMG"` and Node rx recalculation: `0`

`git diff --check` was run only as static whitespace review. It reported only LF/CRLF warnings and no `LiveMonitorPage.tsx` EOF blank-line issue.

## Still Needs User Runtime Verification

The user still needs to start the local stack and verify the patient page visually, because this round did not run frontend build, tests, browser, HTTP, WebSocket, UDP, or hardware validation.
