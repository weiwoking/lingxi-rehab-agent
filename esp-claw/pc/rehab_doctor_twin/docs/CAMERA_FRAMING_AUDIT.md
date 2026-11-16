# Camera Framing Audit

Generated: 2026-07-22T10:15:03.843822+00:00

## Presets

The live monitor keeps the existing presets: front, side, shoulder, forearm/hand and complete right upper limb. The default load preset is `upper_body` so the right shoulder, arm, forearm, hand and motion trajectory fit inside the first view.

## Stability

The camera is not auto-followed per frame. Preset changes happen only from user controls or initial model load. This avoids jitter during 10-50 Hz sensor updates.

DEFAULT_CAMERA_PRESET=upper_body
CAMERA_AUTO_FOLLOW_PER_FRAME=false
RIGHT_FULL_ARM_FRAMING_TARGET=true
