# Right Joint Pivot Audit

Generated: 2026-07-22T10:15:03.843822+00:00

## Runtime Pivot Source

`frontend/src/twin/SegmentRigAdapter.ts` computes fixed pivots once after model load from mapped right humerus, radius, ulna and hand Mesh bounding boxes. No per-frame re-parenting and no per-frame full model bounding-box recalculation are used.

## Hierarchy

```text
rightRoot
└─ rightShoulderPivot
   ├─ rightClavicleGroup
   ├─ rightScapulaGroup
   └─ rightUpperArmGroup
      ├─ rightHumerusGroup
      ├─ rightUpperArmMuscleGroup
      └─ rightElbowPivot
         └─ rightForearmGroup
            ├─ rightRadiusGroup
            ├─ rightUlnaGroup
            ├─ rightForearmMuscleGroup
            └─ rightWristPivot
               └─ rightHandGroup
                  ├─ rightHandBoneGroup
                  └─ rightHandSoftTissueGroup
```

## Pivot Semantics

- Shoulder pivot: upper end of right humerus Mesh.
- Elbow pivot: midpoint between right humerus distal bound and right forearm proximal bound.
- Wrist pivot: midpoint between right forearm distal bound and right hand proximal bound.
- Right hand marker: child of `rightHandGroup`, used as the actual trajectory source.

PIVOT_MAPPING_SOURCE=RIGHT_BONE_MESH_BOUNDING_BOXES
NO_RUNTIME_REPARENT=true
