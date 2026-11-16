# Body Model Audit

## Source

- Model: Body Anatomy 3D Viewer `public/body.glb`
- Official repository: https://github.com/hpfrei/body-anatomy-3d-viewer
- Local path: `frontend/public/models/body.glb`
- License file: `frontend/public/licenses/body-anatomy-3d-viewer-LICENSE.txt`
- Attribution: Body Anatomy 3D Viewer / Z-Anatomy project materials, CC BY-SA 4.0.

## GLB Summary

| Item | Value |
| --- | --- |
| GLB version | 2 |
| File size | 8249484 bytes |
| Nodes | 1872 |
| Meshes | 826 |
| Skins | 0 |
| Runtime bones | 0 |
| Animations | 0 |
| Materials | 2 |
| Textures | 0 |
| DRACO used | true |
| DRACO required | true |
| Primitive count | 826 |
| Static triangle estimate | not reliable because geometry is DRACO-compressed |

## Rig Assessment

This GLB is usable as a static anatomical mesh library. It does not expose a usable glTF skin, bone hierarchy, or animation track for patient-specific skeletal motion. The doctor twin therefore keeps the existing kinematic reference skeleton and maps telemetry to overlay/trajectory helpers instead of deforming the Z-Anatomy mesh.

BODY_MODEL_HAS_USABLE_RIG=false
BLENDER_RIG_REQUIRED=true

## Right Upper Limb Mapping

The production viewer intentionally maps only matched right upper limb and shoulder/chest structures. Unknown anatomy meshes are hidden by default and can be shown only with the `all` display mode.

Mapped sEMG muscles:

- CH1 -> biceps_brachii_right
- CH2 -> triceps_brachii_right
- CH3 -> forearm_flexors_right
- CH4 -> forearm_extensors_right

Mapped bones:

- clavicle_right
- hand_bones_right

Runtime-unbound structures observed in local browser validation:

- brachialis_right
- scapula_right
- humerus_right
- radius_right
- ulna_right

These names exist in the static GLB audit, but the current GLTFLoader runtime did not bind them to visible Mesh objects through the manifest. They are recorded as model-integration limitations instead of being faked with synthetic anatomy meshes.

## Audit JSON

Machine-readable details are stored in `frontend/public/models/body_model_audit.json`.

## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
