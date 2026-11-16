# Blender Rig Plan

The downloaded `body.glb` is a static anatomical mesh and does not provide a usable patient motion rig. A future Blender step is required before mesh deformation can replace the current kinematic skeleton overlay.

Minimum rigging steps:

1. Import `frontend/public/models/body.glb` into Blender with DRACO support.
2. Isolate the right shoulder, upper arm, forearm, wrist, hand, clavicle, and scapula mesh groups listed in `frontend/src/twin/modelManifest.ts`.
3. Create a lightweight clinical visualization armature: trunk, scapula/clavicle, shoulder, upper arm, elbow, forearm, wrist, palm.
4. Parent or weight only the mapped right-side meshes; keep unmapped anatomy hidden or non-deforming.
5. Export a derived GLB with explicit skins and named bones that can be consumed by `JointRigAdapter`.
6. Preserve CC BY-SA attribution for the derived model.

This is not required for the current delivery because the application preserves the existing reference kinematic skeleton fallback.

## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
