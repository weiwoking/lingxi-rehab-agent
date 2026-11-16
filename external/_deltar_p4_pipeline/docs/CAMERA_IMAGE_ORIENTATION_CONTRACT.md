# Camera Image Orientation Contract

Stage 7A 及后续阶段只接受 P4 最终生产输出：`width=640`、`height=360`、物理横屏、场景正立、`software_rotation_degrees=0`。P4 输入像素为 `RGB565_LE`，PC 解码颜色语义为 RGB。不得在 PC 标定或生产链默认增加固定 90° 旋转、crop 或二次 resize。

Camera 必须先物理转正，再以最终 ISP、曝光链和 640×360 输出采集标定图。`camera-orientation-check` 只能可靠检查像素分辨率与 EXIF orientation；它不能自动判断房间、文字或人物是否正立。因此 `scene_upright_confirmed` 只能来自用户通过 `--confirm-upright` 做出的显式人工确认。

冻结清单：

- 画面为横向 640×360；
- 房间竖线、文字和非对称参照物均正立且未镜像；
- EXIF orientation 为 1；
- P4 与 PC 均无软件旋转；
- 这是最终生产像素，不是 1280×720 source 或预处理副本。

标定器只在 orientation manifest 同时满足 `software_rotation_degrees=0` 和 `scene_upright_confirmed=true` 时运行。manifest 绑定确认图的 SHA-256；Camera 物理方向、镜头或 ISP 改动后必须重新确认并重新标定。
