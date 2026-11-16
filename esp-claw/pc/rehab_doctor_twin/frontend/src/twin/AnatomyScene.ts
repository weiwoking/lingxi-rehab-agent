import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { EmgPayload, LiveImuFrame, TelemetryPayload } from "../contracts/realtimeMessages";
import { AnatomyModelLoader, type AnatomyLoadResult } from "./AnatomyModelLoader";
import { AnatomyPicker } from "./AnatomyPicker";
import { addActualPerfDuration, finishActualPerfTrace, incrementActualPerfCounter, measureActualPerfSync } from "./actualPerfTrace";
import { resolveAnatomyModelUrl } from "./cachedAnatomyModel";
import { disposeThreeObject } from "./disposeThreeObject";
import { ImuPoseMapper } from "./ImuPoseMapper";
import { JointRigAdapter } from "./JointRigAdapter";
import { manifestItemForBusinessId } from "./modelManifest";
import { MuscleHighlighter, type PlaybackMuscleActivation } from "./MuscleHighlighter";
import { ReferenceTrajectory } from "./ReferenceTrajectory";
import {
  SegmentRigAdapter,
  type DiagnosticPoseEasing,
  type DiagnosticPoseTransition,
  type DiagnosticPoseVariation
} from "./SegmentRigAdapter";
import type { HybridDrinkPose } from "./HybridDrinkPose";
import type { HybridLateralTransferPose } from "./HybridLateralTransferPose";

export type DisplayMode = "muscle" | "bone" | "muscle_bone" | "all";
export type CameraPreset = "front" | "side" | "shoulder" | "forearm_hand" | "upper_body";
export type PlaybackPhaseKey =
  | "DANGLE_START"
  | "REACH"
  | "GRASP"
  | "LIFT"
  | "DRINK_HOLD"
  | "LOWER"
  | "RELEASE"
  | "RETURN_DANGLE"
  | "DANGLE_END";
export interface AnatomySceneCallbacks { onLoadStatus: (status: string) => void; onSelected: (label: string) => void; onRigMode: (label: string) => void; }
export interface ExistingArmMotionOptions {
  sessionId: string;
  repetitions: number;
  repetitionDurationMs?: number[];
  timingMode?: "demo" | "session_exact";
  emgMode?: "preset" | "external";
  speed?: number;
  startRepetition?: number;
  onRepetitionComplete: (completed: number) => void;
  onPhaseChange?: (state: { repetition: number; phase: PlaybackPhaseKey; elapsedSeconds: number }) => void;
  onComplete: () => void;
}
interface MaterialState { opacity: number; transparent: boolean; depthWrite: boolean; color?: THREE.Color; emissive?: THREE.Color; emissiveIntensity?: number; }
type DiagnosticPosePhase = "DANGLE" | "PREPARE" | "REACH" | "LIFT" | "APPROACH_MOUTH" | "RETURN";
interface PlaybackRepetitionPlan {
  scale: number;
  phases: Array<{ phase: PlaybackPhaseKey; durationMs: number; pose: DiagnosticPosePhase; easing: DiagnosticPoseEasing }>;
  betweenPauseMs: number;
  variation: DiagnosticPoseVariation;
}
type ShoulderAxisDebug = "X" | "Y" | "Z";

