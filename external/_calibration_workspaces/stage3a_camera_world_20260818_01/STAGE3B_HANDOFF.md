# Stage3B Handoff（尚未开始）

Stage3A candidate 通过后才进入本阶段。本文件只是边界与验收脚手架，不代表已运行 Stage3B。

## 必须冻结

- Stage2 Final Bundle：`63c974cd2ff64dd0f135d47d8c92eb170d06658cb315563fb93588862ba8b057`
- Stage3A candidate：届时记录完整路径、SHA-256、源 pose/scan hashes。
- Camera/ToF 机械安装、相机光栅、K/D、Camera→ToF 和 Camera→World 矩阵全部固定。

## Known-height 数据

- support shelf 的真实高度。
- cup / bottle / book / ball 的真实物体高度。
- near / mid / far 三种距离。

## 统计分层

- raw Geometry 必须按 `support_source` 分层。
- `qualified-plane` 与 `point_p5_fallback` 分开统计，不混成一个均值。
- 先验收 raw Geometry，再查看 Competition Shelf Stabilizer；不得用 stabilizer 掩盖 raw bias/variance。
- support_height 与 object_height：target ≤5%，acceptable ≤10%。

若 Stage3B variance 仍大，优先调查 Geometry / DELTAR / support_source；不得把误差重新吸收到 Stage3A RPY。
