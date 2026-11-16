# ChArUco Capture Contract

默认板为 OpenCV ChArUco、`DICT_5X5_100`、7×5 个棋盘方格、方格 25 mm、marker 18 mm。OpenCV 的 `squares_x/squares_y` 表示横向/纵向的棋盘方格数，不是内角点数；7×5 板有 `(7-1)×(5-1)=24` 个 ChArUco 内角点。选择 25 mm 而不是 30 mm，是因为 7×30 mm 正好占满 A4 的 210 mm 宽度，没有可靠打印边距；7×25 mm 可保留边距。

板必须按 A4、100%/actual size 打印，关闭“适合页面”、缩放和无边距放大。打印后测量一个方格边长及完整有效板宽高，把实测值写回 `board_manifest.json` 并设置 `print_measurement.confirmed=true`。工具默认容许 1% 的方格误差；未确认或超差时 fail-closed。

有效数据集包含 25–40 张最终 640×360 图，固定 Camera、镜头、焦点、ISP、曝光链和物理方向。覆盖画面中心、四角和边缘；近、中、远距离；绕水平轴和垂直轴的正负倾斜；多个 roll。避免全部正对、同距离、中心小覆盖、运动模糊、严重过曝、强反射或板面弯曲。

默认按 seed 与文件名哈希做确定性 80% fit / 20% held-out。held-out 图绝不参与拟合，只用已拟合内参运行 PnP 和重投影残差验证。

每图记录文件名、SHA-256、宽高、已知时间戳、来源、接受/拒绝、marker/角点数、覆盖率和清晰度。混合分辨率、竖图、角点不足或不能解码的图会被拒绝并写入质量报告。
