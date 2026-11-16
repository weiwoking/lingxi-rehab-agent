# Frozen forward geometry and metrics

令 frozen ToF unit ray 为 `r_t`，其 z 分量为 `r_z`；frozen transform 为 `p_c=R p_t+t`；Camera ChArUco reference plane 为 `nᵀp_c+d=0`，其中 `n` 是单位法向量。

## A. Axial-z residual

冻结 `axial_z` 语义下，测量值 `z_m` 重建为：

`p_t = r_t * (z_m/r_z)`。

ray 与 reference plane 的预期 axial-z 为：

`z_e = -(nᵀt+d) * r_z / (nᵀRr_t)`。

因此：

`axial_depth_residual_mm = z_m-z_e`。

## B. Orthogonal point-to-plane residual

用测量 `z_m` 重建并变换：

`p_c = R[r_t*(z_m/r_z)]+t`，

`point_to_plane_residual_mm = nᵀp_c+d`。

由于 `n` 归一化，这是 signed orthogonal distance，作为主指标；它在不同 ray/plane 入射角之间具有直接几何可比性。axial residual 是 secondary metric。两者随 incidence angle 有比例差，绝不共享一个阈值。

## 分层与权重

- Layer A：透明 classifier 标出的 core reference-plane observations，用于泛化精度。
- Layer B：所有 broad-valid observations，未裁剪，用于 runtime 宽松策略的长尾诊断。
- Layer C：所有 strict-valid observations，未裁剪，仅作对照。
- observation-level metrics 保留全部 frame-zone 样本；per-pose metric 先在每个 11-frame physical scan 内计算，再对 pose-level distribution 等权汇总，避免一组因 11 帧获得 11 倍物理姿态权重。
- Stage2B heldout 数值只生成 delta/ratio 与诊断标签，不作为自动 PASS gate。最终 GO/NO-GO 属于 GPT + 用户。
