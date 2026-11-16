# 阿里云视觉解释器 V2

该版本在原有 YOLO + ToF 距离融合基础上，加入了现成的 OpenCV 针孔相机反投影算法，用于计算：

- `object_height_cm`
- `bottom_height_from_ground_cm`

## 算法链路

```text
检测框顶部/底部像素
→ cv2.undistortPoints 去畸变
→ 像素反投影成相机射线
→ 使用 ToF 距离确定物体深度平面
→ 顶部/底部射线与深度平面求交
→ 相机坐标变换到地面坐标
→ 计算高度与离地高度
```

这不是用 YOLO 估计高度，也不是使用 solvePnP。YOLO 只提供检测框；几何测量使用标准 RGB-D 反投影。

## 保护规则

以下参数没有配置时：

```text
camera.calibration_ready = false
camera_mount.calibration_ready = false
```

服务器只返回距离，高度字段为 `null`，并返回具体原因。

## 需要填写的云端固定参数

编辑：

```bash
nano /opt/rehab/vision_interpreter/config/vision_config.json
```

填入实际标定值：

```json
"camera": {
  "calibration_ready": true,
  "fx": 0.0,
  "fy": 0.0,
  "cx": 0.0,
  "cy": 0.0,
  "distortion": [0.0, 0.0, 0.0, 0.0, 0.0]
},
"camera_mount": {
  "calibration_ready": true,
  "height_from_ground_cm": 0.0,
  "pitch_deg": 0.0,
  "roll_deg": 0.0,
  "yaw_deg": 0.0
}
```

## 距离定义

`geometry.distance_definition` 可选：

- `camera_z`：输入就是相机 Z 深度；
- `camera_radial`：输入是从相机光心到物体的径向距离；
- `tof_radial_aligned`：输入是从 ToF 原点到物体的径向距离，并用 ToF 相对相机平移做射线—球面求交修正。

当前默认：

```json
"distance_definition": "camera_radial"
```

## 部署

```bash
cd /opt/rehab
rm -rf vision_interpreter
unzip vision_interpreter_geometry_v2.zip
mv vision_interpreter_geometry_v2 vision_interpreter
cd vision_interpreter
bash deploy/install.sh
```

检查：

```bash
systemctl status vision-interpreter --no-pager
curl -s http://127.0.0.1:8010/api/v1/vision/health | python3 -m json.tool
```

配置标定参数后：

```bash
systemctl restart vision-interpreter
```


## 当前演示占位参数

此压缩包已直接启用高度计算，使用以下临时参数：

```json
"camera": {
  "calibration_ready": true,
  "fx": 500.0,
  "fy": 500.0,
  "cx": 320.0,
  "cy": 180.0,
  "distortion": [0, 0, 0, 0, 0]
},
"camera_mount": {
  "calibration_ready": true,
  "height_from_ground_cm": 80.0,
  "pitch_deg": 0.0,
  "roll_deg": 0.0,
  "yaw_deg": 0.0
},
"tof_to_camera": {
  "calibration_ready": true,
  "translation_cm": [0, 0, 0],
  "rotation_deg": [0, 0, 0]
},
"geometry": {
  "distance_definition": "tof_radial_aligned"
}
```

这些参数仅用于让接口输出高度和离地高度，不能用于精确测量。