export class AnatomyScene {
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(42, 1, 0.02, 50);
  private renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: "high-performance", preserveDrawingBuffer: true });
  private controls!: OrbitControls; private resizeObserver!: ResizeObserver; private animationId = 0; private model: AnatomyLoadResult | null = null;
  private highlighter = new MuscleHighlighter(); private picker = new AnatomyPicker(); private trajectory = new ReferenceTrajectory();
  private boneRig: JointRigAdapter | null = null; private segmentRig: SegmentRigAdapter | null = null; private poseMapper = new ImuPoseMapper();
  private referenceLine!: THREE.Line; private ghostArm!: THREE.Line; private showTrajectory = false; private lastTelemetry: TelemetryPayload | null = null;
  private trunkReferenceLine!: THREE.Line; private trunkLiveLine!: THREE.Line; private trunkLeanLabelAnchor = new THREE.Vector3(0.18, -0.78, 0.0);
  private currentDisplayMode: DisplayMode = "muscle_bone"; private materialStates = new Map<THREE.Material, MaterialState>();
  private lastFrameAt = performance.now();
  private liveFrameCount = 0; private droppedFrameCount = 0; private lastFrameTimestampMs = 0; private demoAutoCalibrationSessionId = ""; private lastDebugLogAt = 0;
  private motionTimeouts = new Set<number>();
  private motionGeneration = 0;
  private pendingMotion: ExistingArmMotionOptions | null = null;
  private lastHybridPose: HybridDrinkPose | null = null;
  private lastLateralPose: HybridLateralTransferPose | null = null;
  private presentationCup: THREE.Group | null = null;
  private presentationBall: THREE.Mesh | null = null;
  private presentationObjectMode: "cup" | "ball" = "cup";
  private cupTarget = new THREE.Vector3(-0.86, -0.72, 0.42);
  private cupTablePosition = new THREE.Vector3(-0.86, -0.66, 0.42);
  private cupHandOffset = new THREE.Vector3(-0.015, -0.055, 0.025);
  private cupAttached = false;
  private ballTarget = new THREE.Vector3(0.42, 0.78, 0.22);
  private ballHandOffset = new THREE.Vector3(0.02, -0.04, 0.02);
  private ballAttached = false;
  private disposed = false;
  constructor(private container: HTMLElement, private callbacks: AnatomySceneCallbacks) {
    const constructorStart = performance.now();
    measureActualPerfSync("renderer_scene_init_ms", () => {
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5)); this.renderer.setSize(container.clientWidth, container.clientHeight); this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFSoftShadowMap; container.appendChild(this.renderer.domElement);
    });
    measureActualPerfSync("camera_controls_setup_ms", () => {
      this.camera.position.set(-1.55, 0.95, 2.15); this.controls = new OrbitControls(this.camera, this.renderer.domElement); this.controls.enableDamping = true; this.controls.target.set(-0.18, 0.45, 0.02);
    });
    measureActualPerfSync("scene_static_setup_ms", () => {
      this.scene.background = new THREE.Color("#e6edf1"); this.scene.fog = new THREE.Fog("#e6edf1", 7, 15); this.scene.add(new THREE.HemisphereLight("#ffffff", "#c8d5dd", 1.55));
      const directional = new THREE.DirectionalLight("#ffffff", 2.1); directional.position.set(3, 4, 2); directional.castShadow = true; directional.shadow.mapSize.set(1024, 1024); this.scene.add(directional);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(5.4, 5.4), new THREE.ShadowMaterial({ color: "#7a8e99", opacity: 0.14 })); floor.rotation.x = -Math.PI / 2; floor.position.y = -1.18; floor.receiveShadow = true; this.scene.add(floor);
      const grid = new THREE.GridHelper(5.4, 16, "#9aadb8", "#c4d2da"); grid.position.y = -1.17; (grid.material as THREE.Material).transparent = true; (grid.material as THREE.Material).opacity = 0.46; this.scene.add(grid); this.scene.add(this.trajectory.group);
      this.addPresentationSceneObjects();
      this.referenceLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)]), new THREE.LineBasicMaterial({ color: "#d6fbff", transparent: true, opacity: 0.82 })); this.referenceLine.visible = false; this.scene.add(this.referenceLine);
      this.ghostArm = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-0.1, 0.7, 0), new THREE.Vector3(-0.32, 0.24, 0.32), new THREE.Vector3(-0.5, -0.05, 0.55), new THREE.Vector3(-0.62, -0.08, 0.72)]), new THREE.LineBasicMaterial({ color: "#74e2dc", transparent: true, opacity: 0.42 })); this.ghostArm.visible = false; this.scene.add(this.ghostArm);
      this.trunkReferenceLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([this.trunkLeanLabelAnchor, this.trunkLeanLabelAnchor.clone().add(new THREE.Vector3(0, 0.72, 0))]), new THREE.LineBasicMaterial({ color: "#64748b", transparent: true, opacity: 0.72 }));
      this.trunkLiveLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([this.trunkLeanLabelAnchor, this.trunkLeanLabelAnchor.clone().add(new THREE.Vector3(0, 0.72, 0))]), new THREE.LineBasicMaterial({ color: "#ffffff", linewidth: 3 }));
      this.trunkReferenceLine.visible = false; this.trunkLiveLine.visible = false; this.scene.add(this.trunkReferenceLine, this.trunkLiveLine);
    });
    measureActualPerfSync("animation_setup_ms", () => {
      this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(container); this.renderer.domElement.addEventListener("pointerdown", this.onPointerDown); this.animate();
    });
    addActualPerfDuration("AnatomyScene_constructor_body_ms", performance.now() - constructorStart);
    void this.loadModel();
  }
  resetCamera() { this.setCameraPreset("shoulder"); }
  setCameraPreset(preset: CameraPreset) { const targetByPreset: Record<CameraPreset, THREE.Vector3> = { front: new THREE.Vector3(-0.26, 0.36, 0.18), side: new THREE.Vector3(-0.46, 0.24, 0.28), shoulder: new THREE.Vector3(-0.44, 0.36, 0.28), forearm_hand: new THREE.Vector3(-0.62, -0.02, 0.42), upper_body: new THREE.Vector3(-0.44, 0.92, 0.12) }; const positionByPreset: Record<CameraPreset, THREE.Vector3> = { front: new THREE.Vector3(-1.42, 0.72, 2.05), side: new THREE.Vector3(-2.0, 0.58, 0.58), shoulder: new THREE.Vector3(-1.22, 0.72, 1.58), forearm_hand: new THREE.Vector3(-1.05, 0.42, 1.08), upper_body: new THREE.Vector3(-1.55, 1.18, 2.55) }; this.controls.target.copy(targetByPreset[preset]); this.camera.position.copy(positionByPreset[preset]); this.controls.minDistance = 0.82; this.controls.maxDistance = 3.6; this.controls.maxPolarAngle = Math.PI * 0.72; this.camera.updateProjectionMatrix(); }
  setDisplayMode(mode: DisplayMode) {
    if (!this.model) return;
    this.currentDisplayMode = mode;
    for (const mesh of this.model.meshes) {
      const businessId = this.model.meshBusinessIds.get(mesh) ?? mesh.userData.anatomyBusinessId;
      const mapped = typeof businessId === "string" ? manifestItemForBusinessId(businessId) : undefined;
      const focused = mapped?.focusUpperLimb === true;
      if (mode === "all") mesh.visible = focused;
      else if (mode === "muscle_bone") mesh.visible = mapped?.visibleByDefault === true;
      else mesh.visible = focused && mapped?.type === mode;
      this.applyDisplayMaterial(mesh, mapped?.type, mode);
    }
  }
  setTrajectoryVisible(visible: boolean) { this.showTrajectory = visible; this.trajectory.group.visible = visible; this.referenceLine.visible = false; this.ghostArm.visible = false; }
  setPresentationCupVisible(visible: boolean) {
    if (this.presentationCup) this.presentationCup.visible = visible && this.presentationObjectMode === "cup";
    if (this.presentationBall) this.presentationBall.visible = visible && this.presentationObjectMode === "ball";
  }
  setPresentationObjectMode(mode: "cup" | "ball") {
    this.presentationObjectMode = mode;
    if (this.presentationCup) this.presentationCup.visible = mode === "cup";
    if (this.presentationBall) this.presentationBall.visible = mode === "ball";
  }
  applyLiveSensorFrame(frame: LiveImuFrame, emgFrame?: EmgPayload, telemetryBase?: TelemetryPayload) {
    const base = telemetryBase ?? this.lastTelemetry;
    const imuPose = {
      schema_version: "rehab.imu_pose.v1" as const,
      mapping_mode: frame.source_mode === "live" ? "relative_quaternion_live" as const : "relative_quaternion_demo" as const,
      source: frame.source_mode === "live" ? "LIVE" as const : "DEMO DATA" as const,
      quaternion_order: "w,x,y,z" as const,
      sensors: {
        lower_back_imu: frame.lower_back_imu,
        upper_arm_imu: frame.upper_arm_imu,
        forearm_imu: frame.forearm_imu,
        wrist_imu: frame.wrist_imu
      }
    };
    if (frame.timestamp_ms <= this.lastFrameTimestampMs) this.droppedFrameCount += 1;
    this.lastFrameTimestampMs = Math.max(this.lastFrameTimestampMs, frame.timestamp_ms);
    this.liveFrameCount += 1;
    if (frame.source_mode === "demo" && this.demoAutoCalibrationSessionId !== frame.session_id && (base?.action_phase ?? "PREPARE") === "PREPARE") {
      this.poseMapper.calibrateFromCurrentFrame(imuPose);
      this.demoAutoCalibrationSessionId = frame.session_id;
    }
    this.updateTelemetry({
      shoulder_flexion_deg: base?.shoulder_flexion_deg ?? 0,
      shoulder_abduction_deg: base?.shoulder_abduction_deg ?? 0,
      shoulder_rotation_deg: base?.shoulder_rotation_deg ?? 0,
      elbow_flexion_deg: base?.elbow_flexion_deg ?? 0,
      forearm_pronation_deg: base?.forearm_pronation_deg ?? 0,
      wrist_flexion_deg: base?.wrist_flexion_deg ?? 0,
      trunk_flexion_deg: base?.trunk_flexion_deg ?? 0,
      trajectory_match: base?.trajectory_match ?? 0,
      phase_progress: base?.phase_progress ?? 0,
      spatial_error_cm: base?.spatial_error_cm ?? 0,
      action_phase: base?.action_phase,
      imu_pose: {
        ...imuPose
      }
    });
    this.maybeLogMotionDiagnostics(frame, imuPose);
    if (emgFrame) this.updateEmg(emgFrame);
  }
  updateTelemetry(telemetry: TelemetryPayload) { this.lastTelemetry = telemetry; const pose = this.poseMapper.map(telemetry); this.boneRig?.update(telemetry); this.segmentRig?.updateFromPose(pose, telemetry); const rightHandPosition = this.segmentRig?.getRightHandWorldPosition(new THREE.Vector3()); this.trajectory.update(telemetry, rightHandPosition); }
  applyHybridDrinkPose(pose: HybridDrinkPose, emg?: EmgPayload, lfDetected = false) {
    this.lastTelemetry = null;
    this.lastHybridPose = pose;
    this.lastLateralPose = null;
    this.setPresentationObjectMode("cup");
    this.segmentRig?.updateHybridDrinkPose(pose);
    this.updatePresentationCup(pose.cupPhase as PlaybackPhaseKey);
    this.setSessionTrunkLean(pose.trunkLeanDeg, lfDetected);
    this.setTrunkCompensationHighlight(false);
    if (emg) this.updateEmg(emg);
  }
  applyHybridLateralTransferPose(pose: HybridLateralTransferPose, emg?: EmgPayload, lfDetected = false) {
    this.lastTelemetry = null;
    this.lastHybridPose = null;
    this.lastLateralPose = pose;
    this.segmentRig?.updateHybridLateralTransferPose(pose);
    this.updatePresentationObjectForLateralTransfer(pose);
    this.setSessionTrunkLean(pose.trunkDeviationDeg, lfDetected);
    this.setTrunkCompensationHighlight(false);
    if (emg) this.updateEmg(emg);
  }
  updateEmg(emg: EmgPayload) { this.highlighter.apply(emg, this.currentDisplayMode === "muscle_bone"); }
  setTrunkCompensationHighlight(active: boolean) { this.segmentRig?.setTrunkCompensationHighlight(active); }
  calibrateActualNeutral(telemetry: TelemetryPayload) {
    if (!telemetry.imu_pose) return false;
    this.poseMapper.calibrateFromCurrentFrame(telemetry.imu_pose);
    return this.poseMapper.isCalibrationReady();
  }
  resetActualNeutral() { this.poseMapper.resetCalibration(); }
  applyActualTelemetry(telemetry: TelemetryPayload) {
    if (!telemetry.imu_pose || !this.poseMapper.isCalibrationReady()) return;
    this.updateTelemetry(telemetry);
  }
  clearExternalEmg() {
    this.updateEmg({
      CH1: { label: "EMG1 指伸肌", activation: null, quality: 0, primary: true, muscle_id: "finger_extensors_right" },
      CH2: { label: "EMG2 肱二头肌", activation: null, quality: 0, primary: true, muscle_id: "biceps_brachii_right" },
      CH3: { label: "EMG3 肱三头肌", activation: null, quality: 0, primary: true, muscle_id: "triceps_brachii_right" },
      CH4: { label: "EMG4 尺侧腕伸肌", activation: null, quality: 0, primary: true, muscle_id: "extensor_carpi_ulnaris_right" }
    });
    this.highlighter.clearPlaybackActivation();
  }
  setMeasuredTrunkLean(angleDeg: number | null, lfDetected: boolean) { this.setSessionTrunkLean(angleDeg, lfDetected); }
  clearMeasuredTrunkLean() { this.clearSessionTrunkLean(); this.setTrunkCompensationHighlight(false); }
  setSessionTrunkLean(angleDeg: number | null, lfDetected: boolean) {
    const available = typeof angleDeg === "number" && Number.isFinite(angleDeg);
    this.trunkReferenceLine.visible = available;
    this.trunkLiveLine.visible = available;
    if (!available) return;
    const radians = THREE.MathUtils.degToRad(angleDeg);
    const length = 0.72;
    const start = this.trunkLeanLabelAnchor.clone();
    const end = start.clone().add(new THREE.Vector3(0, Math.cos(radians) * length, Math.sin(radians) * length));
    this.trunkLiveLine.geometry.setFromPoints([start, end]);
    const material = this.trunkLiveLine.material as THREE.LineBasicMaterial;
    material.color.set(lfDetected ? "#8b5cf6" : "#ffffff");
    material.opacity = lfDetected ? 1 : 0.9;
    material.needsUpdate = true;
  }
  clearSessionTrunkLean() {
    this.trunkReferenceLine.visible = false;
    this.trunkLiveLine.visible = false;
  }
  applySessionEmgActivation(activation: PlaybackMuscleActivation) {
    this.highlighter.applyPlaybackActivation(activation);
  }
  applyDiagnosticPose(phase: DiagnosticPosePhase, transition?: DiagnosticPoseTransition) { this.segmentRig?.applyDiagnosticPose(phase, transition); }
  playExistingArmMotion(options: ExistingArmMotionOptions) {
    this.stopExistingArmMotion();
    this.pendingMotion = {
      ...options,
      repetitions: Math.max(1, Math.floor(options.repetitions))
    };
    if (this.pendingMotion.emgMode === "external") this.highlighter.clearPlaybackActivation();
    if (this.segmentRig) this.startExistingArmMotion();
  }
  stopExistingArmMotion() {
    this.motionGeneration += 1;
    for (const timeoutId of this.motionTimeouts) window.clearTimeout(timeoutId);
    this.motionTimeouts.clear();
    this.pendingMotion = null;
    this.lastHybridPose = null;
    this.lastLateralPose = null;
    this.segmentRig?.applyDiagnosticPose("DANGLE");
    this.updatePresentationCup("DANGLE_END");
    this.clearSessionTrunkLean();
    this.highlighter.clearPlaybackActivation();
  }
  applyShoulderAxisDebug(axis: ShoulderAxisDebug, degrees = 12) { this.segmentRig?.applyShoulderAxisDebug(axis, degrees); }
  applyElbowAxisDebug(axis: ShoulderAxisDebug, degrees = 45) { this.segmentRig?.applyElbowAxisDebug(axis, degrees); }
  getMotionDiagnostics() {
    return {
      liveFrameCount: this.liveFrameCount,
      droppedFrameCount: this.droppedFrameCount,
      lastFrameTimestampMs: this.lastFrameTimestampMs,
      trajectoryVisible: this.showTrajectory,
      referenceSkeletonVisible: this.referenceLine.visible || this.ghostArm.visible,
      maxYellowMarkerCount: this.showTrajectory ? 1 : 0,
      poseMapper: this.poseMapper.getDiagnostics(),
      segmentRig: this.segmentRig?.getMotionDiagnostics() ?? null
    };
  }
  dispose() { this.stopExistingArmMotion(); this.disposed = true; cancelAnimationFrame(this.animationId); this.resizeObserver.disconnect(); this.renderer.domElement.removeEventListener("pointerdown", this.onPointerDown); this.controls.dispose(); this.segmentRig?.dispose(); this.segmentRig = null; if (this.model) { this.highlighter.reset(this.model.originalMaterials); disposeThreeObject(this.model.root); } disposeThreeObject(this.scene); this.renderer.dispose(); this.container.replaceChildren(); }
  private async loadModel() {
    this.callbacks.onLoadStatus("正在加载人体模型");
    try {
      const modelInfo = await resolveAnatomyModelUrl();
      if (this.disposed) return;
      const result = await new AnatomyModelLoader().load(modelInfo.url, (progress) => {
        if (!this.disposed) this.callbacks.onLoadStatus(`模型加载 ${progress}%`);
      });
      if (this.disposed) {
        disposeThreeObject(result.root);
        return;
      }
      this.model = result;
      measureActualPerfSync("scene_add_ms", () => {
        this.scene.add(result.root);
      });
      measureActualPerfSync("highlighter_init_ms", () => {
        measureActualPerfSync("capture_material_states_ms", () => {
          this.captureMaterialStates(result.meshes);
        });
        measureActualPerfSync("highlighter_bind_ms", () => {
          this.highlighter.bind(result.meshes);
        });
      });
      measureActualPerfSync("rig_init_ms", () => {
        this.boneRig = new JointRigAdapter(result.bones);
        if (this.boneRig.mode === "bone-driven") this.callbacks.onRigMode("完整骨骼蒙皮模型驱动");
        else {
          this.segmentRig = new SegmentRigAdapter({ meshes: result.meshes, meshBusinessIds: result.meshBusinessIds });
          this.scene.add(this.segmentRig.group);
          this.callbacks.onRigMode(`${this.segmentRig.label} / 骨骼 ${result.rightBoneMeshSets.clavicle.length + result.rightBoneMeshSets.scapula.length + result.rightBoneMeshSets.humerus.length + result.rightBoneMeshSets.radius.length + result.rightBoneMeshSets.ulna.length + result.rightBoneMeshSets.handBones.length}`);
        }
      });
      measureActualPerfSync("object_model_setup_ms", () => {
        this.callbacks.onLoadStatus(`模型已加载：${result.meshes.length} Mesh / ${result.bones.length} Bone / ${(modelInfo.bodyGlbSize / 1024 / 1024).toFixed(1)} MB`);
        this.setDisplayMode("muscle_bone");
        this.highlighter.capturePlaybackBaseline();
        this.setCameraPreset("upper_body");
        if (this.lastTelemetry) this.updateTelemetry(this.lastTelemetry);
        if (this.lastHybridPose) this.segmentRig?.updateHybridDrinkPose(this.lastHybridPose);
        if (this.lastLateralPose) this.segmentRig?.updateHybridLateralTransferPose(this.lastLateralPose);
        if (this.pendingMotion && this.segmentRig) this.startExistingArmMotion();
      });
      finishActualPerfTrace("actual_scene_ready");
    } catch (error) {
      this.highlighter.clearPlaybackActivation();
      if (this.disposed) return;
      this.boneRig = new JointRigAdapter([]);
      this.callbacks.onRigMode("模型加载失败，保留标准轨迹和传感器面板");
      this.callbacks.onLoadStatus(error instanceof Error ? error.message : "Anatomical model integration pending.");
    }
  }
  private onPointerDown = (event: PointerEvent) => { if (!this.model) return; const label = this.picker.pick(event, this.renderer.domElement.getBoundingClientRect(), this.camera, this.model.meshes); this.callbacks.onSelected(label ?? "未选择结构"); };
  private resize() { const width = this.container.clientWidth || 1; const height = this.container.clientHeight || 1; this.camera.aspect = width / height; this.camera.updateProjectionMatrix(); this.renderer.setSize(width, height, false); }
  private animate = () => { const now = performance.now(); const deltaSeconds = (now - this.lastFrameAt) / 1000; this.lastFrameAt = now; this.segmentRig?.render(deltaSeconds); this.renderPresentationCup(); this.renderPresentationBall(); this.highlighter.renderPlaybackActivation(); this.controls.update(); this.trajectory.group.visible = this.showTrajectory; this.referenceLine.visible = false; this.ghostArm.visible = false; this.renderer.render(this.scene, this.camera); this.animationId = requestAnimationFrame(this.animate); };
  private startExistingArmMotion() {
    const options = this.pendingMotion;
    if (!options || !this.segmentRig || this.disposed) return;
    const generation = ++this.motionGeneration;

    const schedule = (delayMs: number, callback: () => void) => {
      const timeoutId = window.setTimeout(() => {
        this.motionTimeouts.delete(timeoutId);
        if (this.disposed || generation !== this.motionGeneration) return;
        callback();
      }, delayMs);
      this.motionTimeouts.add(timeoutId);
    };

    const speed = Math.max(0.25, Math.min(options.speed ?? 1, 2));
    const startIndex = Math.max(0, Math.min(options.startRepetition ?? 0, options.repetitions - 1));
    const exactSessionTiming = options.timingMode === "session_exact";

    const playRepetition = (index: number) => {
      const plan = playbackPlan(options.sessionId, index);
      let elapsedMs = 0;
      const plannedPhaseMs = plan.phases.reduce((sum, step) => sum + Math.round(step.durationMs * plan.scale), 0);
      const requestedDuration = options.repetitionDurationMs?.[index];
      const repDurationScale =
        typeof requestedDuration === "number" && Number.isFinite(requestedDuration) && requestedDuration > 0
          ? exactSessionTiming
            ? requestedDuration / Math.max(1, plannedPhaseMs)
            : Math.max(0.35, Math.min(2.8, requestedDuration / Math.max(1, plannedPhaseMs)))
          : 1;
      const phaseDurations = exactSessionTiming && typeof requestedDuration === "number" && Number.isFinite(requestedDuration) && requestedDuration > 0
        ? exactPhaseDurations(plan.phases, plan.scale, requestedDuration)
        : plan.phases.map((step) => Math.round(step.durationMs * plan.scale * repDurationScale));
      for (const [phaseIndex, step] of plan.phases.entries()) {
        const phaseElapsedMs = elapsedMs;
        const delay = Math.round(elapsedMs / speed);
        const phaseDurationMs = phaseDurations[phaseIndex] ?? 0;
        const duration = exactSessionTiming
          ? Math.max(1, Math.round(phaseDurationMs / speed))
          : Math.max(80, Math.round(phaseDurationMs / speed));
        schedule(delay, () => {
          this.applyPlaybackPhase(step.pose, playbackTransition(duration, step.easing, plan.variation), step.phase);
          options.onPhaseChange?.({ repetition: index, phase: step.phase, elapsedSeconds: phaseElapsedMs / 1000 });
        });
        elapsedMs += phaseDurationMs;
      }
      schedule(Math.round(elapsedMs / speed), () => {
        const completed = index + 1;
        options.onRepetitionComplete(completed);
      });

      elapsedMs += exactSessionTiming ? 0 : plan.betweenPauseMs;
      schedule(Math.round(elapsedMs / speed), () => {
        const completed = index + 1;
        if (completed < options.repetitions) playRepetition(completed);
        else {
          this.pendingMotion = null;
          this.applyPlaybackPhase("DANGLE", playbackTransition(280, "smootherstep"), "DANGLE_END");
          options.onPhaseChange?.({ repetition: index, phase: "DANGLE_END", elapsedSeconds: elapsedMs / 1000 });
          this.highlighter.clearPlaybackActivation();
          options.onComplete();
        }
      });
    };

    playRepetition(startIndex);
  }
  private applyPlaybackPhase(
    phase: DiagnosticPosePhase,
    transition?: DiagnosticPoseTransition,
    activationPhase: PlaybackPhaseKey = "DANGLE_START"
  ) {
    this.applyDiagnosticPose(phase, transition);
    this.updatePresentationCup(activationPhase);
    const activations: Record<PlaybackPhaseKey, PlaybackMuscleActivation> = {
      DANGLE_START: {},
      REACH: { finger_extensors_right: 0.32, biceps_brachii_right: 0.28 },
      GRASP: { finger_extensors_right: 0.62, extensor_carpi_ulnaris_right: 0.54 },
      LIFT: { biceps_brachii_right: 0.82, extensor_carpi_ulnaris_right: 0.5 },
      DRINK_HOLD: { biceps_brachii_right: 0.88, extensor_carpi_ulnaris_right: 0.68 },
      LOWER: { triceps_brachii_right: 0.7, biceps_brachii_right: 0.35 },
      RELEASE: { finger_extensors_right: 0.58 },
      RETURN_DANGLE: { triceps_brachii_right: 0.62 },
      DANGLE_END: {}
    };
    if (this.pendingMotion?.emgMode === "external") return;
    this.highlighter.applyPlaybackActivation(activations[activationPhase]);
  }
  private addPresentationSceneObjects() {
    const table = new THREE.Mesh(
      new THREE.BoxGeometry(1.32, 0.06, 0.82),
      new THREE.MeshStandardMaterial({ color: "#26464b", roughness: 0.72, metalness: 0.05 })
    );
    table.position.set(-0.82, -0.78, 0.42);
    table.castShadow = true;
    table.receiveShadow = true;
    this.scene.add(table);

    const chairSeat = new THREE.Mesh(
      new THREE.BoxGeometry(0.72, 0.08, 0.72),
      new THREE.MeshStandardMaterial({ color: "#1b3138", roughness: 0.8 })
    );
    chairSeat.position.set(0.12, -0.92, -0.18);
    chairSeat.castShadow = true;
    chairSeat.receiveShadow = true;
    this.scene.add(chairSeat);

    const chairBack = new THREE.Mesh(
      new THREE.BoxGeometry(0.74, 0.78, 0.08),
      new THREE.MeshStandardMaterial({ color: "#1a2c34", roughness: 0.82 })
    );
    chairBack.position.set(0.12, -0.56, -0.5);
    chairBack.castShadow = true;
    this.scene.add(chairBack);

    const cup = new THREE.Group();
    const body = new THREE.Mesh(
      new THREE.CylinderGeometry(0.065, 0.052, 0.18, 32),
      new THREE.MeshStandardMaterial({ color: "#d9f6ff", roughness: 0.35, metalness: 0.02 })
    );
    const rim = new THREE.Mesh(
      new THREE.TorusGeometry(0.063, 0.006, 8, 28),
      new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.3 })
    );
    body.castShadow = true;
    rim.position.y = 0.09;
    cup.add(body, rim);
    cup.position.copy(this.cupTarget);
    this.presentationCup = cup;
    this.scene.add(cup);

    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(0.075, 32, 18),
      new THREE.MeshStandardMaterial({ color: "#f59e0b", roughness: 0.42, metalness: 0.02 })
    );
    ball.name = "lateral_transfer_presentation_ball";
    ball.castShadow = true;
    ball.position.copy(this.ballTarget);
    ball.visible = false;
    this.presentationBall = ball;
    this.scene.add(ball);
  }
  private updatePresentationCup(phase: PlaybackPhaseKey) {
    this.cupAttached = phase === "GRASP" || phase === "LIFT" || phase === "DRINK_HOLD" || phase === "LOWER";
    if (this.cupAttached) this.updateCupTargetFromHand();
    else this.cupTarget.copy(this.cupTablePosition);
    if (phase === "DRINK_HOLD") this.presentationCup?.rotation.set(0.18, 0, -0.12);
    else this.presentationCup?.rotation.set(0, 0, 0);
  }
  private renderPresentationCup() {
    if (!this.presentationCup) return;
    if (this.cupAttached) this.updateCupTargetFromHand();
    const alpha = this.cupAttached ? 0.16 : 0.2;
    this.presentationCup.position.lerp(this.cupTarget, alpha);
  }
  private updatePresentationBall(pose: HybridLateralTransferPose) {
    this.ballAttached = pose.phase === "GRASP_BALL" || pose.phase === "INWARD_TRANSFER" || pose.phase === "OUTWARD_TRANSFER";
    this.ballTarget.copy(pose.ballPosition);
    if (this.ballAttached) this.updateBallTargetFromHand();
  }
  private updatePresentationObjectForLateralTransfer(pose: HybridLateralTransferPose) {
    const attached = pose.phase === "GRASP_BALL" || pose.phase === "INWARD_TRANSFER" || pose.phase === "OUTWARD_TRANSFER";
    if (this.presentationObjectMode === "cup") {
      this.ballAttached = false;
      this.cupAttached = attached;
      this.cupTarget.copy(pose.ballPosition);
      if (this.cupAttached) this.updateCupTargetFromHand();
      this.presentationCup?.rotation.set(0, 0, 0);
      return;
    }
    this.cupAttached = false;
    this.updatePresentationBall(pose);
  }
  private renderPresentationBall() {
    if (!this.presentationBall) return;
    if (this.ballAttached) this.updateBallTargetFromHand();
    this.presentationBall.position.lerp(this.ballTarget, this.ballAttached ? 0.2 : 0.24);
  }
  private updateCupTargetFromHand() {
    const hand = this.segmentRig?.getRightHandWorldPosition(new THREE.Vector3());
    if (!hand) return;
    this.cupTarget.copy(hand).add(this.cupHandOffset);
  }
  private updateBallTargetFromHand() {
    const hand = this.segmentRig?.getRightHandWorldPosition(new THREE.Vector3());
    if (!hand) return;
    this.ballTarget.copy(hand).add(this.ballHandOffset);
  }
  private maybeLogMotionDiagnostics(frame: LiveImuFrame, imuPose: NonNullable<TelemetryPayload["imu_pose"]>) {
    if (!(import.meta as ImportMeta & { env?: { DEV?: boolean } }).env?.DEV) return;
    const now = performance.now();
    if (now - this.lastDebugLogAt < 500) return;
    this.lastDebugLogAt = now;
    const sensors = imuPose.sensors;
    console.debug("[rehab-motion]", {
      timestamp_ms: frame.timestamp_ms,
      session_id: frame.session_id,
      source_mode: frame.source_mode,
      receivedFrameCount: this.liveFrameCount,
      droppedFrameCount: this.droppedFrameCount,
      lower_back_imu: sensors.lower_back_imu?.quaternion,
      upper_arm_imu: sensors.upper_arm_imu.quaternion,
      forearm_imu: sensors.forearm_imu.quaternion,
      wrist_imu: sensors.wrist_imu.quaternion,
      mapper: this.poseMapper.getDiagnostics(),
      rig: this.segmentRig?.getMotionDiagnostics()
    });
  }
  private captureMaterialStates(meshes: THREE.Mesh[]) {
    incrementActualPerfCounter("capture_material_mesh_count", meshes.length);
    for (const mesh of meshes) this.forEachMaterial(mesh, (material) => {
      incrementActualPerfCounter("capture_material_access_count");
      const standard = material as THREE.MeshStandardMaterial;
      this.materialStates.set(material, {
        opacity: material.opacity,
        transparent: material.transparent,
        depthWrite: material.depthWrite,
        color: "color" in standard ? standard.color.clone() : undefined,
        emissive: "emissive" in standard ? standard.emissive.clone() : undefined,
        emissiveIntensity: "emissiveIntensity" in standard ? standard.emissiveIntensity : undefined
      });
      incrementActualPerfCounter("capture_material_state_count");
    });
  }
  private applyDisplayMaterial(mesh: THREE.Mesh, type: string | undefined, mode: DisplayMode) {
    this.forEachMaterial(mesh, (material) => {
      this.restoreMaterial(material);
      const standard = material as THREE.MeshStandardMaterial;
      if (type === "bone" && (mode === "bone" || mode === "muscle_bone")) {
        material.transparent = false; material.opacity = 1; material.depthWrite = true; mesh.renderOrder = 4;
        if ("color" in standard) standard.color.set("#f2ead8");
        if ("emissive" in standard) { standard.emissive.set("#16120b"); standard.emissiveIntensity = 0.08; }
      } else if (type === "muscle" && mode === "muscle_bone") {
        material.transparent = true; material.opacity = 0.66; material.depthWrite = false; mesh.renderOrder = 2;
      } else {
        mesh.renderOrder = 0;
      }
      material.needsUpdate = true;
    });
  }
  private restoreMaterial(material: THREE.Material) {
    const state = this.materialStates.get(material);
    if (!state) return;
    material.opacity = state.opacity; material.transparent = state.transparent; material.depthWrite = state.depthWrite;
    const standard = material as THREE.MeshStandardMaterial;
    if (state.color && "color" in standard) standard.color.copy(state.color);
    if (state.emissive && "emissive" in standard) standard.emissive.copy(state.emissive);
    if (state.emissiveIntensity !== undefined && "emissiveIntensity" in standard) standard.emissiveIntensity = state.emissiveIntensity;
  }
  private forEachMaterial(mesh: THREE.Mesh, callback: (material: THREE.Material) => void) {
    const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const material of materials) if (material) callback(material);
  }
}

