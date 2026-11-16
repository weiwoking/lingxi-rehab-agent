export const REHAB_ACTUAL_PERF_TRACE = true;

export interface ActualPerfContext {
  sessionId?: string;
  scoreProfile?: string | null;
  objectType?: string | null;
}

type ActualPerfCacheState = {
  modelStatusCacheHit?: boolean;
  modelTemplateCacheHit?: boolean;
  modelTemplateInflight?: boolean;
};

interface ActualPerfTrace {
  context: ActualPerfContext;
  startMs: number;
  marks: Record<string, number>;
  durations: Record<string, number>;
  counters: Record<string, number>;
  cache: ActualPerfCacheState;
  emitted: boolean;
}

let activeTrace: ActualPerfTrace | null = null;

export function startActualPerfTrace(context: ActualPerfContext) {
  if (!REHAB_ACTUAL_PERF_TRACE) return;
  activeTrace = {
    context,
    startMs: performance.now(),
    marks: {},
    durations: {},
    counters: {},
    cache: {},
    emitted: false
  };
  markActualPerf("actual_entry_start");
}

export function ensureActualPerfTrace(context: ActualPerfContext) {
  if (!REHAB_ACTUAL_PERF_TRACE) return;
  if (!activeTrace || activeTrace.emitted) startActualPerfTrace(context);
  else updateActualPerfContext(context);
}

export function updateActualPerfContext(context: ActualPerfContext) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return;
  activeTrace.context = { ...activeTrace.context, ...context };
}

export function markActualPerf(name: string) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return;
  activeTrace.marks[name] = performance.now();
}

export function addActualPerfDuration(name: string, durationMs: number) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace || !Number.isFinite(durationMs)) return;
  activeTrace.durations[name] = (activeTrace.durations[name] ?? 0) + Math.max(0, durationMs);
}

export function incrementActualPerfCounter(name: string, amount = 1) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return;
  activeTrace.counters[name] = (activeTrace.counters[name] ?? 0) + amount;
}

export function setActualPerfCacheState(state: ActualPerfCacheState) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return;
  activeTrace.cache = { ...activeTrace.cache, ...state };
}

export function measureActualPerfSync<T>(name: string, callback: () => T): T {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return callback();
  const start = performance.now();
  try {
    return callback();
  } finally {
    addActualPerfDuration(name, performance.now() - start);
  }
}

export async function measureActualPerfAsync<T>(name: string, callback: () => Promise<T>): Promise<T> {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace) return callback();
  const start = performance.now();
  try {
    return await callback();
  } finally {
    addActualPerfDuration(name, performance.now() - start);
  }
}

