# Left Bone Mesh Audit

Generated at: 2026-07-22T09:25:22.725Z

## Summary

- MODEL_BONE_COUNT=0
- MODEL_SKINNED_MESH_COUNT=0
- ANATOMICAL_BONES_ARE_MESH=true
- Selection rule: anatomical name/metadata candidates + negative X model-space center + no right-side fallback.

## Left Bone Mesh Sets

- clavicle (1): Clavicle.001
- scapula (1): Scapula.003
- humerus (1): Humerus.003
- radius (1): Radius.003
- ulna (1): Ulna.003
- handBones (23): Distal phalanx of first finger of hand.001, Distal phalanx of second finger of hand.001, Distal phalanx of third finger of hand.001, Distal phalanx of fourth finger of hand.001, Distal phalanx of fifth finger of hand.001, Middle phalanx of second finger of hand.001, Middle phalanx of third finger of hand.001, Middle phalanx of fourth finger of hand.001, Middle phalanx of fifth finger of hand.001, Proximal phalanx of first finger of hand.001, Proximal phalanx of second finger of hand.001, Proximal phalanx of third finger of hand.001, Proximal phalanx of fourth finger of hand.001, Proximal phalanx of fifth finger of hand.001, First metacarpal bone.001, Second metacarpal bone.001, Third metacarpal bone.001, Fourth metacarpal bone.001, Fifth metacarpal bone.001, Capitate bone.001, Hamate bone.001, Trapezium bone.001, Trapezoid bone.001

## Notes

- The model exposes static anatomical structures as ordinary Mesh nodes; node.type === Bone and isSkinnedMesh are not valid primary classifiers.
- Runtime code must keep the selected left clavicle, scapula, humerus, radius, ulna, carpal/metacarpal/phalanx meshes visible in bone mode.
- Muscle+bone overlay should keep bone meshes opaque ivory while muscles are semi-transparent.

Full record: `frontend/public/models/left_bone_mesh_audit.json`.