const PLAYBACK_VARIATION_VERSION = "playback-natural-v1";

function playbackPlan(sessionId: string, repetitionIndex: number): PlaybackRepetitionPlan {
  const next = seededUnit(`${sessionId}:${repetitionIndex}:${PLAYBACK_VARIATION_VERSION}`);
  const recordIsPolished = sessionId.endsWith("002") || sessionId === "TASK2";
  return {
    scale: recordIsPolished ? 0.97 : 1.03,
    phases: [
      { phase: "DANGLE_START", durationMs: 600, pose: "DANGLE", easing: "smootherstep" },
      { phase: "REACH", durationMs: variedDuration(1000, 0.04, next), pose: "REACH", easing: "easeInOutCubic" },
      { phase: "GRASP", durationMs: 250, pose: "REACH", easing: "easeInOutSine" },
      { phase: "LIFT", durationMs: variedDuration(1100, 0.04, next), pose: "LIFT", easing: "easeInOutCubic" },
      { phase: "DRINK_HOLD", durationMs: 700, pose: "APPROACH_MOUTH", easing: "smootherstep" },
      { phase: "LOWER", durationMs: variedDuration(1100, 0.04, next), pose: "RETURN", easing: "easeInOutCubic" },
      { phase: "RELEASE", durationMs: 250, pose: "RETURN", easing: "easeInOutSine" },
      { phase: "RETURN_DANGLE", durationMs: variedDuration(1100, 0.04, next), pose: "DANGLE", easing: "smootherstep" },
      { phase: "DANGLE_END", durationMs: 600, pose: "DANGLE", easing: "smootherstep" }
    ],
    betweenPauseMs: boundedPause(recordIsPolished ? 260 : 360, 35, next),
    variation: {
      shoulderFlexionDeg: centered(next) * (recordIsPolished ? 2.2 : 3.2),
      shoulderAbductionDeg: centered(next) * 2,
      shoulderRotationDeg: centered(next) * 2.5,
      elbowFlexionDeg: centered(next) * (recordIsPolished ? 3.5 : 5.5),
      wristExtensionDeg: centered(next) * 3,
      wristPronationDeg: centered(next) * 4
    }
  };
}