export function finishActualPerfTrace(reason: string) {
  if (!REHAB_ACTUAL_PERF_TRACE || !activeTrace || activeTrace.emitted) return;
  activeTrace.emitted = true;
  const endMs = performance.now();
  const totalMs = endMs - activeTrace.startMs;
  const durations = activeTrace.durations;
  const marks = activeTrace.marks;
  const accounted = Object.values(durations).reduce((sum, value) => sum + value, 0);
  const sceneStart = marks.AnatomyScene_construct_start;
  const sceneEnd = marks.AnatomyScene_construct_end;
  const actualReady = marks.actual_ready_timestamp;
  const payload = {
    session_id: activeTrace.context.sessionId ?? null,
    score_profile: activeTrace.context.scoreProfile ?? null,
    object_type: activeTrace.context.objectType ?? null,
    reason,
    total_ms: roundPerf(totalMs),
    actual_request_timestamp: roundPerf(marks.actual_entry_start - activeTrace.startMs),
    AnatomyScene_construct_start_ms: offsetOrNull(sceneStart, activeTrace.startMs),
    AnatomyScene_construct_end_ms: offsetOrNull(sceneEnd, activeTrace.startMs),
    actual_ready_timestamp_ms: offsetOrNull(actualReady, activeTrace.startMs),
    pre_scene_delay_ms: sceneStart ? roundPerf(sceneStart - activeTrace.startMs) : null,
    scene_construct_ms: roundPerf(durations.scene_construct_ms ?? 0),
    post_scene_delay_ms: sceneEnd ? roundPerf(endMs - sceneEnd) : null,
    renderer_scene_init_ms: roundPerf(durations.renderer_scene_init_ms ?? 0),
    camera_controls_setup_ms: roundPerf(durations.camera_controls_setup_ms ?? 0),
    scene_static_setup_ms: roundPerf(durations.scene_static_setup_ms ?? 0),
    object_model_setup_ms: roundPerf(durations.object_model_setup_ms ?? 0),
    animation_setup_ms: roundPerf(durations.animation_setup_ms ?? 0),
    AnatomyScene_constructor_body_ms: roundPerf(durations.AnatomyScene_constructor_body_ms ?? 0),
    AnatomyModelLoader_load_total_ms: roundPerf(durations.AnatomyModelLoader_load_total_ms ?? 0),
    get_cached_template_ms: roundPerf(durations.get_cached_template_ms ?? 0),
    skeleton_clone_ms: roundPerf(durations.skeleton_clone_ms ?? 0),
    geometry_clone_ms: roundPerf(durations.geometry_clone_ms ?? 0),
    material_clone_stage1_ms: roundPerf(durations.material_clone_stage1_ms ?? 0),
    material_clone_stage2_ms: roundPerf(durations.material_clone_stage2_ms ?? 0),
    texture_clone_ms: roundPerf(durations.texture_clone_ms ?? 0),
    mesh_traversal_ms: roundPerf(durations.mesh_traversal_ms ?? 0),
    manifest_mapping_ms: roundPerf(durations.manifest_mapping_ms ?? 0),
    manifest_node_name_mapping_pass_ms: roundPerf(durations.manifest_node_name_mapping_pass_ms ?? 0),
    manifest_spatial_mapping_pass_ms: roundPerf(durations.manifest_spatial_mapping_pass_ms ?? 0),
    manifest_right_bone_mesh_set_ms: roundPerf(durations.manifest_right_bone_mesh_set_ms ?? 0),
    manifest_lookup_ms: roundPerf(durations.manifest_lookup_ms ?? 0),
    manifest_spatial_bounds_ms: roundPerf(durations.manifest_spatial_bounds_ms ?? 0),
    manifest_side_match_ms: roundPerf(durations.manifest_side_match_ms ?? 0),
    bounds_normalize_ms: roundPerf(durations.bounds_normalize_ms ?? 0),
    scene_add_ms: roundPerf(durations.scene_add_ms ?? 0),
    highlighter_init_ms: roundPerf(durations.highlighter_init_ms ?? 0),
    capture_material_states_ms: roundPerf(durations.capture_material_states_ms ?? 0),
    highlighter_bind_ms: roundPerf(durations.highlighter_bind_ms ?? 0),
    highlighter_bind_scan_ms: roundPerf(durations.highlighter_bind_scan_ms ?? 0),
    highlighter_bind_mapping_ms: roundPerf(durations.highlighter_bind_mapping_ms ?? 0),
    highlighter_playback_baseline_material_state_ms: roundPerf(durations.highlighter_playback_baseline_material_state_ms ?? 0),
    rig_init_ms: roundPerf(durations.rig_init_ms ?? 0),
    mesh_count: activeTrace.counters.mesh_count ?? 0,
    manifest_entry_count: activeTrace.counters.manifest_entry_count ?? 0,
    manifest_mesh_count: activeTrace.counters.manifest_mesh_count ?? 0,
    manifest_name_lookup_count: activeTrace.counters.manifest_name_lookup_count ?? 0,
    manifest_spatial_lookup_count: activeTrace.counters.manifest_spatial_lookup_count ?? 0,
    manifest_find_scan_count: activeTrace.counters.manifest_find_scan_count ?? 0,
    manifest_bounds_count: activeTrace.counters.manifest_bounds_count ?? 0,
    manifest_side_match_count: activeTrace.counters.manifest_side_match_count ?? 0,
    highlighter_mesh_count: activeTrace.counters.highlighter_mesh_count ?? 0,
    highlighter_manifest_entry_count: activeTrace.counters.highlighter_manifest_entry_count ?? 0,
    highlighter_manifest_lookup_count: activeTrace.counters.highlighter_manifest_lookup_count ?? 0,
    highlighter_find_count: activeTrace.counters.highlighter_find_count ?? 0,
    highlighter_material_state_count: activeTrace.counters.highlighter_material_state_count ?? 0,
    highlighter_group_count: activeTrace.counters.highlighter_group_count ?? 0,
    capture_material_mesh_count: activeTrace.counters.capture_material_mesh_count ?? 0,
    capture_material_access_count: activeTrace.counters.capture_material_access_count ?? 0,
    capture_material_state_count: activeTrace.counters.capture_material_state_count ?? 0,
    geometry_count: activeTrace.counters.geometry_clone_count ?? 0,
    material_count: (activeTrace.counters.material_clone_stage1_count ?? 0) + (activeTrace.counters.material_clone_stage2_count ?? 0),
    material_clone_stage1_count: activeTrace.counters.material_clone_stage1_count ?? 0,
    material_clone_stage2_count: activeTrace.counters.material_clone_stage2_count ?? 0,
    texture_count: activeTrace.counters.texture_clone_count ?? 0,
    model_status_cache_hit: activeTrace.cache.modelStatusCacheHit ?? null,
    model_template_cache_hit: activeTrace.cache.modelTemplateCacheHit ?? null,
    model_template_inflight: activeTrace.cache.modelTemplateInflight ?? null,
    unaccounted_ms: roundPerf(Math.max(0, totalMs - accounted))
  };
  console.log("[REHAB_ACTUAL_PERF]", payload);
}

function offsetOrNull(value: number | undefined, base: number) {
  return typeof value === "number" ? roundPerf(value - base) : null;
}

function roundPerf(value: number) {
  return Math.round(value * 100) / 100;
}
