# Drink Action Presentation V1

## Boundary

The current reconstruction page uses a fixed drink-water action presentation. It is not a real IMU reconstruction algorithm and does not write CSV, score data, prediction samples, or formal sessions.

## Reused Model

The implementation continues to use the existing Three.js anatomy scene, model loading, segment rig adapter, and muscle highlighter. No new external model asset is introduced.

## Scene Objects

The scene adds lightweight Three.js geometry for:

- chair reference
- table surface
- water cup

These objects are visual references for the playback page only.

## Action Phases

One drink-water repetition is represented as nine phases:

1. DANGLE_START
2. REACH
3. GRASP
4. LIFT
5. DRINK_HOLD
6. LOWER
7. RELEASE
8. RETURN_DANGLE
9. DANGLE_END

The base phase duration totals about 6.70 seconds before minor deterministic per-record variation.

## Stops And Cup Logic

The playback includes visible pauses at the initial dangle, grasp, drink hold, release, and final dangle states. The cup starts on the table, moves with the hand through lift and drink hold, returns to the table during lower/release, and remains on the table during return dangle.

## Muscle Highlight

The playback changes material highlight intensity for four existing right-side muscle targets:

- finger extensors
- biceps brachii
- triceps brachii
- extensor carpi ulnaris

No fake sEMG or IMU numeric values are generated.

## Deterministic Differences

`训练记录 01` uses four repetitions with slightly slower, less polished motion.

`训练记录 02` uses five repetitions with slightly smoother and more complete motion.

The variation is deterministic from record id and repetition index, not from per-frame random values.
