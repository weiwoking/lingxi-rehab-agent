import * as THREE from "three";
import type { EmgPayload } from "../contracts/realtimeMessages";
import { addActualPerfDuration, incrementActualPerfCounter } from "./actualPerfTrace";
import { anatomyManifest, manifestItemForBusinessId, manifestItemForObjectName, semgToBusinessId } from "./modelManifest";

export type PlaybackMuscleBusinessId =
  | "finger_extensors_right"
  | "biceps_brachii_right"
  | "triceps_brachii_right"
  | "extensor_carpi_ulnaris_right";

export type PlaybackMuscleActivation = Partial<Record<PlaybackMuscleBusinessId, number>>;

interface PlaybackMaterialState {
  emissive: THREE.Color;
  emissiveIntensity: number;
  opacity: number;
}

export class MuscleHighlighter {
  private meshByBusinessId = new Map<string, THREE.Mesh[]>();
  private playbackBaseline = new Map<THREE.MeshStandardMaterial, PlaybackMaterialState>();
  private playbackActivations: PlaybackMuscleActivation = {};
  private playbackActive = false;
  private scratch = new THREE.Color();
  private low = new THREE.Color("#5a1f22");
  private mid = new THREE.Color("#c34a2b");
  private high = new THREE.Color("#f2a93b");

  bind(meshes: THREE.Mesh[]) {
    this.meshByBusinessId.clear();
    incrementActualPerfCounter("highlighter_mesh_count", meshes.length);
    incrementActualPerfCounter("highlighter_manifest_entry_count", anatomyManifest.length);
    const knownBusinessIds = new Set(anatomyManifest.map((item) => item.businessId));
    const meshesByBusinessId = new Map<string, THREE.Mesh[]>();
    const scanStart = performance.now();
    for (const mesh of meshes) {
      const explicitBusinessId = mesh.userData.anatomyBusinessId;
      let businessId = explicitBusinessId;
      if (businessId === undefined || businessId === null) {
        const mappingStart = performance.now();
        const mapped = manifestItemForObjectName(mesh);
        addActualPerfDuration("highlighter_bind_mapping_ms", performance.now() - mappingStart);
        incrementActualPerfCounter("highlighter_manifest_lookup_count");
        businessId = mapped?.businessId;
      }
      if (typeof businessId !== "string" || !knownBusinessIds.has(businessId)) continue;
      const group = meshesByBusinessId.get(businessId);
      if (group) group.push(mesh);
      else meshesByBusinessId.set(businessId, [mesh]);
    }
    addActualPerfDuration("highlighter_bind_scan_ms", performance.now() - scanStart);
    for (const item of anatomyManifest) {
      const matched = meshesByBusinessId.get(item.businessId) ?? [];
      this.meshByBusinessId.set(item.businessId, matched);
      incrementActualPerfCounter("highlighter_group_count");
    }
  }

  capturePlaybackBaseline() {
    this.playbackBaseline.clear();
    const baselineStart = performance.now();
    for (const [businessId, meshes] of this.meshByBusinessId) {
      const item = manifestItemForBusinessId(businessId);
      if (item?.type !== "muscle" || item.side !== "right") continue;
      for (const mesh of meshes) {
        const material = this.standardMaterial(mesh);
        if (!material) continue;
        incrementActualPerfCounter("highlighter_material_state_count");
        this.playbackBaseline.set(material, {
          emissive: material.emissive.clone(),
          emissiveIntensity: material.emissiveIntensity,
          opacity: material.opacity
        });
      }
    }
    addActualPerfDuration("highlighter_playback_baseline_material_state_ms", performance.now() - baselineStart);
  }

  apply(emg: EmgPayload, overlayMode = false) {
    const activations = {
      [semgToBusinessId.CH1]: emg.CH1.activation,
      [semgToBusinessId.CH2]: emg.CH2.activation,
      [semgToBusinessId.CH3]: emg.CH3.activation,
      [semgToBusinessId.CH4]: emg.CH4.activation
    };
    for (const [businessId, meshes] of this.meshByBusinessId) {
      const activation = activations[businessId as keyof typeof activations] ?? 0;
      for (const mesh of meshes) {
        const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
        if (!material || !("emissive" in material)) continue;
        const standard = material as THREE.MeshStandardMaterial;
        if (activation <= 0.2) {
          standard.emissiveIntensity = THREE.MathUtils.lerp(standard.emissiveIntensity, 0, 0.12);
          if (overlayMode && standard.transparent) standard.opacity = THREE.MathUtils.lerp(standard.opacity, 0.62, 0.12);
          continue;
        }
        const target = activation < 0.5 ? this.low : activation < 0.8 ? this.mid : this.high;
        this.scratch.copy(target);
        standard.emissive.lerp(this.scratch, 0.12);
        standard.emissiveIntensity = THREE.MathUtils.lerp(standard.emissiveIntensity, 0.25 + activation * 0.55, 0.12);
        if (overlayMode && standard.transparent) standard.opacity = THREE.MathUtils.lerp(standard.opacity, Math.min(0.85, 0.64 + activation * 0.25), 0.18);
      }
    }
  }

  applyPlaybackActivation(activations: PlaybackMuscleActivation) {
    this.playbackActive = true;
    this.playbackActivations = { ...activations };
  }

  renderPlaybackActivation() {
    if (!this.playbackActive) return;
    for (const [businessId, meshes] of this.meshByBusinessId) {
      const item = manifestItemForBusinessId(businessId);
      if (item?.type !== "muscle" || item.side !== "right") continue;
      const activation = this.playbackActivations[businessId as PlaybackMuscleBusinessId] ?? 0;
      for (const mesh of meshes) {
        const material = this.standardMaterial(mesh);
        const baseline = material ? this.playbackBaseline.get(material) : undefined;
        if (!material || !baseline) continue;
        if (activation <= 0) {
          material.emissive.lerp(baseline.emissive, 0.12);
          material.emissiveIntensity = THREE.MathUtils.lerp(material.emissiveIntensity, baseline.emissiveIntensity, 0.12);
          material.opacity = THREE.MathUtils.lerp(material.opacity, baseline.opacity, 0.12);
          continue;
        }
        const target = activation < 0.5 ? this.low : activation < 0.8 ? this.mid : this.high;
        material.emissive.lerp(this.scratch.copy(target), 0.12);
        material.emissiveIntensity = THREE.MathUtils.lerp(material.emissiveIntensity, 0.25 + activation * 0.55, 0.12);
        if (material.transparent) {
          material.opacity = THREE.MathUtils.lerp(material.opacity, Math.min(0.85, 0.64 + activation * 0.25), 0.18);
        }
      }
    }
  }

  clearPlaybackActivation() {
    this.playbackActive = false;
    this.playbackActivations = {};
    for (const [material, baseline] of this.playbackBaseline) {
      material.emissive.copy(baseline.emissive);
      material.emissiveIntensity = baseline.emissiveIntensity;
      material.opacity = baseline.opacity;
    }
  }

  reset(originalMaterials: Map<THREE.Mesh, THREE.Material | THREE.Material[]>) {
    this.clearPlaybackActivation();
    for (const [mesh, material] of originalMaterials) mesh.material = material;
  }

  private standardMaterial(mesh: THREE.Mesh) {
    const material = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    return material instanceof THREE.MeshStandardMaterial ? material : null;
  }
}
