---
{
  "name": "rehab-task-decision",
  "description": "使用已持久化的最新成功场景正式选择康复任务；处理开始、重新决策和正式决策状态查询，不执行环境扫描。",
  "metadata": {
    "cap_groups": ["rehab_task_decision_coordinator"],
    "manage_mode": "readonly",
    "category": ["utility"],
    "peripherals": [],
    "tags": ["rehab", "task-decision", "deepseek"]
  }
}
---

# Rehab Task Decision

## Execute Contract

1. 正常输入 `rehab_task_decision_llm_input.v1` 是本轮完整 model-facing projection；full `rehab_task_decision_input.v4` 只由 P4 内部保存和验证，DeepSeek 不重建 full v4。只有 adapter 明确失败并实际返回 schema=v4 时才进入一次 recovery fallback。
2. 直接信任 compact 投影的 machine facts；不重新扫描、resolve target、验证 SHA/generation、推导 hard gate 或重排 candidates。
3. P4 preparation 只前移 frozen difficulty/compensation/frequency/perception/fixed_total；DeepSeek 仍决定患者语义、medical block、candidate-scoped advice、winner/tie、reason/summary 和最终 commit。
4. 按下面骨架单遍执行。No new evidence = no second pass.
5. 只调用 `rehab_task_decision_commit` 提交完整 `rehab_task_decision_output.v4`。

## One-pass Reasoning Protocol

```text
input=confirmed

candidate_ledger:
ref | availability | evidence

business:
ref=<short judgement>; ...

prepared_score_ledger:
ref | advice | final_raw | score

winner_ref=<ref>

preflight=pass; commit=rehab_task_decision_commit
```

每段一次。`candidate_ledger`、business、prepared score ledger、winner、preflight 各做一次；没有新输入、明确 tool error 或 `retry_allowed=true` 时不得重读或重算。禁止无证据触发的 “Let me think / Actually / Wait / Let me reconsider / Let me double check”。reasoning 只记业务结论、advice 和最终数值，不复述 workflow、authority、schema、JSON/tool 组装、policy、公式、固定分项推导、rounding 或 tie 原理。简洁不能省略候选、medical evidence、winner 或 output 字段。

## Input

输入获取二选一：

- Context 已含完整 compact v1：记录 `input=confirmed`；不得再次 activate Skill，不调用 formal input-get 或 status。
- 尚未提供 compact：调用 `rehab_task_decision_formal_input_get` 一次且仅一次；微信 source=`wechat`，普通本地 Root source=`local_ui`。

获得 v1 后不得再次 input_get，不得调用 status，不得用聊天 history 或 memory 补写，也不得覆盖 machine facts。

Recovery only：若实际 tool/context payload 的 `schema_version=rehab_task_decision_input.v4` 且没有 `decision_preparation`，这是既有 compact-adapter fallback；按 frozen v4 `scoring_policy` 单遍计算并继续 output v4，不得伪造 preparation、再次 input_get 或把 fallback 当正常路径。

只消费 compact 的 `decision_binding`、`patient`、`history`、`candidates`、`targets`、`latest_compensation`、`decision_preparation`。Advice policy 与 tie 顺序只读 `decision_preparation.advice_policy/tie_break_candidate_refs`。Candidate refs 固定为：0 水杯横向取放训练（一级）；1 水杯拿取与饮水训练（二级）；2 水杯拿取与饮水训练（三级）；3 球类横向取放训练（一级）；4 球类横向取放训练（二级）。每项只读一次，不增删、重排或改 identity。

## Direct-use Machine Facts

直接消费且不重复证明：`candidate.availability`、`candidate.hard_gate`、`candidate.target_reference`、`targets[]` 与 `decision_preparation.candidates[].fixed_components`。

`target_reference` 只关联本轮 `targets[].target_ref`。不访问 `/sdcard/rehab/latest_successful_scene_result.json`，不寻找 raw scene objects、bbox、frame/image/warnings/distance/object height/top height。

