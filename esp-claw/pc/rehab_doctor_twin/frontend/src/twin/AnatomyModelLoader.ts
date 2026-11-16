import * as THREE from "three";
import { addActualPerfDuration, incrementActualPerfCounter } from "./actualPerfTrace";
import { instantiateCachedAnatomyModel } from "./cachedAnatomyModel";
import { anatomyManifest, rightBoneBusinessIds, manifestItemForNodeName, manifestItemForObjectName } from "./modelManifest";

export interface RightBoneMeshSets {
  clavicle: THREE.Mesh[];
  scapula: THREE.Mesh[];
  humerus: THREE.Mesh[];
  radius: THREE.Mesh[];
  ulna: THREE.Mesh[];
  handBones: THREE.Mesh[];
}

export interface AnatomyLoadResult {
  root: THREE.Object3D;
  meshes: THREE.Mesh[];
  bones: THREE.Bone[];
  originalMaterials: Map<THREE.Mesh, THREE.Material | THREE.Material[]>;
  meshBusinessIds: Map<THREE.Mesh, string>;
  nodeNames: string[];
  missingBusinessIds: string[];
  rightBoneMeshSets: RightBoneMeshSets;
  box: THREE.Box3;
  sphere: THREE.Sphere;
  hasDraco: boolean;
}

export class AnatomyModelLoader {
  private warned = false;

  async load(url: string, onProgress: (progress: number) => void): Promise<AnatomyLoadResult> {
    const loadStart = performance.now();
    const instance = await instantiateCachedAnatomyModel(url, onProgress);
    const root = instance.root;
    root.updateMatrixWorld(true);
    const meshes: THREE.Mesh[] = [];
    const bones: THREE.Bone[] = [];
    const originalMaterials = new Map<THREE.Mesh, THREE.Material | THREE.Material[]>();
    const meshBusinessIds = new Map<THREE.Mesh, string>();
    const nodeNames: string[] = [];
    incrementActualPerfCounter("manifest_entry_count", anatomyManifest.length);

    const traversalStart = performance.now();
    root.traverse((node) => {
      nodeNames.push(node.name || node.type);
      if ((node as THREE.Mesh).isMesh) {
        const mesh = node as THREE.Mesh;
        meshes.push(mesh);
        incrementActualPerfCounter("mesh_count");
        incrementActualPerfCounter("manifest_mesh_count");
        mesh.castShadow = true;
        mesh.receiveShadow = true;
        const materialStart = performance.now();
        const cloned = Array.isArray(mesh.material)
          ? mesh.material.map((material) => material.clone())
          : mesh.material.clone();
        addActualPerfDuration("material_clone_stage2_ms", performance.now() - materialStart);
        incrementActualPerfCounter("material_clone_stage2_count", Array.isArray(mesh.material) ? mesh.material.length : 1);
        mesh.material = cloned;
        originalMaterials.set(mesh, cloned);
        const mappingStart = performance.now();
        const mapped = this.spatialManifestItemForMesh(mesh);
        addActualPerfDuration("manifest_spatial_mapping_pass_ms", performance.now() - mappingStart);
        addActualPerfDuration("manifest_mapping_ms", performance.now() - mappingStart);
        if (mapped) {
          meshBusinessIds.set(mesh, mapped.businessId);
          mesh.userData.anatomyBusinessId = mapped.businessId;
          mesh.visible = mapped.visibleByDefault;
        } else {
          mesh.visible = false;
        }
      }
      if ((node as THREE.Bone).isBone) bones.push(node as THREE.Bone);
    });
    addActualPerfDuration("mesh_traversal_ms", performance.now() - traversalStart);

    const manifestStart = performance.now();
    root.traverse((node) => {
      const lookupStart = performance.now();
      const mapped = manifestItemForNodeName(node.name || "");
      addActualPerfDuration("manifest_lookup_ms", performance.now() - lookupStart);
      incrementActualPerfCounter("manifest_name_lookup_count");
      incrementActualPerfCounter("manifest_find_scan_count", anatomyManifest.length);
      if (!mapped) return;
      node.traverse((child) => {
        if (!(child as THREE.Mesh).isMesh) return;
        const mesh = child as THREE.Mesh;
        if (!this.meshMatchesSide(mesh, mapped.side)) return;
        if (!meshBusinessIds.has(mesh)) {
          meshBusinessIds.set(mesh, mapped.businessId);
          mesh.userData.anatomyBusinessId = mapped.businessId;
          mesh.visible = mapped.visibleByDefault;
        }
      });
    });
    addActualPerfDuration("manifest_mapping_ms", performance.now() - manifestStart);
    addActualPerfDuration("manifest_node_name_mapping_pass_ms", performance.now() - manifestStart);

    const matched = new Set(meshBusinessIds.values());
    const missingBusinessIds = anatomyManifest.filter((item) => item.focusUpperLimb && !matched.has(item.businessId)).map((item) => item.businessId);
    const rightBoneStart = performance.now();
    const rightBoneMeshSets = this.createRightBoneMeshSets(meshes, meshBusinessIds);
    addActualPerfDuration("manifest_right_bone_mesh_set_ms", performance.now() - rightBoneStart);
    for (const [key, businessId] of Object.entries(rightBoneBusinessIds)) {
      if (rightBoneMeshSets[key as keyof RightBoneMeshSets].length === 0 && !missingBusinessIds.includes(businessId)) {
        missingBusinessIds.push(businessId);
      }
    }
    if (missingBusinessIds.length && !this.warned) {
      this.warned = true;
      console.warn(`[AnatomyModelLoader] Missing mapped anatomy nodes: ${missingBusinessIds.join(", ")}`);
    }

    const boundsStart = performance.now();
    const box = new THREE.Box3().setFromObject(root);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    root.position.sub(center);
    const maxAxis = Math.max(size.x, size.y, size.z);
    if (maxAxis > 0) root.scale.setScalar(3.6 / maxAxis);
    const normalizedBox = new THREE.Box3().setFromObject(root);
    const sphere = normalizedBox.getBoundingSphere(new THREE.Sphere());
    addActualPerfDuration("bounds_normalize_ms", performance.now() - boundsStart);
    addActualPerfDuration("AnatomyModelLoader_load_total_ms", performance.now() - loadStart);

    return {
      root,
      meshes,
      bones,
      originalMaterials,
      meshBusinessIds,
      nodeNames,
      missingBusinessIds,
      rightBoneMeshSets,
      box: normalizedBox,
      sphere,
      hasDraco: instance.hasDraco
    };
  }

