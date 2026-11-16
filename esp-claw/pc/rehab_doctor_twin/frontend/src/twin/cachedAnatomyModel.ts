import * as THREE from "three";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { GLTFLoader, type GLTF } from "three/examples/jsm/loaders/GLTFLoader.js";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";
import {
  addActualPerfDuration,
  incrementActualPerfCounter,
  measureActualPerfAsync,
  measureActualPerfSync,
  setActualPerfCacheState
} from "./actualPerfTrace";

export interface AnatomyModelUrlInfo {
  url: string;
  bodyGlbSize: number;
}

interface ModelStatusResponse {
  body_glb_present: boolean;
  body_glb_size?: number;
  rigged_glb_present?: boolean;
}

interface ParsedAnatomyTemplate {
  url: string;
  gltf: GLTF;
}

const parsedTemplateCache = new Map<string, Promise<ParsedAnatomyTemplate>>();
const parsedTemplateState = new Map<string, "loading" | "ready">();
let resolvedModelUrlPromise: Promise<AnatomyModelUrlInfo> | null = null;
let sharedDracoLoader: DRACOLoader | null = null;

export function resolveAnatomyModelUrl(): Promise<AnatomyModelUrlInfo> {
  setActualPerfCacheState({ modelStatusCacheHit: Boolean(resolvedModelUrlPromise) });
  if (!resolvedModelUrlPromise) {
    resolvedModelUrlPromise = fetch("/api/model/status")
      .then(async (response) => {
        if (!response.ok) throw new Error("model_status_unavailable");
        const status = (await response.json()) as ModelStatusResponse;
        if (!status.body_glb_present) throw new Error("body.glb missing");
        return {
          url: status.rigged_glb_present ? "/models/rehab_upper_limb_rigged.glb" : "/models/body.glb",
          bodyGlbSize: status.body_glb_size ?? 0
        };
      })
      .catch((error) => {
        resolvedModelUrlPromise = null;
        throw error;
      });
  }
  return resolvedModelUrlPromise;
}

export function prewarmAnatomyModel(): Promise<void> {
  return resolveAnatomyModelUrl()
    .then((info) => loadParsedTemplate(info.url, () => undefined))
    .then(() => undefined);
}

export async function instantiateCachedAnatomyModel(
  url: string,
  onProgress: (progress: number) => void
): Promise<{ root: THREE.Object3D; hasDraco: boolean }> {
  const template = await measureActualPerfAsync("get_cached_template_ms", () => loadParsedTemplate(url, onProgress));
  onProgress(100);
  return {
    root: cloneTemplateRoot(template.gltf.scene),
    hasDraco: true
  };
}

function loadParsedTemplate(
  url: string,
  onProgress: (progress: number) => void
): Promise<ParsedAnatomyTemplate> {
  const cached = parsedTemplateCache.get(url);
  if (cached) {
    const state = parsedTemplateState.get(url);
    setActualPerfCacheState({
      modelTemplateCacheHit: state === "ready",
      modelTemplateInflight: state === "loading"
    });
    return cached;
  }

  setActualPerfCacheState({ modelTemplateCacheHit: false, modelTemplateInflight: false });
  parsedTemplateState.set(url, "loading");
  const promise = loadParsedTemplateCold(url, onProgress).catch((error) => {
    parsedTemplateCache.delete(url);
    parsedTemplateState.delete(url);
    throw error;
  }).then((template) => {
    parsedTemplateState.set(url, "ready");
    return template;
  });
  parsedTemplateCache.set(url, promise);
  return promise;
}

async function loadParsedTemplateCold(
  url: string,
  onProgress: (progress: number) => void
): Promise<ParsedAnatomyTemplate> {
  const manager = new THREE.LoadingManager();
  manager.onProgress = (_url, loaded, total) => onProgress(total ? Math.round((loaded / total) * 100) : 0);

  const loader = new GLTFLoader(manager);
  loader.setDRACOLoader(sharedDraco());
  const gltf = await loader.loadAsync(url, (event) => {
    if (event.total > 0) onProgress(Math.round((event.loaded / event.total) * 100));
  });
  return { url, gltf };
}

function sharedDraco() {
  if (!sharedDracoLoader) {
    sharedDracoLoader = new DRACOLoader();
    sharedDracoLoader.setDecoderPath("/draco/");
    sharedDracoLoader.preload();
  }
  return sharedDracoLoader;
}

function cloneTemplateRoot(root: THREE.Object3D) {
  const instance = measureActualPerfSync("skeleton_clone_ms", () => SkeletonUtils.clone(root));
  instance.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    if (mesh.geometry) {
      const start = performance.now();
      mesh.geometry = mesh.geometry.clone();
      addActualPerfDuration("geometry_clone_ms", performance.now() - start);
      incrementActualPerfCounter("geometry_clone_count");
    }
    mesh.material = cloneMaterialLike(mesh.material);
  });
  return instance;
}

function cloneMaterialLike<T extends THREE.Material | THREE.Material[]>(material: T): T {
  if (Array.isArray(material)) {
    return material.map((item) => cloneMaterialWithTextures(item)) as T;
  }
  return cloneMaterialWithTextures(material) as T;
}

function cloneMaterialWithTextures(material: THREE.Material) {
  const materialStart = performance.now();
  const cloned = material.clone();
  addActualPerfDuration("material_clone_stage1_ms", performance.now() - materialStart);
  incrementActualPerfCounter("material_clone_stage1_count");
  const fields = cloned as THREE.Material & Record<string, unknown>;
  for (const key of Object.keys(fields)) {
    const value = fields[key];
    if (value instanceof THREE.Texture) {
      const textureStart = performance.now();
      fields[key] = value.clone();
      addActualPerfDuration("texture_clone_ms", performance.now() - textureStart);
      incrementActualPerfCounter("texture_clone_count");
    }
  }
  return cloned;
}
