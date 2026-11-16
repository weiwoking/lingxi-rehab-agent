from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def lateral_keyframes() -> list[dict[str, float | str]]:
    source = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
    rows: list[dict[str, float | str]] = []
    pattern = re.compile(
        r'at:\s*(?P<at>[0-9.]+),\s*phase:\s*"(?P<phase>[A-Z_]+)".*?'
        r'ball:\s*\[(?P<ball>[^\]]+)\].*?'
        r'shoulderFlexionDeg:\s*(?P<shoulder_flexion>-?[0-9.]+).*?'
        r'shoulderAbductionDeg:\s*(?P<shoulder_abduction>-?[0-9.]+).*?'
        r'shoulderRotationDeg:\s*(?P<shoulder_rotation>-?[0-9.]+).*?'
        r'elbowFlexionDeg:\s*(?P<elbow>-?[0-9.]+).*?'
        r'wristExtensionDeg:\s*(?P<wrist_extension>-?[0-9.]+).*?'
        r'wristPronationDeg:\s*(?P<wrist_pronation>-?[0-9.]+)'
    )
    for match in pattern.finditer(source):
        ball = [float(part.strip()) for part in match.group("ball").split(",")]
        rows.append({
            "at": float(match.group("at")),
            "phase": match.group("phase"),
            "ball_x": ball[0],
            "ball_y": ball[1],
            "ball_z": ball[2],
            "shoulder_flexion": float(match.group("shoulder_flexion")),
            "shoulder_abduction": float(match.group("shoulder_abduction")),
            "shoulder_rotation": float(match.group("shoulder_rotation")),
            "elbow": float(match.group("elbow")),
            "wrist_extension": float(match.group("wrist_extension")),
            "wrist_pronation": float(match.group("wrist_pronation")),
        })
    return rows


