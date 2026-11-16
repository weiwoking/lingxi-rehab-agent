from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class HybridDrinkPoseContractTest(unittest.TestCase):
    def test_module_exports_required_pure_functions(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        for name in (
            "sampleDrinkReferencePose",
            "computeSensorDelta",
            "mapSensorDeltaToModelBasis",
            "extractSignedHingeAngle",
            "computeMeasuredJointCorrections",
            "assessJointCorrectionReliability",
            "composeHybridDrinkPose",
            "computeQuaternionTrunkLean",
            "validateForwardReachDirection",
            "handPositionFromAngles",
        ):
            self.assertIn(f"export function {name}", source)

    def test_single_rep_uses_full_session_neutral_not_active_plan(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("const sessionNeutralFrame = offlinePlan ? resolveSessionNeutralFrame(offlinePlan) : null", page)
        self.assertIn("composeHybridDrinkPose(activeFrame, activeNormalizedPhase, sessionNeutralFrame)", page)
        self.assertNotIn("const activeNeutralFrame = activeOfflinePlan ? resolveSessionNeutralFrame(activeOfflinePlan) : null", page)

    def test_rep_scope_does_not_enter_scene_calibration_key(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("const planKey = activeOfflinePlan.sessionId", page)
        self.assertNotIn("`${activeOfflinePlan.sessionId}:${actualScope}`", page)

    def test_identity_alignment_is_not_marked_ready_for_limb_imus(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("export const ALIGNMENT_READY", source)
        self.assertRegex(source, r"imu1:\s*false")
        self.assertRegex(source, r"imu3:\s*false")
        self.assertRegex(source, r"imu4:\s*false")
        self.assertRegex(source, r"imu2:\s*true")

    def test_unknown_alignment_makes_joint_corrections_unreliable(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        reliability_body = source.split("export function assessJointCorrectionReliability", 1)[1].split("export function computeSensorDelta", 1)[0]
        self.assertIn("ALIGNMENT_READY.imu1 && ALIGNMENT_READY.imu2", reliability_body)
        self.assertIn("ALIGNMENT_READY.imu1 && ALIGNMENT_READY.imu3", reliability_body)
        self.assertIn("ALIGNMENT_READY.imu3 && ALIGNMENT_READY.imu4", reliability_body)

    def test_sensor_delta_uses_current_times_inverse_neutral_order(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn('export type SensorDeltaOrder = "current_times_inverse_neutral"', source)
        self.assertIn("current.clone().normalize().multiply(neutral.clone().normalize().invert())", source)

    def test_sensor_to_model_alignment_uses_conjugation_but_not_claimed_complete(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("SENSOR_TO_MODEL_ALIGNMENT", source)
        self.assertIn("multiply(delta.clone().normalize()).multiply(sensorToModel.clone().normalize().invert())", source)
        self.assertIn("alignmentReady: { ...ALIGNMENT_READY }", source)

    def test_forward_reach_uses_hand_position_geometry(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        function_body = source.split("export function validateForwardReachDirection", 1)[1].split("function poseFromAngles", 1)[0]
        self.assertIn("handPositionFromAngles(baseAngles)", function_body)
        self.assertIn("handPositionFromAngles(finalAngles)", function_body)
        self.assertIn("finalDelta.dot(MODEL_FORWARD_AXIS)", function_body)
        self.assertNotIn("Math.sign(baseAngles.shoulderFlexionDeg", function_body)

    def test_reverse_forward_gate_restores_base_shoulder_and_wrist(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        body = source.split("if (!validateForwardReachDirection", 1)[1].split("const appliedCorrections", 1)[0]
        self.assertIn("finalAngles.shoulderFlexionDeg = base.canonicalJointAngles.shoulderFlexionDeg", body)
        self.assertIn("finalAngles.shoulderAbductionDeg = base.canonicalJointAngles.shoulderAbductionDeg", body)
        self.assertIn("finalAngles.shoulderRotationDeg = base.canonicalJointAngles.shoulderRotationDeg", body)
        self.assertIn("finalAngles.wristExtensionDeg = base.canonicalJointAngles.wristExtensionDeg", body)

    def test_normal_forward_reach_has_positive_model_axis_progress(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("const MODEL_FORWARD_AXIS = computeModelForwardAxis()", source)
        self.assertIn("handPositionFromAngles(SAMPLE_POINTS[2].angles).sub(handPositionFromAngles(SAMPLE_POINTS[0].angles)).normalize()", source)

    def test_model_forward_axis_is_initialized_after_sample_points(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        sample_index = source.index("const SAMPLE_POINTS")
        axis_index = source.index("const MODEL_FORWARD_AXIS = computeModelForwardAxis()")
        function_index = source.index("function computeModelForwardAxis()")
        self.assertLess(sample_index, axis_index)
        self.assertLess(axis_index, function_index)

    def test_trunk_visual_angle_is_one_to_one_not_point_three_five(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("clamp(trunkLeanDeg ?? 0, -25, 25) * -1.0", source)
        self.assertNotIn("* -0.35", source)

    def test_imu2_trunk_lean_uses_quaternion_delta_not_pitch(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        function_body = source.split("export function computeQuaternionTrunkLean", 1)[1].split("export function validateForwardReachDirection", 1)[0]
        self.assertIn('sensorDeltaFor(frame.imu2, neutralFrame.imu2, "imu2")', function_body)
        self.assertNotIn(".pitch", function_body)

    def test_drink_hold_elbow_is_visibly_flexed_more_than_reach(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        reach = re.search(r'phase: "REACH".*?elbowFlexionDeg: ([0-9.]+)', source, re.S)
        hold = re.search(r'phase: "DRINK_HOLD".*?elbowFlexionDeg: ([0-9.]+)', source, re.S)
        self.assertIsNotNone(reach)
        self.assertIsNotNone(hold)
        self.assertGreater(float(hold.group(1)), float(reach.group(1)) + 40)

    def test_elbow_uses_hinge_axis_not_full_three_dof(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("extractSignedHingeAngle(forearmRelative, DRINK_CUP_AXIS_MAPPING.elbowFlexion.axis)", source)
        self.assertIn("elbowQuaternion(angles)", source)

    def test_wrist_correction_cannot_dominate(self) -> None:
        source = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("wristCorrectionWeight: 0.22", source)
        self.assertIn("HYBRID_CORRECTION_LIMITS.wristExtensionDeg", source)

    def test_actual_does_not_use_raw_quaternion_as_final_pose(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("composeHybridDrinkPose(activeFrame", page)
        self.assertIn("scene.applyHybridDrinkPose", page)
        self.assertNotIn("scene.applyActualTelemetry", page)

    def test_lf_changes_line_color_only(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn('material.color.set(lfDetected ? "#8b5cf6" : "#ffffff")', scene)
        self.assertIn("setTrunkCompensationHighlight(false)", scene)

    def test_actual_emg_remains_external_csv_driven(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("frameToEmg(activeFrame, activeRepPlan.emgScale)", page)
        self.assertIn("if (emg) this.updateEmg(emg)", scene)
        self.assertNotIn("applyPlaybackActivation", page)

    def test_demo_mode_keeps_preset_emg(self) -> None:
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn('emgMode?: "preset" | "external"', scene)
        self.assertIn('if (this.pendingMotion?.emgMode === "external") return', scene)
        self.assertIn("this.highlighter.applyPlaybackActivation(activations[activationPhase])", scene)

    def test_cup_follows_hybrid_hand_position_as_visual_aid(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        scene = read_text("frontend/src/twin/AnatomyScene.ts")
        self.assertIn("水杯位置仅用于动作可视化", page)
        self.assertIn("this.updatePresentationCup(pose.cupPhase as PlaybackPhaseKey)", scene)
        self.assertIn("this.updateCupTargetFromHand()", scene)

    def test_actual_still_uses_csv_rep_count_and_duration(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        trajectory = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("offlinePlan.validRepCount", page)
        self.assertIn("timeline.durationMs", page)
        self.assertIn("cursor += durationMs", trajectory)

    def test_dragging_progress_updates_hybrid_pose_from_same_elapsed_ms(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        self.assertIn("onChange={(event) => seekActual(Number(event.currentTarget.value))}", page)
        self.assertIn("resolvePlanAtElapsedMs(activeOfflinePlan, actualElapsedMs)", page)
        self.assertIn("resolveFrameInPlanAtElapsedMs(activeRepPlan, actualElapsedMs)", page)

    def test_no_fake_sensor_frames_or_zero_fill(self) -> None:
        trajectory = read_text("frontend/src/twin/actualTrajectory.ts")
        self.assertIn("return typeof value === \"number\" && Number.isFinite(value) ? value : null", trajectory)
        self.assertIn("value === null ? null", trajectory)
        self.assertNotIn("return value ?? 0", trajectory)
        self.assertIn("activation: value === null ? null", trajectory)

    def test_actual_visual_path_still_uses_internal_fixed_kinematic_prior(self) -> None:
        page = read_text("frontend/src/pages/ReconstructionPage.tsx")
        pose = read_text("frontend/src/twin/HybridDrinkPose.ts")
        self.assertIn("composeHybridDrinkPose(activeFrame", page)
        self.assertIn("const base = sampleDrinkReferencePose(normalizedPhase)", pose)

    def test_reconstruction_files_are_utf8_readable(self) -> None:
        for relative in ("frontend/src/pages/ReconstructionPage.tsx", "frontend/src/twin/actualTrajectory.ts", "frontend/src/twin/HybridDrinkPose.ts"):
            source = read_text(relative)
            self.assertNotIn("\ufffd", source)


if __name__ == "__main__":
    unittest.main()