function playbackTransition(
  durationMs: number,
  easing: DiagnosticPoseEasing,
  variation?: DiagnosticPoseVariation
): DiagnosticPoseTransition {
  return {
    durationSeconds: durationMs / 1000,
    easing,
    variation
  };
}

function exactPhaseDurations(
  phases: PlaybackRepetitionPlan["phases"],
  planScale: number,
  requestedDurationMs: number
) {
  const requested = Math.max(1, Math.round(requestedDurationMs));
  const plannedTotal = phases.reduce((sum, step) => sum + step.durationMs * planScale, 0);
  let used = 0;
  return phases.map((step, index) => {
    if (index === phases.length - 1) return Math.max(1, requested - used);
    const duration = Math.max(1, Math.round((step.durationMs * planScale / Math.max(1, plannedTotal)) * requested));
    used += duration;
    return duration;
  });
}

function variedDuration(baseMs: number, spread: number, next: () => number) {
  return Math.round(baseMs * (1 + centered(next) * spread));
}

function boundedPause(baseMs: number, spreadMs: number, next: () => number) {
  return Math.round(baseMs + centered(next) * spreadMs);
}

function centered(next: () => number) {
  return next() * 2 - 1;
}

function seededUnit(seedText: string) {
  let state = 2166136261;
  for (let index = 0; index < seedText.length; index += 1) {
    state ^= seedText.charCodeAt(index);
    state = Math.imul(state, 16777619);
  }
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}


