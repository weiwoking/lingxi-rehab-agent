# Calibration Profile v2

`deltar_geometry_calibration.v2` 是严格、closed 的新合同；v1 reader 与 Stage 6 历史结果保持不变。v2 在 Stage 7A 只增加 Camera intrinsics 子集和后续阶段骨架：

- `measurement_status`: `measured` 或 `unverified`；
- Camera 固定 640×360、landscape/upright、software rotation 0；
- `opencv_brown` 五参数内参；
- Camera–ToF extrinsics 与 world pose 必须为 `unresolved`；
- 即使内参 measured，`absolute_geometry_valid` 仍必须为 false；
- fit/held-out 指标和完整 provenance 必填。

provenance 绑定 board manifest、orientation manifest、accepted image hash manifest、每张接受图 SHA-256、Python/OpenCV 版本、CLI 参数、seed、fit/held-out 文件列表、生成时间、工具版本和源路径。

`v2_camera_to_v1_camera_model()` 是显式兼容 adapter，只提取 legacy `camera_model` 形状，不伪造 v1 外参、地面或 absolute geometry。v1 与 v2 不做隐式互换。
