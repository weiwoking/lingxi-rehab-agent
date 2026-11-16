# Model Integration

## Runtime Paths

- Model: `frontend/public/models/body.glb`
- DRACO decoder: `frontend/public/draco/`
- Audit JSON: `frontend/public/models/body_model_audit.json`
- Audit report: `docs/BODY_MODEL_AUDIT.md`

The application loads `/models/body.glb` through `GLTFLoader` and `DRACOLoader`. If the model or WebGL pipeline fails, the existing kinematic reference skeleton and trajectory overlay remain available and the UI reports `Anatomical model integration pending.`

## Model Loader

`frontend/src/twin/AnatomyModelLoader.ts`:

- Loads the official Body Anatomy 3D Viewer `body.glb`.
- Uses the DRACO decoder path `/draco/`.
- Clones mesh materials once during load.
- Hides unmapped anatomy by default.
- Maps matched mesh names through `frontend/src/twin/modelManifest.ts`.
- Records missing business anatomy IDs for diagnostics.

## Business Anatomy Mapping

`frontend/src/twin/modelManifest.ts` maps only structures needed by the current doctor twin demo:

- Right shoulder and upper limb muscles: deltoid, biceps brachii, triceps brachii, brachialis, forearm flexor group, forearm extensor group.
- Right shoulder and upper limb bones: scapula, clavicle, humerus, radius, ulna, hand bones.
- sEMG mapping:
  - CH1 -> right biceps brachii
  - CH2 -> right triceps brachii
  - CH3 -> right forearm flexor group
  - CH4 -> right forearm extensor group

The UI does not infer anatomy by fuzzy substring matching at runtime. It uses explicit model manifest names from the audited GLB.

## Display Layers

`AnatomyScene` supports:

- `muscle_bone`: default right upper limb focus layer.
- `muscle`: mapped muscles only.
- `bone`: mapped bones only.
- `all`: all meshes for inspection.

Camera presets include front, side, shoulder, forearm/hand, and upper body.

## Current Limitation

The downloaded GLB is a static anatomical mesh set. It has no usable glTF skin, bone hierarchy, or animation track for mesh deformation. Motion remains a reference kinematic skeleton/trajectory overlay until a Blender rigged derivative is produced. See `docs/BLENDER_RIG_PLAN.md`.

## 国赛演示收尾说明（2026-07-22）

本版本新增医生首页一键国赛演示入口，实时页采用 `body.glb` + 分段解剖网格运动映射。姿态由4个IMU相对四元数映射，sEMG固定为 CH1/CH2/CH3/CH4 四路业务肌肉归一化活动度。演示数据保持 `DEMO DATA` 标识，不代表真实临床结果。
