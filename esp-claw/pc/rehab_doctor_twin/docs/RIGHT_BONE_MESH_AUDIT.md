# Right Upper Limb Bone Mesh Audit

Generated: 2026-07-22T10:13:38.618699+00:00

Source model: `frontend/public/models/body.glb`

MODEL_BONE_COUNT=0
MODEL_SKINNED_MESH_COUNT=0
MODEL_ANIMATION_COUNT=0
ANATOMICAL_BONES_ARE_MESH=true

## Side Rule

right clinical upper limb uses the current visible negative-X mesh set from body.glb; no left/right mirroring is performed

The selected meshes are ordinary `THREE.Mesh` objects. The runtime does not use `node.type === "Bone"`, `object.isBone`, or `object.isSkinnedMesh` as the primary anatomical-bone selector.

## Right Bone Mesh Sets

### clavicle_right
COUNT=1
- Clavicle.001

### scapula_right
COUNT=1
- Scapula.003

### humerus_right
COUNT=1
- Humerus.003

### radius_right
COUNT=1
- Radius.003

### ulna_right
COUNT=1
- Ulna.003

### hand_bones_right
COUNT=23
- Distal phalanx of first finger of hand.001
- Distal phalanx of second finger of hand.001
- Distal phalanx of third finger of hand.001
- Distal phalanx of fourth finger of hand.001
- Distal phalanx of fifth finger of hand.001
- Middle phalanx of second finger of hand.001
- Middle phalanx of third finger of hand.001
- Middle phalanx of fourth finger of hand.001
- Middle phalanx of fifth finger of hand.001
- Proximal phalanx of first finger of hand.001
- Proximal phalanx of second finger of hand.001
- Proximal phalanx of third finger of hand.001
- Proximal phalanx of fourth finger of hand.001
- Proximal phalanx of fifth finger of hand.001
- First metacarpal bone.001
- Second metacarpal bone.001
- Third metacarpal bone.001
- Fourth metacarpal bone.001
- Fifth metacarpal bone.001
- Capitate bone.001
- Hamate bone.001
- Trapezium bone.001
- Trapezoid bone.001

