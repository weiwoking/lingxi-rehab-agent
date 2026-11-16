import math
import unittest

from backend.app.demo_engine import QUATERNION_ORDER, engine


def quat_tuple(frame: dict, sensor_id: str) -> tuple[float, float, float, float]:
    quat = frame["telemetry"]["imu_pose"]["sensors"][sensor_id]["quaternion"]
    return (quat["w"], quat["x"], quat["y"], quat["z"])


def quat_norm(quat: tuple[float, float, float, float]) -> float:
    return math.sqrt(sum(value * value for value in quat))


def quat_distance(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def quat_multiply(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def rotate_vector(quat: tuple[float, float, float, float], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    rotated = quat_multiply(quat_multiply(quat, (0.0, *vector)), (quat[0], -quat[1], -quat[2], -quat[3]))
    return (rotated[1], rotated[2], rotated[3])


class DemoMotionQuaternionTest(unittest.TestCase):
    def frame_at(self, t: float) -> dict:
        return engine._frame({"duration_sec": 72, "rep_metrics": []}, t)

    def wrist_position_at(self, t: float) -> tuple[float, float, float]:
        frame = self.frame_at(t)
        position = [0.0, 0.0, 0.0]
        for sensor_id, segment_vector in [
            ("upper_arm_imu", (0.0, -0.42, 0.0)),
            ("forearm_imu", (0.0, -0.38, 0.0)),
            ("wrist_imu", (0.0, -0.12, 0.0)),
        ]:
            segment = rotate_vector(quat_tuple(frame, sensor_id), segment_vector)
            position = [position[index] + segment[index] for index in range(3)]
        return (position[0], position[1], position[2])

    def test_demo_generates_unit_quaternions_for_four_imus(self) -> None:
        frame = self.frame_at(12.0)
        self.assertEqual(frame["telemetry"]["imu_pose"]["quaternion_order"], QUATERNION_ORDER)
        self.assertEqual(QUATERNION_ORDER, "w,x,y,z")
        for sensor_id in ["lower_back_imu", "upper_arm_imu", "forearm_imu", "wrist_imu"]:
            self.assertAlmostEqual(quat_norm(quat_tuple(frame, sensor_id)), 1.0, places=4)

    def test_upper_arm_changes_between_prepare_and_lift(self) -> None:
        prepare = quat_tuple(self.frame_at(1.0), "upper_arm_imu")
        lift = quat_tuple(self.frame_at(12.0), "upper_arm_imu")
        self.assertGreater(quat_distance(prepare, lift), 0.08)

    def test_forearm_changes_between_prepare_and_approach_mouth(self) -> None:
        prepare = quat_tuple(self.frame_at(1.0), "forearm_imu")
        approach_mouth = quat_tuple(self.frame_at(14.0), "forearm_imu")
        self.assertGreater(quat_distance(prepare, approach_mouth), 0.08)

    def test_prepare_to_return_remains_continuous_and_normalized(self) -> None:
        previous = quat_tuple(self.frame_at(1.0), "wrist_imu")
        for t in [7.4, 8.8, 10.4, 12.0, 14.0, 16.0]:
            current = quat_tuple(self.frame_at(t), "wrist_imu")
            self.assertAlmostEqual(quat_norm(current), 1.0, places=4)
            self.assertLess(quat_distance(previous, current), 1.25)
            previous = current

    def test_drink_cup_task_space_moves_forward_upward_and_inward(self) -> None:
        prepare = self.wrist_position_at(1.0)
        reach = self.wrist_position_at(8.5)
        lift = self.wrist_position_at(12.5)
        mouth = self.wrist_position_at(14.5)
        returned = self.wrist_position_at(18.5)
        self.assertGreater(reach[2], prepare[2] + 0.25)
        self.assertGreater(reach[1], prepare[1] + 0.15)
        self.assertGreater(lift[1], reach[1] + 0.45)
        self.assertGreater(mouth[1], lift[1] + 0.12)
        self.assertGreater(mouth[0], lift[0] + 0.01)
        self.assertLess(abs(returned[2] - prepare[2]), 0.08)
        self.assertLess(abs(returned[1] - prepare[1]), 0.08)


if __name__ == "__main__":
    unittest.main()