`decision_preparation` 是 frozen deterministic result，不是 calculation hint；必须与 candidate_ref/availability 同序绑定。Input-blocked 的 fixed components 为 null；input-available 必须有四个 fixed components 与 `fixed_total`。不得从 target/history/compensation 重算、修正、覆盖或解释 difficulty、perception、frequency、compensation，也不得展开任何 component formula。

## Business Rules

P4 authority：full input、binding、patient ability parse、candidate identity/order、machine hard gate、target resolve/binding、stale check、四个 deterministic components、component/final/score consistency、output structure/type/range/ref 与 selected-task transaction。DeepSeek不得推翻 P4-blocked。

DeepSeek authority：医嘱/患者 free-text 语义、candidate-scoped advice、明确医疗 hard block、available candidate 业务比较、winner/tie、reason、summary、commit。P4 preparation 不生成 advice，不决定 medical block，不排名或选择 winner。

Scoring：对每个仍 available 的 candidate，直接复制同 ref 的 prepared `difficulty/compensation/frequency/perception`；按 `advice_policy` 和 patient free-text 独立选择整数 advice。只做一次 `final_raw=fixed_total+advice`，`score` 是 clamp 到 0..100 后的三位小数值；不要解释这一步，不要重新累计四个 frozen components。输出仍必须有五项完整 breakdown，但 DeepSeek只拥有 advice、winner/tie 与业务文字的判断权。Input-blocked 保持 `score_breakdown=null, score=0`。Winner 是最高正分 available；完全同分才按 `tie_break_candidate_refs`。

Advice：逐 available candidate 解释 `patient.condition.raw_text` 的动作、物体、否定、程度、并列、例外和作用域；正/负/neutral 使用 policy range，不硬编码历史分数。普通“少做”只影响 advice，不能 hard-block。仅原 input-available 且原文对该 candidate 有明确强医疗禁止时可改 blocked；`advice_evidence` 必须是 `patient.condition.raw_text` 连续逐字子串。P4-blocked 不解除。

## Output and Commit

Commit 参数只含 output.v4：root=`schema_version,decision_id,advice_basis,candidates,winner_ref,summary`；`candidates[5]` 固定 refs 0..4；每项=`candidate_ref,availability,advice_evidence,score_breakdown,score,reason`；available breakdown=`difficulty,advice,compensation,frequency,perception,final_raw`；blocked=null/0。不得加 `task_id/task_name` 或未知字段。Reason/summary 简洁自然，不强制 reasoning 语言。

静默 preflight 一次：字段/类型、5 refs、blocked null/0、available 的四个 prepared 值不变、advice 合法、final_raw/score 与 fixed_total 一致、最高分 winner/tie、非空 reason/summary、无未知字段。通过后立即 commit，不再检查或解释 tool arguments。P4 validator 会从 frozen full v4 独立重算全部 deterministic components、final_raw 与 score；不得把 validation 当作重新选择 winner 的授权。

Commit 成功并返回 `SELECTED` 后才声称选中。首次失败仅当 `retry_allowed=true` 时修正受影响参数一次，保持同一 frozen input；不得重新 input_get。第二次失败或 `retry_allowed=false` 立即如实失败。

## START INTENT

适用于“帮我决定一个任务”“直接决策”“开始决策”“决策任务”“重新决策”“帮我安排任务”“根据最新扫描结果决定任务”“根据最新场景推荐任务”及 `rehab-formal-ui` 内部请求。按 Input 二选一路径执行；不调用旧 run/context/rules/validate/selected_clear，不写 selected_task，不启动 2241、训练或第二个 Agent。

## STATUS INTENT

只适用于“任务决定好了吗”“现在决策到哪了”“查看决策状态”“决策结果出来了吗”等显式状态查询。调用 `rehab_task_decision_status({})`，一次且仅一次；不得调用 `rehab_task_decision_run({})`；只按快照回复并结束，当前 turn 内不得第二次调用 status。

## Scene Boundary

启动决策时不调用 `rehab_environment_scan_status({})`，也不调用 `rehab_environment_scan_start({})`。Scene unavailable 时只提示先扫描，不自动扫描；不启动 Camera、ToF、VisionPC、车辆、训练或 Session。无本轮 tool 结果不猜任务、目标、分数或状态。决策成功只表示原子提交，不表示训练已开始。
