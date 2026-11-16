# WT901 IMU gravity fallback — source-first feasibility

`IMU_FALLBACK = CONDITIONAL`

## What exists

Local project sources confirm that `node_left` and `node_right` each use two WT901C-TTL modules. The node-right README identifies two WT901 units and a 100 Hz sensor packet. `main/main.c` states that raw accelerometer fields retain gravity and derives a gravity projection from roll/pitch. The parser handles the WT901 standard acceleration (`0x51`) and angle (`0x53`) frames. The task-supplied module material quotes approximately 0.2° static inclination accuracy.

These are patient/sensor-node assets, not a Camera IMU. No S3 firmware or repository is modified in this work.

## Can one be temporarily used?

Mechanically and electrically, a spare WT901 could provide a static gravity observation when rigidly attached to the Camera+ToF enclosure. Borrowing a module already installed in a formal node is not recommended: it would disturb the S3 chain and create reassembly/revalidation burden. Prefer an unassigned spare if the image route later fails.

## Required axis relationship

The IMU reports gravity in IMU coordinates. Camera gravity requires

`u_up_camera = R_camera_from_imu @ u_up_imu`.

Therefore `R_camera_from_imu` must be known. Acceptable ways to establish it are:

- a mechanically known mounting face whose normal and in-plane direction are tied to camera axes by enclosure CAD/jig geometry; or
- a separate multi-orientation cross-calibration/hand-eye procedure while the IMU is rigidly attached.

A single static reading with an arbitrary attachment does not determine the IMU-to-camera rotation. It maps only one gravity vector and leaves rotation around gravity unconstrained; more importantly, without a known mechanical relationship it does not tell which IMU direction corresponds to camera axes. Adhesive placement by eye is not sufficient for a sub-degree authority.

## Impact on the S3 chain

No impact occurs if a spare module and an independent readout are used. Removing a production-node IMU would make that node incomplete and require wiring, mounting, identity, and end-to-end packet validation afterward. This fallback must not modify S3 firmware and is not the preferred next step.

Conclusion: available only conditionally, behind the image-based W04G route and only with a spare plus a defensible rigid axis transform.