  private spatialManifestItemForMesh(mesh: THREE.Mesh) {
    incrementActualPerfCounter("manifest_spatial_lookup_count");
    incrementActualPerfCounter("manifest_find_scan_count", anatomyManifest.length);
    const lookupStart = performance.now();
    const mapped = manifestItemForObjectName(mesh);
    addActualPerfDuration("manifest_lookup_ms", performance.now() - lookupStart);
    if (!mapped) return undefined;
    return this.meshMatchesSide(mesh, mapped.side) ? mapped : undefined;
  }

  private meshMatchesSide(mesh: THREE.Mesh, side: string) {
    incrementActualPerfCounter("manifest_side_match_count");
    const sideStart = performance.now();
    if (side !== "right") {
      addActualPerfDuration("manifest_side_match_ms", performance.now() - sideStart);
      return true;
    }
    const boundsStart = performance.now();
    const box = new THREE.Box3().setFromObject(mesh);
    const center = box.getCenter(new THREE.Vector3());
    addActualPerfDuration("manifest_spatial_bounds_ms", performance.now() - boundsStart);
    incrementActualPerfCounter("manifest_bounds_count");
    addActualPerfDuration("manifest_side_match_ms", performance.now() - sideStart);
    // body.glb currently exposes the clinically displayed right upper limb on
    // the author's negative-X mesh side. Keep the mesh as-is; do not mirror.
    return center.x < -0.0005;
  }

  private createRightBoneMeshSets(meshes: THREE.Mesh[], meshBusinessIds: Map<THREE.Mesh, string>): RightBoneMeshSets {
    const sets: RightBoneMeshSets = {
      clavicle: [],
      scapula: [],
      humerus: [],
      radius: [],
      ulna: [],
      handBones: []
    };
    for (const mesh of meshes) {
      const businessId = meshBusinessIds.get(mesh) ?? mesh.userData.anatomyBusinessId;
      if (businessId === rightBoneBusinessIds.clavicle) sets.clavicle.push(mesh);
      if (businessId === rightBoneBusinessIds.scapula) sets.scapula.push(mesh);
      if (businessId === rightBoneBusinessIds.humerus) sets.humerus.push(mesh);
      if (businessId === rightBoneBusinessIds.radius) sets.radius.push(mesh);
      if (businessId === rightBoneBusinessIds.ulna) sets.ulna.push(mesh);
      if (businessId === rightBoneBusinessIds.handBones) sets.handBones.push(mesh);
    }
    return sets;
  }
}