class ReconstructionOfflineCsvStaticContractTest(unittest.TestCase):
    def test_reconstruction_page_contains_no_mojibake_markers(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        markers = [chr(code) for code in (0x9354, 0x9360, 0x93C6, 0x9286, 0x951B, 0xFFFD)]
        for marker in markers:
            self.assertNotIn(marker, source)

    def test_view_contract_exposes_records_and_actual_only(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn('export type ReconstructionView = "records" | "actual"', source)
        self.assertNotIn('{view === "fixed"', source)

    def test_actual_keeps_internal_drink_and_lateral_fixed_priors(self) -> None:
        drink = read_text("frontend/src/twin/HybridDrinkPose.ts")
        lateral = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertIn("sampleDrinkReferencePose(normalizedPhase)", drink)
        self.assertIn("sampleLateralTransferFixedPose(normalizedPhase)", lateral)

    def test_user_visible_fixed_mode_and_hidden_actual_entry_are_removed(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertNotIn('{view === "fixed"', source)
        self.assertNotIn("data-driven-reconstruction-entry", source)
        self.assertNotIn("返回标准示范", source)
        self.assertNotIn("runtimeSnapshot", source)
        self.assertNotIn("useRehabStore", source)

    def test_selected_record_entry_uses_session_context_and_actual_view(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        play_selected = source.split("function playSelectedRecord", 1)[1].split("function returnToRecords", 1)[0]
        self.assertIn("record.sessionId", play_selected)
        self.assertIn('view: "actual"', play_selected)
        self.assertIn('setView("actual")', play_selected)

    def test_actual_mode_reads_selected_session_detail_and_trajectory(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("fetchSessionResult(sessionId)", source)
        self.assertIn("fetchSessionTrajectory(sessionId, rep.rep_index)", source)
        self.assertIn("buildOfflinePlaybackPlan(sessionDetail, trajectories)", source)

    def test_actual_mode_does_not_use_live_runtime_contract(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        for term in ("SESSION_IDLE", "ACTUAL_FRAME_STALE_MS", "runtimeSnapshot", "real-time UDP"):
            self.assertNotIn(term, source)

    def test_actual_mode_uses_hybrid_pose_not_demo_animation(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("scene.applyHybridLateralTransferPose", source)
        self.assertIn("scene.applyHybridDrinkPose", source)
        self.assertIn("composeHybridDrinkPose(activeFrame", source)

    def test_actual_mode_displays_saved_csv_source(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("已保存训练CSV", source)
        self.assertIn("患者动作还原", source)

    def test_actual_repetition_count_comes_from_session_repetitions(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("offlinePlan.validRepCount", source)
        self.assertIn("offlinePlan?.plans.map", source)
        actual_branch = source.split('{view === "actual"', 1)[1]
        self.assertNotIn("DEMO_REPETITION_COUNT", actual_branch)

    def test_actual_whole_group_uses_valid_rep_plans_only(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("const validReps = [...detail.repetitions].filter((rep) => rep.valid)", source)
        self.assertIn("validRepCount: validReps.length", source)

    def test_frame_selection_uses_last_real_frame_not_interpolation(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("export function resolveFrameAtElapsedMs", source)
        self.assertIn("if (item.relativeMs > target) break", source)
        self.assertNotIn("interp", source.lower())

    def test_actual_lf_comes_from_per_rep_score(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("export function resolveRepLfState", source)
        self.assertIn('rep.compensation.types?.includes("LF")', source)
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertNotIn("0.09885350515694372", page)

    def test_actual_uses_full_session_neutral_frame(self) -> None:
        source = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("export function resolveSessionNeutralFrame", source)
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("const sessionNeutralFrame = offlinePlan ? resolveSessionNeutralFrame(offlinePlan) : null", page)
        self.assertIn("composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)

    def test_imu_and_emg_labels_use_authoritative_mapping(self) -> None:
        trajectory = read_text("frontend/src/twin/actualTrajectory.ts")
        for text in ("IMU1 上臂", "IMU2 躯干", "IMU3 前臂", "IMU4 手腕"):
            self.assertIn(text, trajectory)
        for text in ("EMG1 指伸肌", "EMG2 肱二头肌", "EMG3 肱三头肌", "EMG4 尺侧腕伸肌"):
            self.assertIn(text, trajectory)

    def test_actual_cup_is_visual_aid_not_csv_claim(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("水杯位置仅用于动作可视化", page)
        self.assertNotIn("cup coordinate from CSV", page)

    def test_reconstruction_chinese_copy_does_not_change_business_branches(self) -> None:
        source = read_text("frontend/src/pages/ReconstructionPage.tsx")
        for text in ("动作还原", "训练记录", "水杯拿取与饮水训练（二级）", "患者实际动作"):
            self.assertIn(text, source)
        for english in ("Action Reconstruction", "Training Records", "Drink water", "Fixed demo 5 reps"):
            self.assertNotIn(english, source)
        self.assertNotIn('{view === "fixed"', source)
        self.assertIn('{view === "actual"', source)
        self.assertIn("fetchSessionTrajectory(sessionId, rep.rep_index)", source)

    def test_lateral_transfer_prior_is_present_with_full_phase_contract(self) -> None:
        source = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        for phase in ("DANGLE", "REACH_BALL", "GRASP_BALL", "INWARD_TRANSFER", "OUTWARD_TRANSFER", "RELEASE_BALL", "RETURN_DANGLE"):
            self.assertIn(phase, source)
        for token in ("shoulderFlexionDeg", "shoulderAbductionDeg", "elbowFlexionDeg", "wristExtensionDeg", "ballPosition"):
            self.assertIn(token, source)
        self.assertIn("sampleLateralTransferFixedPose", source)
        self.assertIn("composeHybridLateralTransferPose", source)

    def test_lateral_transfer_reuses_existing_hybrid_scene_and_rig_interfaces(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        rig = read_text("frontend/src/twin/SegmentRigAdapter.ts")
        self.assertIn("composeHybridLateralTransferPose(activeFrame", page)
        self.assertIn("sampleLateralTransferFixedPose", read_text("frontend/src/twin/HybridLateralTransferPose.ts"))
        self.assertIn("scene.applyHybridLateralTransferPose", page)
        self.assertIn("applyHybridLateralTransferPose", scene)
        self.assertIn("updateHybridLateralTransferPose", rig)
        self.assertNotIn("runtimeSnapshot", page)
        self.assertNotIn("SESSION_IDLE", page)

    def test_lateral_actual_uses_session_duration_and_real_emg_without_fake_solver(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        lateral = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertIn("activeNormalizedPhase", page)
        self.assertIn("activeRepPlan.timeline.durationMs", page)
        self.assertIn("frameToEmg(activeFrame, activeRepPlan.emgScale)", page)
        self.assertIn('poseFromAngles(base.canonicalJointAngles, base.phase, base.ballPosition, trunkDeviationDeg, "offline_session_csv")', lateral)
        self.assertNotIn("inverseKinematics", lateral)
        self.assertNotIn("quaternion fusion", lateral)
        self.assertNotIn("fixed fake EMG", page)

    def test_lateral_ball_uses_existing_presentation_object_lifecycle(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("presentationBall", scene)
        self.assertIn("setPresentationObjectMode(mode: \"cup\" | \"ball\")", scene)
        self.assertIn("lateral_transfer_presentation_ball", scene)
        self.assertIn("updatePresentationBall", scene)
        self.assertIn("renderPresentationBall", scene)

    def test_drink_reconstruction_path_remains_available(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("composeHybridDrinkPose(activeFrame", page)
        self.assertIn("scene.applyHybridDrinkPose", page)
        self.assertIn("setPresentationObjectMode(\"cup\")", scene)
        self.assertIn("水杯拿取与饮水训练（二级）", page)

    def test_lateral_grasp_and_release_keep_elbow_near_extension(self) -> None:
        frames = lateral_keyframes()
        grasp = next(frame for frame in frames if frame["phase"] == "GRASP_BALL")
        inward = max((frame for frame in frames if frame["phase"] == "INWARD_TRANSFER"), key=lambda frame: float(frame["elbow"]))
        release = next(frame for frame in frames if frame["phase"] == "RELEASE_BALL")
        self.assertLess(float(grasp["elbow"]), 25)
        self.assertLess(float(release["elbow"]), 25)
        self.assertGreater(float(inward["elbow"]), 80)
        self.assertGreater(float(inward["elbow"]) - float(grasp["elbow"]), 60)
        self.assertGreater(float(inward["elbow"]) - float(release["elbow"]), 60)

    def test_lateral_pull_in_uses_shoulder_coupling_not_elbow_only(self) -> None:
        frames = lateral_keyframes()
        grasp = next(frame for frame in frames if frame["phase"] == "GRASP_BALL")
        inward = max((frame for frame in frames if frame["phase"] == "INWARD_TRANSFER"), key=lambda frame: float(frame["elbow"]))
        shoulder_delta = (
            abs(float(inward["shoulder_flexion"]) - float(grasp["shoulder_flexion"])) +
            abs(float(inward["shoulder_abduction"]) - float(grasp["shoulder_abduction"])) +
            abs(float(inward["shoulder_rotation"]) - float(grasp["shoulder_rotation"]))
        )
        wrist_delta = (
            abs(float(inward["wrist_extension"]) - float(grasp["wrist_extension"])) +
            abs(float(inward["wrist_pronation"]) - float(grasp["wrist_pronation"]))
        )
        self.assertGreaterEqual(shoulder_delta, 20)
        self.assertLess(wrist_delta, 8)

    def test_lateral_push_out_reverses_pull_in_shoulder_and_elbow_pattern(self) -> None:
        frames = lateral_keyframes()
        grasp = next(frame for frame in frames if frame["phase"] == "GRASP_BALL")
        inward = max((frame for frame in frames if frame["phase"] == "INWARD_TRANSFER"), key=lambda frame: float(frame["elbow"]))
        release = next(frame for frame in frames if frame["phase"] == "RELEASE_BALL")
        self.assertLess(abs(float(release["elbow"]) - float(grasp["elbow"])), 5)
        self.assertLess(abs(float(release["shoulder_flexion"]) - float(grasp["shoulder_flexion"])), 3)
        self.assertLess(abs(float(release["shoulder_abduction"]) - float(grasp["shoulder_abduction"])), 3)
        self.assertGreater(abs(float(inward["shoulder_flexion"]) - float(release["shoulder_flexion"])), 10)
        self.assertGreater(abs(float(inward["shoulder_rotation"]) - float(release["shoulder_rotation"])), 10)

    def test_lateral_main_ball_path_keeps_height_without_drop(self) -> None:
        frames = lateral_keyframes()
        main = [frame for frame in frames if 0.30 <= float(frame["at"]) <= 0.84]
        y_values = [float(frame["ball_y"]) for frame in main]
        self.assertLessEqual(max(y_values) - min(y_values), 0.01)

    def test_lateral_prior_keeps_internal_control_points_without_new_phase_contract(self) -> None:
        frames = lateral_keyframes()
        phases = {str(frame["phase"]) for frame in frames}
        self.assertEqual(phases, {"DANGLE", "REACH_BALL", "GRASP_BALL", "INWARD_TRANSFER", "OUTWARD_TRANSFER", "RELEASE_BALL", "RETURN_DANGLE"})
        self.assertGreaterEqual(sum(1 for frame in frames if frame["phase"] == "INWARD_TRANSFER"), 2)
        self.assertGreaterEqual(sum(1 for frame in frames if frame["phase"] == "OUTWARD_TRANSFER"), 2)

    def test_lateral_wrist_is_not_primary_motion_driver(self) -> None:
        frames = lateral_keyframes()
        main = [frame for frame in frames if 0.30 <= float(frame["at"]) <= 0.84]
        wrist_extension_range = max(float(frame["wrist_extension"]) for frame in main) - min(float(frame["wrist_extension"]) for frame in main)
        wrist_pronation_range = max(float(frame["wrist_pronation"]) for frame in main) - min(float(frame["wrist_pronation"]) for frame in main)
        elbow_range = max(float(frame["elbow"]) for frame in main) - min(float(frame["elbow"]) for frame in main)
        self.assertLessEqual(wrist_extension_range, 4)
        self.assertLessEqual(wrist_pronation_range, 4)
        self.assertGreaterEqual(elbow_range, 70)

    def test_lateral_pull_in_end_is_chest_level_hold_target(self) -> None:
        frames = lateral_keyframes()
        grasp = next(frame for frame in frames if frame["phase"] == "GRASP_BALL")
        pull_in = max((frame for frame in frames if frame["phase"] == "INWARD_TRANSFER"), key=lambda frame: float(frame["elbow"]))
        self.assertLessEqual(float(pull_in["shoulder_flexion"]), 10)
        self.assertLessEqual(float(pull_in["shoulder_abduction"]), 12)
        self.assertGreaterEqual(float(pull_in["shoulder_rotation"]), 20)
        self.assertGreaterEqual(float(pull_in["elbow"]), 88)
        self.assertLessEqual(float(pull_in["elbow"]), 100)
        self.assertLess(float(pull_in["ball_x"]), float(grasp["ball_x"]) - 0.4)
        self.assertLessEqual(abs(float(pull_in["ball_y"]) - float(grasp["ball_y"])), 0.01)

    def test_lateral_pull_in_and_push_out_use_symmetric_control_points(self) -> None:
        frames = lateral_keyframes()
        inward = [frame for frame in frames if frame["phase"] == "INWARD_TRANSFER"]
        outward = [frame for frame in frames if frame["phase"] == "OUTWARD_TRANSFER"]
        self.assertGreaterEqual(len(inward), 3)
        self.assertGreaterEqual(len(outward), 3)
        self.assertEqual([float(frame["elbow"]) for frame in inward], [46.0, 76.0, 92.0])
        self.assertEqual([float(frame["elbow"]) for frame in outward[:2]], [76.0, 46.0])
        self.assertLess(float(inward[-1]["shoulder_flexion"]), float(inward[0]["shoulder_flexion"]))
        self.assertGreater(float(outward[1]["shoulder_flexion"]), float(outward[0]["shoulder_flexion"]))

    def test_lateral_release_recovers_grasp_shape_and_height(self) -> None:
        frames = lateral_keyframes()
        grasp = next(frame for frame in frames if frame["phase"] == "GRASP_BALL")
        release = next(frame for frame in frames if frame["phase"] == "RELEASE_BALL")
        for key in ("shoulder_flexion", "shoulder_abduction", "shoulder_rotation", "elbow", "ball_y"):
            self.assertLessEqual(abs(float(release[key]) - float(grasp[key])), 0.01)

    def test_lateral_actual_reuses_drink_trunk_helper(self) -> None:
        source = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertIn('import { computeQuaternionTrunkLean } from "./HybridDrinkPose"', source)
        self.assertIn("computeQuaternionTrunkLean(frame, neutralFrame)", source)
        self.assertNotIn("function computeTrunkDeviationDeg", source)

    def test_lateral_actual_keeps_arm_from_fixed_prior_and_trunk_from_history(self) -> None:
        source = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertIn("const base = sampleLateralTransferFixedPose(normalizedPhase)", source)
        self.assertIn("poseFromAngles(base.canonicalJointAngles, base.phase, base.ballPosition, trunkDeviationDeg", source)
        self.assertIn("measuredCorrectionReliable: { trunk: trunkDeviationDeg !== null, shoulder: false, elbow: false, wrist: false }", source)
        self.assertNotIn("computeMeasuredJointCorrections(frame, neutralFrame)", source)
        self.assertNotIn("inverseKinematics", source)

    def test_lateral_actual_uses_history_duration_emg_and_rep_count_like_drink(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        trajectory = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("activeRelativeMs / activeRepPlan.timeline.durationMs", page)
        self.assertIn("frameToEmg(activeFrame, activeRepPlan.emgScale)", page)
        self.assertIn("validRepCount: validReps.length", trajectory)
        self.assertIn("repCount: detail.repetitions.length", trajectory)
        self.assertIn("fetchSessionTrajectory(sessionId, rep.rep_index)", page)

    def test_lateral_actual_has_no_runtime_or_synthetic_tremor_dependency(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        lateral = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        for token in ("runtimeSnapshot", "SESSION_IDLE", "realtime_udp", "device_online", "Math.sin", "random", "jitter"):
            self.assertNotIn(token, page)
        self.assertIn('tremorDisplay: "badge/text only; no synthetic bone shake"', lateral)

    def test_lateral_ball_is_visual_prior_not_sensor_measured(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        lateral = read_text("frontend/src/twin/HybridLateralTransferPose.ts")
        self.assertIn("objectLabel(selectedObjectType)", page)
        self.assertIn("位置仅用于动作可视化", page)
        self.assertIn("base.ballPosition", lateral)
        self.assertNotIn("ballSensor", lateral)


if __name__ == "__main__":
    unittest.main()

