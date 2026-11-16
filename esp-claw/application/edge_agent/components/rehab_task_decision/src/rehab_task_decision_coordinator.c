// SPDX-License-Identifier: Apache-2.0
#include "rehab_task_decision_priv.h"
#include "rehab_business_audio.h"

#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "claw_paths.h"
#include "claw_agent_mgr.h"
#include "claw_memory.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "mbedtls/sha256.h"
#include "rehab_task_decision_formal_input_artifact.h"
#include "rehab_task_decision_llm_input_adapter.h"
#include "rehab_task_decision_llm_input_artifact.h"
#include "rehab_task_decision_scoring.h"
#include "rehab_task_target_resolver.h"

#define FORMAL_SCENE_SCHEMA "stage8b4a_p4_decision_package.v1"
#define FORMAL_SELECTED_SCHEMA "rehab_selected_task.v2"
#define FORMAL_SCENE_SUBPATH "rehab/latest_successful_scene_result.json"
#define FORMAL_SCENE_MAX_BYTES (64U * 1024U)
#define FORMAL_COORDINATOR_STACK (16U * 1024U)
#define FORMAL_COORDINATOR_PRIORITY 5U
#define FORMAL_WAIT_SLICE_MS 50U
#define FORMAL_DEFAULT_LLM_TIMEOUT_MS 100000U
#define FORMAL_MIN_LLM_TIMEOUT_MS 15000U
#define FORMAL_MAX_LLM_TOKENS 4096U
#define FORMAL_ROOT_SUBMIT_TIMEOUT_MS 1000U
#define FORMAL_ROOT_COMPLETION_TIMEOUT_MS 100000U
#define FORMAL_ROOT_WAITER_STACK (4U * 1024U)

static const char *TAG = "rehab_decision_coord";

static claw_core_llm_request_options_t formal_reasoning_options(void)
{
    claw_core_llm_request_options_t options = {
        .scope = "rehab_formal",
    };
#if CONFIG_REHAB_DECISION_THINKING_DISABLED
    options.thinking_mode = CLAW_CORE_LLM_THINKING_DISABLED;
#else
    options.thinking_mode = CLAW_CORE_LLM_THINKING_ENABLED;
    options.reasoning_effort = CLAW_CORE_LLM_REASONING_EFFORT_HIGH;
#endif
    return options;
}

#define LLM_ADVICE_SEMANTICS_TEXT \
    "user_condition is free natural language. DeepSeek must evaluate every " \
    "candidate independently using raw_text, task_name, level, action_family, " \
    "semantic_group, required_plane, target_facts.raw_class, selected target, " \
    "and all available object facts. Interpret meaning rather than matching a " \
    "fixed vocabulary: " \
    "understand negation, degree, conjunction, exceptions, the real motion goal, " \
    "and object-specific scope. Sharing an object does not make drinking and cup " \
    "lateral transfer the same action. Use 20 when the prescription has no " \
    "relevant explicit positive or negative direction for this candidate. Use " \
    "21..25 only for actual positive, priority, or encouragement semantics for " \
    "this candidate, with stronger and more direct support nearer 25. Use 16..19 " \
    "only for actual negative, reduction, lower-priority, or avoidance semantics " \
    "for this candidate, with stronger and more direct opposition nearer 16. " \
    "Lack of positive support is not negative evidence: encouraging A must not " \
    "automatically lower an unrelated B. One sentence may encourage A, reduce B, " \
    "and prohibit C. Ordinary preferences only change integer advice 16..25. " \
    "A soft negative preference must not hard-block. Only an explicit strong " \
    "medical prohibition may change an input-available candidate to blocked, " \
    "and only for candidates within that prohibition's semantic/object scope. " \
    "An input-blocked candidate can never become available. DeepSeek performs " \
    "this interpretation; P4 checks machine invariants and provenance but does " \
    "not implement a second natural-language classifier."
#define LLM_REASON_CONTRACT_TEXT \
    "For every candidate return a concise, non-empty, readable reason. It may " \
    "explain relevant facts and advice naturally, in any wording or order; it " \
    "does not need to repeat task names, numeric components, evidence, target " \
    "tokens, heights, scores, or a fixed language template."
#define LLM_BLOCKED_REASON_CONTRACT_TEXT \
    "For an input-blocked candidate, describe the gate fact readably without a " \
    "fixed phrase. For an input-available candidate changed to blocked, " \
    "advice_evidence must be a non-empty continuous verbatim substring of " \
    "patient_condition.raw_text that supports the prohibition. The reason may " \
    "paraphrase and does not need to quote evidence or use canonical group names."
#define LLM_SUMMARY_CONTRACT_TEXT \
    "Return a concise, non-empty, readable summary of the result. It may use any " \
    "natural wording, language, punctuation and fact order; do not include hidden " \
    "reasoning, and do not follow a fixed token template."
#define LLM_ALLOWED_FIELDS_TEXT \
    "root:schema_version,decision_id,advice_basis,candidates,winner_ref,summary; " \
    "advice_basis:direction,evidence; " \
    "candidate:candidate_ref,availability,advice_evidence,score_breakdown,score,reason; " \
    "breakdown:difficulty,advice,compensation,frequency,perception,final_raw"
#define LLM_FORBIDDEN_FIELDS_TEXT \
    "task_id,task_name,winner_task_id,winner_task_name," \
    "advice_interpretation, hidden reasoning and every unknown field"
#define LLM_OUTPUT_SCHEMA_TEXT \
    "{\"$schema\":\"https://json-schema.org/draft/2020-12/schema\"," \
    "\"type\":\"object\",\"additionalProperties\":false," \
    "\"required\":[\"schema_version\",\"decision_id\",\"advice_basis\",\"candidates\"," \
    "\"winner_ref\",\"summary\"],\"properties\":{" \
    "\"schema_version\":{\"const\":\"rehab_task_decision_output.v4\"}," \
    "\"decision_id\":{\"type\":\"string\",\"minLength\":1}," \
    "\"advice_basis\":{\"type\":\"object\",\"additionalProperties\":false," \
    "\"required\":[\"direction\",\"evidence\"],\"properties\":{" \
    "\"direction\":{\"enum\":[\"cup\",\"ball\",\"neutral\",\"mixed\"]}," \
    "\"evidence\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":511}}}," \
    "\"candidates\":{\"type\":\"array\",\"minItems\":5,\"maxItems\":5," \
    "\"items\":{\"type\":\"object\",\"additionalProperties\":false," \
    "\"required\":[\"candidate_ref\",\"availability\",\"advice_evidence\"," \
    "\"score_breakdown\",\"score\",\"reason\"],\"properties\":{" \
    "\"candidate_ref\":{\"type\":\"integer\",\"minimum\":0,\"maximum\":4}," \
    "\"availability\":{\"enum\":[\"available\",\"blocked\"]}," \
    "\"advice_evidence\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":511}," \
    "\"score_breakdown\":{\"oneOf\":[{\"type\":\"null\"},{\"type\":\"object\"," \
    "\"additionalProperties\":false,\"required\":[\"difficulty\"," \
    "\"advice\",\"compensation\",\"frequency\",\"perception\"," \
    "\"final_raw\"],\"properties\":{\"difficulty\":" \
    "{\"type\":\"number\",\"minimum\":0,\"maximum\":35}," \
    "\"advice\":{\"type\":\"integer\",\"minimum\":16,\"maximum\":25}," \
    "\"compensation\":{\"type\":\"number\",\"minimum\":0,\"maximum\":20}," \
    "\"frequency\":{\"type\":\"number\",\"minimum\":0,\"maximum\":10}," \
    "\"perception\":{\"type\":\"number\",\"minimum\":6,\"maximum\":10}," \
    "\"final_raw\":{\"type\":\"number\"}}}]}," \
    "\"score\":{\"type\":\"number\",\"minimum\":0,\"maximum\":100}," \
    "\"reason\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":511}}," \
    "\"allOf\":[{\"if\":{\"properties\":{\"availability\":{" \
    "\"const\":\"blocked\"}}},\"then\":{\"properties\":{" \
    "\"score_breakdown\":{\"type\":\"null\"},\"score\":{\"const\":0}}}," \
    "\"else\":{\"properties\":{\"score_breakdown\":{" \
    "\"type\":\"object\"}}}}]}}," \
    "\"winner_ref\":{\"type\":\"integer\",\"minimum\":0,\"maximum\":4}," \
    "\"summary\":{\"type\":\"string\",\"minLength\":1,\"maxLength\":511}}}"
static const char s_system_prompt[] __attribute__((unused)) =
    "You are the formal DeepSeek semantic authority for five rehabilitation "
    "tasks. P4 supplies immutable facts, candidate_ref identities, deterministic "
    "hard gates and frozen fixed component values, and independently "
    "recomputes deterministic output during validation. Decide "
    "patient semantics, candidate-scoped advice, medical hard blocks, and every "
    "winner/reason/summary field. Process every "
    "candidate_ref from the input exactly once in fixed order 0,1,2,3,4. "
    "Never add, delete, reorder, "
    "or rewrite a candidate identity. Do not output task_id or task_name. Do "
    "not output advice_interpretation. " LLM_ADVICE_SEMANTICS_TEXT " Treat "
    "advice_basis as a non-scoring overall audit summary; mixed advice may use "
    "direction=mixed. For every candidate return non-empty readable "
    "advice_evidence; a hard medical block specifically requires a verbatim "
    "continuous raw_text substring. patient_ability.level is P4's absolute hard "
    "gate; raw text may explain it but cannot override it, and ability must not "
    "be scored again as soft advice. When raw_text has no explicit special "
    "direction, return neutral. For each available candidate copy difficulty, "
    "compensation, frequency, and perception from decision_preparation, choose "
    "one integer advice under advice_policy, then once set final_raw to "
    "fixed_total plus advice and score to its 0..100 clamped three-decimal "
    "value. Never recalculate or explain the four prepared components. Only if the actual "
    "input is the existing full-v4 adapter-failure fallback and has no "
    "decision_preparation, calculate its frozen policy once. For "
    "input availability=blocked return score=0 and score_breakdown=null; never "
    "unblock it. An input-available candidate may become blocked only under the "
    "direct-prohibition contract; otherwise keep it available. For available "
    "candidates emit the complete output breakdown using the prepared ledger. "
    "The winner_ref must name the highest positive "
    "available output score; ties use decision_preparation.tie_break_candidate_refs. "
    LLM_REASON_CONTRACT_TEXT " " LLM_BLOCKED_REASON_CONTRACT_TEXT " "
    LLM_SUMMARY_CONTRACT_TEXT " Return readable presentation text without "
    "hidden chain-of-thought. "
    "Only output one JSON object, never Markdown. Allowed fields: "
    LLM_ALLOWED_FIELDS_TEXT ". Forbidden fields: " LLM_FORBIDDEN_FIELDS_TEXT
    ". Complete copyable JSON Schema: " LLM_OUTPUT_SCHEMA_TEXT;

const char *rehab_task_decision_formal_output_schema_json(void)
{
    return LLM_OUTPUT_SCHEMA_TEXT;
}

typedef struct {
    char *bytes;
    size_t length;
    char sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX];
    cJSON *root;
    const cJSON *objects;
    char scan_id[REHAB_TASK_DECISION_SCAN_ID_MAX];
    char recommended_target_object_id[
        REHAB_TASK_DECISION_OBJECT_ID_MAX];
} formal_scene_t;

struct rehab_task_decision_formal_package {
    formal_scene_t scene;
    char *input;
    uint32_t request_id;
    uint32_t generation;
    rehab_task_decision_start_source_t entry_source;
    char session_id[96];
};

typedef struct {
    rehab_task_decision_handle_t handle;
    uint32_t generation;
    uint32_t request_id;
} formal_root_waiter_context_t;

#ifdef REHAB_TASK_DECISION_SIZE_PROBE
const unsigned char rehab_size_formal_scene[sizeof(formal_scene_t)] = {0};
#endif

typedef struct {
    cJSON *root;
    cJSON *input_root;
    const cJSON *candidates;
    const cJSON *input_candidates;
    const cJSON *selected_entry;
    const cJSON *target_object;
    size_t winner_ref;
    const char *selected_task_id;
    const char *selected_reason;
    const char *target_object_id;
} formal_decision_t;

static bool coordinator_lock(rehab_task_decision_handle_t handle)
{
    return handle != NULL && handle->coordinator_lock != NULL &&
        xSemaphoreTake(handle->coordinator_lock,
                       pdMS_TO_TICKS(2000)) == pdTRUE;
}

static void coordinator_unlock(rehab_task_decision_handle_t handle)
{
    xSemaphoreGive(handle->coordinator_lock);
}

static uint64_t decision_monotonic_ms(void)
{
    return (uint64_t)(esp_timer_get_time() / 1000);
}

/** Caller owns coordinator_lock. */
static void trace_transition_locked(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    rehab_decision_trace_stage_t stage,
    const char *failure_code)
{
    (void)rehab_decision_trace_transition(
        &handle->coordinator_trace, generation, stage,
        decision_monotonic_ms(), failure_code);
}

static bool finite_number(const cJSON *item, double minimum, double maximum)
{
    return cJSON_IsNumber(item) && isfinite(item->valuedouble) &&
        item->valuedouble >= minimum && item->valuedouble <= maximum;
}

static bool json_string(const cJSON *item, size_t minimum, size_t maximum)
{
    return cJSON_IsString(item) && item->valuestring != NULL &&
        strlen(item->valuestring) >= minimum &&
        strlen(item->valuestring) <= maximum &&
        rehab_task_decision_text_valid(item->valuestring, maximum,
                                       maximum, minimum == 0);
}

static esp_err_t bytes_sha256(const unsigned char *bytes, size_t length,
                              char output[65])
{
    unsigned char digest[32] = {0};
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    int result = mbedtls_sha256_starts(&context, 0);
    if (result == 0) {
        result = mbedtls_sha256_update(&context, bytes, length);
    }
    if (result == 0) {
        result = mbedtls_sha256_finish(&context, digest);
    }
    mbedtls_sha256_free(&context);
    if (result != 0) {
        return ESP_FAIL;
    }
    for (size_t i = 0; i < sizeof(digest); ++i) {
        snprintf(output + i * 2U, 3U, "%02x", digest[i]);
    }
    output[64] = '\0';
    return ESP_OK;
}

static esp_err_t load_file_bounded(const char *path, size_t maximum,
                                   char **out_bytes, size_t *out_length)
{
    if (path == NULL || out_bytes == NULL || out_length == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_bytes = NULL;
    *out_length = 0;
    struct stat info = {0};
    if (stat(path, &info) != 0) {
        return ESP_ERR_NOT_FOUND;
    }
    if (!S_ISREG(info.st_mode) || info.st_size <= 0 ||
        (uint64_t)info.st_size > maximum) {
        return ESP_ERR_INVALID_SIZE;
    }
    size_t length = (size_t)info.st_size;
    char *bytes = malloc(length + 1U);
    if (bytes == NULL) {
        return ESP_ERR_NO_MEM;
    }
    FILE *file = fopen(path, "rb");
    if (file == NULL) {
        free(bytes);
        return ESP_FAIL;
    }
    size_t got = fread(bytes, 1, length, file);
    int extra = fgetc(file);
    bool valid = got == length && extra == EOF && !ferror(file);
    if (fclose(file) != 0) {
        valid = false;
    }
    if (!valid) {
        free(bytes);
        return ESP_FAIL;
    }
    bytes[length] = '\0';
    *out_bytes = bytes;
    *out_length = length;
    return ESP_OK;
}

static bool bbox_valid(const cJSON *bbox)
{
    if (!cJSON_IsArray(bbox) || cJSON_GetArraySize(bbox) != 4) {
        return false;
    }
    double values[4] = {0};
    for (size_t i = 0; i < 4U; ++i) {
        const cJSON *item = cJSON_GetArrayItem(bbox, (int)i);
        if (!finite_number(item, 0.0, i % 2U == 0 ? 640.0 : 360.0)) {
            return false;
        }
        values[i] = item->valuedouble;
    }
    return values[0] < values[2] && values[1] < values[3];
}

static bool scene_object_valid(const cJSON *object)
{
    if (!cJSON_IsObject(object) ||
        !json_string(cJSON_GetObjectItemCaseSensitive(
                         object, "object_id"), 1,
                     REHAB_TASK_DECISION_OBJECT_ID_MAX - 1U) ||
        !json_string(cJSON_GetObjectItemCaseSensitive(
                         object, "class_name"), 1,
                     REHAB_TASK_DECISION_CLASS_NAME_MAX - 1U)) {
        return false;
    }
    const cJSON *confidence = cJSON_GetObjectItemCaseSensitive(
        object, "confidence");
    if (confidence != NULL && !finite_number(confidence, 0.0, 1.0)) {
        return false;
    }
    const cJSON *bbox = cJSON_GetObjectItemCaseSensitive(
        object, "bbox_xyxy");
    if (bbox != NULL && !bbox_valid(bbox)) {
        return false;
    }
    const cJSON *geometry = cJSON_GetObjectItemCaseSensitive(
        object, "geometry");
    return geometry == NULL || cJSON_IsObject(geometry);
}

static bool scene_root_valid(const cJSON *root)
{
    const cJSON *objects = cJSON_GetObjectItemCaseSensitive(root, "objects");
    const cJSON *scan_id = cJSON_GetObjectItemCaseSensitive(root, "scan_id");
    const cJSON *selection = cJSON_GetObjectItemCaseSensitive(
        root, "selection");
    if (!cJSON_IsObject(root) ||
        !cJSON_IsString(cJSON_GetObjectItemCaseSensitive(
            root, "schema_version")) ||
        strcmp(cJSON_GetObjectItemCaseSensitive(
                   root, "schema_version")->valuestring,
               FORMAL_SCENE_SCHEMA) != 0 ||
        !cJSON_IsString(cJSON_GetObjectItemCaseSensitive(root, "status")) ||
        strcmp(cJSON_GetObjectItemCaseSensitive(
                   root, "status")->valuestring, "decision_ready") != 0 ||
        !json_string(scan_id, 1, REHAB_TASK_DECISION_SCAN_ID_MAX - 1U) ||
        !cJSON_IsArray(objects)) {
        return false;
    }
    if (selection != NULL) {
        const cJSON *recommended = cJSON_IsObject(selection)
            ? cJSON_GetObjectItemCaseSensitive(
                selection, "recommended_target_object_id")
            : NULL;
        if (!cJSON_IsObject(selection) ||
            (recommended != NULL && !cJSON_IsNull(recommended) &&
             !json_string(
                 recommended, 1,
                 REHAB_TASK_DECISION_OBJECT_ID_MAX - 1U))) {
            return false;
        }
    }
    const cJSON *object = NULL;
    cJSON_ArrayForEach(object, objects) {
        if (!scene_object_valid(object)) {
            return false;
        }
        const cJSON *other = object->next;
        while (other != NULL) {
            const cJSON *id = cJSON_GetObjectItemCaseSensitive(
                object, "object_id");
            const cJSON *other_id = cJSON_GetObjectItemCaseSensitive(
                other, "object_id");
            if (cJSON_IsString(other_id) &&
                strcmp(id->valuestring, other_id->valuestring) == 0) {
                return false;
            }
            other = other->next;
        }
    }
    return true;
}

static void formal_scene_release(formal_scene_t *scene)
{
    if (scene == NULL) {
        return;
    }
    cJSON_Delete(scene->root);
    free(scene->bytes);
    memset(scene, 0, sizeof(*scene));
}

static void formal_package_release(
    rehab_task_decision_formal_package_t *package)
{
    if (package == NULL) {
        return;
    }
    formal_scene_release(&package->scene);
    cJSON_free(package->input);
    free(package);
}

static void tool_error_set(rehab_task_decision_tool_error_t *error,
                           const char *code,
                           const char *field,
                           const char *detail,
                           bool retry_allowed)
{
    if (error == NULL) {
        return;
    }
    memset(error, 0, sizeof(*error));
    strlcpy(error->code, code != NULL ? code : "formal_decision_failed",
            sizeof(error->code));
    strlcpy(error->field, field != NULL ? field : "none",
            sizeof(error->field));
    strlcpy(error->detail, detail != NULL ? detail : error->code,
            sizeof(error->detail));
    error->retry_allowed = retry_allowed;
}

static esp_err_t formal_scene_load(rehab_task_decision_handle_t handle,
                                   formal_scene_t *scene)
{
    if (handle == NULL || scene == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    memset(scene, 0, sizeof(*scene));
    char path[192] = {0};
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA, FORMAL_SCENE_SUBPATH,
                                    path, sizeof(path));
    if (err == ESP_OK) {
        err = load_file_bounded(path, FORMAL_SCENE_MAX_BYTES,
                                &scene->bytes, &scene->length);
    }
    if (err == ESP_OK) {
        err = bytes_sha256((const unsigned char *)scene->bytes,
                           scene->length, scene->sha256);
    }
    if (err != ESP_OK) {
        formal_scene_release(scene);
        return err;
    }
    scene->root = cJSON_ParseWithOpts(scene->bytes, NULL, true);
    if (!scene_root_valid(scene->root)) {
        formal_scene_release(scene);
        return ESP_ERR_INVALID_RESPONSE;
    }
    scene->objects = cJSON_GetObjectItemCaseSensitive(scene->root, "objects");
    strlcpy(scene->scan_id,
            cJSON_GetObjectItemCaseSensitive(
                scene->root, "scan_id")->valuestring,
            sizeof(scene->scan_id));
    const cJSON *selection = cJSON_GetObjectItemCaseSensitive(
        scene->root, "selection");
    const cJSON *recommended = cJSON_IsObject(selection)
        ? cJSON_GetObjectItemCaseSensitive(
            selection, "recommended_target_object_id")
        : NULL;
    if (cJSON_IsString(recommended) && recommended->valuestring != NULL) {
        strlcpy(scene->recommended_target_object_id,
                recommended->valuestring,
                sizeof(scene->recommended_target_object_id));
    }
    return ESP_OK;
}

static esp_err_t formal_scene_is_current(
    rehab_task_decision_handle_t handle,
    const formal_scene_t *frozen,
    uint32_t coordinator_generation)
{
    if (handle == NULL || frozen == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    bool generation_current =
        handle->coordinator_generation == coordinator_generation &&
        handle->coordinator_status.active;
    coordinator_unlock(handle);
    if (!generation_current) {
        return ESP_ERR_INVALID_STATE;
    }

    char path[192] = {0};
    char *bytes = NULL;
    size_t length = 0;
    char sha256[65] = {0};
    esp_err_t err = claw_paths_join(CLAW_PATH_DATA, FORMAL_SCENE_SUBPATH,
                                    path, sizeof(path));
    if (err == ESP_OK) {
        err = load_file_bounded(path, FORMAL_SCENE_MAX_BYTES,
                                &bytes, &length);
    }
    if (err == ESP_OK) {
        err = bytes_sha256((const unsigned char *)bytes, length, sha256);
    }
    free(bytes);
    return err == ESP_OK && strcmp(sha256, frozen->sha256) == 0
        ? ESP_OK : (err == ESP_OK ? ESP_ERR_INVALID_STATE : err);
}

esp_err_t rehab_task_decision_latest_scene_validate(
    rehab_task_decision_handle_t handle,
    char *out_scan_id,
    size_t scan_id_size,
    char *out_sha256,
    size_t sha256_size)
{
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    formal_scene_t scene = {0};
    esp_err_t err = formal_scene_load(handle, &scene);
    if (err == ESP_OK && out_scan_id != NULL && scan_id_size > 0U) {
        strlcpy(out_scan_id, scene.scan_id, scan_id_size);
    }
    if (err == ESP_OK && out_sha256 != NULL && sha256_size > 0U) {
        strlcpy(out_sha256, scene.sha256, sha256_size);
    }
    formal_scene_release(&scene);
    return err;
}

static cJSON *compact_object(const cJSON *source)
{
    cJSON *object = cJSON_CreateObject();
    if (object == NULL ||
        cJSON_AddStringToObject(
            object, "object_id",
            cJSON_GetObjectItemCaseSensitive(
                source, "object_id")->valuestring) == NULL ||
        cJSON_AddStringToObject(
            object, "class",
            cJSON_GetObjectItemCaseSensitive(
                source, "class_name")->valuestring) == NULL) {
        cJSON_Delete(object);
        return NULL;
    }
    const cJSON *confidence = cJSON_GetObjectItemCaseSensitive(
        source, "confidence");
    if (cJSON_IsNumber(confidence) &&
        cJSON_AddNumberToObject(object, "confidence",
                                confidence->valuedouble) == NULL) {
        cJSON_Delete(object);
        return NULL;
    }
    const cJSON *frame_id = cJSON_GetObjectItemCaseSensitive(
        source, "source_frame_id");
    const cJSON *frame_sequence = cJSON_GetObjectItemCaseSensitive(
        source, "source_frame_sequence");
    if (cJSON_IsString(frame_id) && cJSON_IsNumber(frame_sequence)) {
        if (cJSON_AddStringToObject(object, "source_frame_id",
                                    frame_id->valuestring) == NULL ||
            cJSON_AddNumberToObject(object, "source_frame_sequence",
                                    frame_sequence->valuedouble) == NULL) {
            cJSON_Delete(object);
            return NULL;
        }
    }
    const cJSON *source_bbox = cJSON_GetObjectItemCaseSensitive(
        source, "bbox_xyxy");
    if (source_bbox != NULL) {
        cJSON *bbox = cJSON_Duplicate(source_bbox, true);
        if (bbox == NULL) {
            cJSON_Delete(object);
            return NULL;
        }
        cJSON_AddItemToObject(object, "bbox_xyxy", bbox);
    }
    const cJSON *geometry = cJSON_GetObjectItemCaseSensitive(
        source, "geometry");
    if (geometry != NULL) {
        cJSON *geometry_copy = cJSON_Duplicate(geometry, true);
        if (geometry_copy == NULL) {
            cJSON_Delete(object);
            return NULL;
        }
        const cJSON *geometry_valid = cJSON_GetObjectItemCaseSensitive(
            geometry, "valid");
        if (cJSON_GetObjectItemCaseSensitive(
                geometry_copy, "available") == NULL) {
            cJSON_AddBoolToObject(
                geometry_copy, "available",
                cJSON_IsTrue(geometry_valid));
        }
        cJSON_AddItemToObject(object, "geometry", geometry_copy);
    }
    const cJSON *visibility = cJSON_GetObjectItemCaseSensitive(
        source, "visibility");
    if (visibility != NULL) {
        cJSON *visibility_copy = cJSON_Duplicate(visibility, true);
        if (visibility_copy == NULL) {
            cJSON_Delete(object);
            return NULL;
        }
        cJSON_AddItemToObject(object, "visibility", visibility_copy);
    }
    return object;
}

static bool add_nullable_copy(cJSON *object, const char *name,
                              const cJSON *source)
{
    cJSON *copy = source != NULL
        ? cJSON_Duplicate(source, true) : cJSON_CreateNull();
    if (copy == NULL || !cJSON_AddItemToObject(object, name, copy)) {
        cJSON_Delete(copy);
        return false;
    }
    return true;
}

static esp_err_t build_llm_input(rehab_task_decision_handle_t handle,
                                 const formal_scene_t *scene,
                                 uint32_t generation,
                                 const char *decision_id,
                                 char **out_prompt)
{
    if (handle == NULL || scene == NULL || generation == 0U ||
        decision_id == NULL || out_prompt == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    *out_prompt = NULL;
    cJSON *root = cJSON_CreateObject();
    cJSON *binding = root != NULL
        ? cJSON_AddObjectToObject(root, "decision_binding") : NULL;
    cJSON *scene_json = root != NULL
        ? cJSON_AddObjectToObject(root, "scene") : NULL;
    cJSON *objects = scene_json != NULL
        ? cJSON_AddArrayToObject(scene_json, "objects") : NULL;
    if (root == NULL || binding == NULL || scene_json == NULL ||
        objects == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                                "rehab_task_decision_input.v4") == NULL ||
        cJSON_AddStringToObject(binding, "decision_id",
                                decision_id) == NULL ||
        cJSON_AddNumberToObject(binding, "generation", generation) == NULL ||
        cJSON_AddStringToObject(binding, "source_scan_id",
                                scene->scan_id) == NULL ||
        cJSON_AddStringToObject(binding, "source_scene_sha256",
                                scene->sha256) == NULL ||
        cJSON_AddStringToObject(scene_json, "scan_id",
                                scene->scan_id) == NULL ||
        cJSON_AddStringToObject(scene_json, "sha256",
                                scene->sha256) == NULL) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }
    const cJSON *source_object = NULL;
    cJSON_ArrayForEach(source_object, scene->objects) {
        cJSON *object = compact_object(source_object);
        if (object == NULL) {
            cJSON_Delete(root);
            return ESP_ERR_NO_MEM;
        }
        cJSON_AddItemToArray(objects, object);
    }

    cJSON *semantic_groups = cJSON_AddObjectToObject(
        root, "semantic_groups");
    cJSON *cup_members = semantic_groups != NULL
        ? cJSON_AddArrayToObject(semantic_groups, "cup") : NULL;
    cJSON *ball_members = semantic_groups != NULL
        ? cJSON_AddArrayToObject(semantic_groups, "ball") : NULL;
    if (semantic_groups == NULL || cup_members == NULL ||
        ball_members == NULL ||
        cJSON_AddItemToArray(cup_members, cJSON_CreateString("cup")) == 0 ||
        cJSON_AddItemToArray(cup_members, cJSON_CreateString("bottle")) == 0 ||
        cJSON_AddItemToArray(ball_members,
                            cJSON_CreateString("sports ball")) == 0 ||
        cJSON_AddItemToArray(ball_members, cJSON_CreateString("ball")) == 0 ||
        cJSON_AddItemToArray(ball_members,
                            cJSON_CreateString("volleyball")) == 0) {
        cJSON_Delete(root);
        return ESP_ERR_NO_MEM;
    }

    int ability_level = 1;
    bool ability_parsed = false;
    esp_err_t ability_err = rehab_task_decision_context_ability_level(
        &ability_level, &ability_parsed);
    if (ability_err != ESP_OK) {
        cJSON_Delete(root);
        return ability_err;
    }
    cJSON *candidates = cJSON_AddArrayToObject(root, "candidates");
    for (size_t i = 0;
         candidates != NULL &&
         i < rehab_task_decision_rules_task_count(); ++i) {
        const char *task_id = rehab_task_decision_rules_task_id_at(i);
        char task_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX] = {0};
        char version[REHAB_TASK_DECISION_RULES_VERSION_MAX] = {0};
        const rehab_task_policy_definition_t *policy =
            rehab_task_policy_find(task_id);
        rehab_task_target_resolution_t resolution = {0};
        cJSON *candidate = cJSON_CreateObject();
        if (task_id == NULL || policy == NULL || candidate == NULL ||
            rehab_task_decision_rules_task_metadata(
                handle, task_id, task_name, sizeof(task_name),
                version, sizeof(version)) != ESP_OK ||
            cJSON_AddNumberToObject(candidate, "candidate_ref",
                                    (double)i) == NULL ||
            cJSON_AddStringToObject(candidate, "task_id",
                                    task_id) == NULL ||
            cJSON_AddStringToObject(candidate, "task_name",
                                    task_name) == NULL ||
            cJSON_AddNumberToObject(candidate, "level",
                                    policy->level) == NULL ||
            cJSON_AddStringToObject(candidate, "semantic_group",
                                    policy->semantic_group) == NULL ||
            cJSON_AddStringToObject(candidate, "action_family",
                                    policy->action_family) == NULL ||
            cJSON_AddStringToObject(candidate, "required_plane",
                                    policy->plane) == NULL ||
            rehab_task_target_resolve(
                scene->objects, task_id, NULL, "prompt",
                &resolution) != ESP_OK) {
            cJSON_Delete(candidate);
            cJSON_Delete(root);
            return ESP_ERR_NO_MEM;
        }
        const bool allowed_by_ability = policy->level <=
            (unsigned)ability_level;
        const char *blocked_reason = "none";
        if (!allowed_by_ability) {
            blocked_reason = "ability_exceeded";
        } else if (resolution.semantic_group_count == 0U) {
            blocked_reason = "semantic_group_absent";
        } else if (resolution.object == NULL) {
            blocked_reason = "no_matching_plane_target";
        }
        const bool blocked = strcmp(blocked_reason, "none") != 0;
        cJSON *gates = cJSON_AddObjectToObject(candidate, "hard_gates");
        cJSON *facts = cJSON_AddObjectToObject(candidate, "target_facts");
        if (gates == NULL || facts == NULL ||
            cJSON_AddStringToObject(
                candidate, "availability",
                blocked ? "blocked" : "available") == NULL ||
            cJSON_AddBoolToObject(gates, "allowed_by_ability",
                                  allowed_by_ability) == NULL ||
            cJSON_AddBoolToObject(gates, "ability_allowed",
                                  allowed_by_ability) == NULL ||
            cJSON_AddNumberToObject(gates, "group_object_count",
                                    resolution.semantic_group_count) == NULL ||
            cJSON_AddBoolToObject(gates, "feasible_target",
                                  resolution.object != NULL) == NULL ||
            cJSON_AddBoolToObject(gates, "geometry_height_valid",
                                  resolution.object != NULL) == NULL ||
            cJSON_AddBoolToObject(gates, "blocked", blocked) == NULL ||
            cJSON_AddStringToObject(gates, "blocked_reason",
                                    blocked_reason) == NULL ||
            cJSON_AddBoolToObject(gates, "score_must_be_zero",
                                  blocked) == NULL ||
            cJSON_AddStringToObject(facts, "display_group_name",
                                    rehab_task_policy_group_zh(
                                        policy->semantic_group)) == NULL) {
            cJSON_Delete(candidate);
            cJSON_Delete(root);
            return ESP_ERR_NO_MEM;
        }
        if (resolution.object != NULL) {
            const cJSON *raw_class = cJSON_GetObjectItemCaseSensitive(
                resolution.object, "class_name");
            const cJSON *confidence = cJSON_GetObjectItemCaseSensitive(
                resolution.object, "confidence");
            const cJSON *geometry = cJSON_GetObjectItemCaseSensitive(
                resolution.object, "geometry");
            const cJSON *visibility = cJSON_GetObjectItemCaseSensitive(
                resolution.object, "visibility");
            const cJSON *full_extent = cJSON_IsObject(visibility)
                ? cJSON_GetObjectItemCaseSensitive(
                    visibility, "full_extent_visible") : NULL;
            const cJSON *touches_border = cJSON_IsObject(visibility)
                ? cJSON_GetObjectItemCaseSensitive(
                    visibility, "touches_border") : NULL;
            const bool touches_border_known = cJSON_IsBool(touches_border);
            const bool full_extent_known = cJSON_IsBool(full_extent);
            const cJSON *geometry_valid = cJSON_IsObject(geometry)
                ? cJSON_GetObjectItemCaseSensitive(geometry, "valid") : NULL;
            const cJSON *geometry_confidence = cJSON_IsObject(geometry)
                ? cJSON_GetObjectItemCaseSensitive(
                    geometry, "geometry_confidence") : NULL;
            const bool geometry_valid_known = cJSON_IsBool(geometry_valid);
            const bool geometry_confidence_available =
                cJSON_IsNumber(geometry_confidence) &&
                isfinite(geometry_confidence->valuedouble);
            const bool geometry_confidence_malformed =
                geometry_confidence != NULL &&
                !cJSON_IsNull(geometry_confidence) &&
                !geometry_confidence_available;
            const bool not_border = touches_border_known &&
                cJSON_IsFalse(touches_border);
            if (cJSON_AddStringToObject(
                    facts, "target_object_id",
                    resolution.object_id) == NULL ||
                cJSON_AddStringToObject(
                    facts, "raw_class",
                    cJSON_IsString(raw_class)
                        ? raw_class->valuestring : "unknown") == NULL ||
                cJSON_AddNumberToObject(
                    facts, "support_height_mm",
                    resolution.support_height_mm) == NULL ||
                cJSON_AddNumberToObject(
                    facts, "height_confidence",
                    resolution.height_confidence) == NULL ||
                cJSON_AddStringToObject(
                    facts, "plane", policy->plane) == NULL ||
                cJSON_AddStringToObject(
                    facts, "height_status", "available") == NULL ||
                cJSON_AddBoolToObject(
                    facts, "geometry_valid",
                    cJSON_IsTrue(geometry_valid)) == NULL ||
                cJSON_AddBoolToObject(
                    facts, "geometry_valid_known",
                    geometry_valid_known) == NULL ||
                (geometry_confidence_available
                    ? cJSON_AddNumberToObject(
                          facts, "geometry_confidence",
                          geometry_confidence->valuedouble) == NULL
                    : cJSON_AddNullToObject(
                          facts, "geometry_confidence") == NULL) ||
                cJSON_AddStringToObject(
                    facts, "geometry_confidence_status",
                    geometry_confidence_available ? "available" :
                        (geometry_confidence_malformed
                            ? "malformed_fallback" :
                              "unavailable_fallback")) == NULL ||
                cJSON_AddStringToObject(
                    facts, "perception_mode",
                    geometry_confidence_available
                        ? "geometry" : "fallback") == NULL ||
                cJSON_AddNumberToObject(
                    facts, "detection_confidence",
                    cJSON_IsNumber(confidence)
                        ? confidence->valuedouble : 0.0) == NULL ||
                cJSON_AddStringToObject(
                    facts, "detection_confidence_status",
                    cJSON_IsNumber(confidence)
                        ? "available" : "conservative_zero") == NULL ||
                cJSON_AddBoolToObject(
                    facts, "full_extent_visible",
                    cJSON_IsTrue(full_extent)) == NULL ||
                cJSON_AddBoolToObject(
                    facts, "full_extent_visible_known",
                    full_extent_known) == NULL ||
                cJSON_AddBoolToObject(
                    facts, "touches_border_known",
                    touches_border_known) == NULL ||
                cJSON_AddBoolToObject(
                    facts, "not_border", not_border) == NULL) {
                cJSON_Delete(candidate);
                cJSON_Delete(root);
                return ESP_ERR_NO_MEM;
            }
            cJSON *border_fact = touches_border_known
                ? cJSON_CreateBool(cJSON_IsTrue(touches_border))
                : cJSON_CreateNull();
            if (border_fact == NULL ||
                !cJSON_AddItemToObject(
                    facts, "touches_border", border_fact)) {
                cJSON_Delete(border_fact);
                cJSON_Delete(candidate);
                cJSON_Delete(root);
                return ESP_ERR_NO_MEM;
            }
            const double detection = cJSON_IsNumber(confidence)
                ? fmax(0.0, fmin(1.0, confidence->valuedouble)) : 0.0;
            const double height = fmax(
                0.0, fmin(1.0, resolution.height_confidence));
            const double geometry_value = geometry_confidence_available
                ? fmax(0.0, fmin(1.0,
                    geometry_confidence->valuedouble)) : 0.0;
            const double quality = geometry_confidence_available
                ? 0.50 * geometry_value + 0.30 * detection + 0.20 * height
                : 0.60 * detection + 0.40 * height;
            if (cJSON_AddNumberToObject(facts, "perception_quality",
                                        quality) == NULL ||
                !add_nullable_copy(facts, "source_frame_id",
                    cJSON_GetObjectItemCaseSensitive(
                        resolution.object, "source_frame_id")) ||
                !add_nullable_copy(facts, "source_frame_sequence",
                    cJSON_GetObjectItemCaseSensitive(
                        resolution.object, "source_frame_sequence")) ||
                !add_nullable_copy(facts, "bbox_xyxy",
                    cJSON_GetObjectItemCaseSensitive(
                        resolution.object, "bbox_xyxy")) ||
                !add_nullable_copy(facts, "object_height_mm",
                    cJSON_GetObjectItemCaseSensitive(
                        geometry, "object_height_mm")) ||
                !add_nullable_copy(facts, "top_height_mm",
                    cJSON_GetObjectItemCaseSensitive(
                        geometry, "top_height_above_floor_mm")) ||
                !add_nullable_copy(facts, "distance_mm",
                    cJSON_GetObjectItemCaseSensitive(geometry,
                                                     "distance_mm"))) {
                cJSON_Delete(candidate);
                cJSON_Delete(root);
                return ESP_ERR_NO_MEM;
            }
        } else {
            cJSON_AddNullToObject(facts, "target_object_id");
            cJSON_AddNullToObject(facts, "raw_class");
            cJSON_AddNullToObject(facts, "support_height_mm");
            cJSON_AddNumberToObject(facts, "height_confidence", 0.0);
            cJSON_AddStringToObject(facts, "plane", policy->plane);
            cJSON_AddStringToObject(facts, "height_status", "unavailable");
            cJSON_AddBoolToObject(facts, "geometry_valid", false);
            cJSON_AddBoolToObject(facts, "geometry_valid_known", false);
            cJSON_AddNullToObject(facts, "geometry_confidence");
            cJSON_AddStringToObject(facts, "geometry_confidence_status",
                                    "unavailable_fallback");
            cJSON_AddStringToObject(facts, "perception_mode", "fallback");
            cJSON_AddNumberToObject(facts, "detection_confidence", 0.0);
            cJSON_AddStringToObject(facts, "detection_confidence_status",
                                    "conservative_zero");
            cJSON_AddBoolToObject(facts, "full_extent_visible", false);
            cJSON_AddBoolToObject(facts, "full_extent_visible_known", false);
            cJSON_AddBoolToObject(facts, "touches_border_known", false);
            cJSON_AddNullToObject(facts, "touches_border");
            cJSON_AddBoolToObject(facts, "not_border", false);
            cJSON_AddNumberToObject(facts, "perception_quality", 0.0);
            cJSON_AddNullToObject(facts, "source_frame_id");
            cJSON_AddNullToObject(facts, "source_frame_sequence");
            cJSON_AddNullToObject(facts, "bbox_xyxy");
            cJSON_AddNullToObject(facts, "object_height_mm");
            cJSON_AddNullToObject(facts, "top_height_mm");
            cJSON_AddNullToObject(facts, "distance_mm");
        }
        cJSON_AddItemToArray(candidates, candidate);
    }
    esp_err_t err = candidates == NULL
        ? ESP_ERR_NO_MEM
        : rehab_task_decision_context_add_inputs(handle, root);
    if (err == ESP_OK) {
        err = rehab_task_scoring_add_policy(root);
    }
    if (err != ESP_OK) {
        cJSON_Delete(root);
        return err;
    }
    char *prompt = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (prompt == NULL) {
        return ESP_ERR_NO_MEM;
    }
    if (strlen(prompt) >
        REHAB_TASK_DECISION_LLM_INPUT_MAX - 512U) {
        cJSON_free(prompt);
        return ESP_ERR_INVALID_SIZE;
    }
    ESP_LOGI(TAG, "LLM_CONTRACT_VERSION version=%s",
             REHAB_TASK_DECISION_OUTPUT_SCHEMA_VERSION);
    ESP_LOGI(TAG, "LLM_CANDIDATE_REFS refs=0,1,2,3,4");
    *out_prompt = prompt;
    return ESP_OK;
}

static esp_err_t llm_call(rehab_task_decision_handle_t handle,
                          uint32_t generation,
                          uint32_t attempt,
                          const char *decision_id,
                          const char *prompt,
                          char **out_text)
{
    if (handle == NULL || !handle->coordinator_llm_ready ||
        handle->coordinator_llm_core == NULL || prompt == NULL ||
        out_text == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    *out_text = NULL;
    claw_core_request_t request = {
        .request_id = UINT32_C(0x8C100000) |
            ((generation & UINT32_C(0x0007FFFF)) << 1U) |
            (attempt & 1U),
        .flags = 0,
        .session_id = decision_id,
        .user_text = prompt,
        .source_channel = "rehab_task_decision",
        .source_chat_id = decision_id,
        .source_sender_id = "coordinator",
        .source_message_id = decision_id,
        .source_cap = "rehab_task_decision_coordinator",
        .target_channel = "rehab_task_decision",
        .target_chat_id = decision_id,
    };
    esp_err_t err = claw_core_submit(handle->coordinator_llm_core,
                                     &request, 1000U);
    claw_core_response_t response = {0};
    if (err == ESP_OK) {
        err = claw_core_receive_for(
            handle->coordinator_llm_core, request.request_id, &response,
            handle->coordinator_llm_timeout_ms);
    }
    if (err == ESP_OK &&
        (response.status != CLAW_CORE_RESPONSE_STATUS_OK ||
         response.text == NULL || response.text[0] == '\0')) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    if (err == ESP_OK &&
        strlen(response.text) > REHAB_TASK_DECISION_LLM_OUTPUT_MAX) {
        err = ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK) {
        *out_text = strdup(response.text);
        if (*out_text == NULL) {
            err = ESP_ERR_NO_MEM;
        }
    }
    claw_core_response_free(&response);
    return err;
}

static char *build_repair_prompt(const char *input,
                                 const char *previous_output,
                                 const char *validation_error)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *errors = root != NULL
        ? cJSON_AddArrayToObject(root, "validation_errors") : NULL;
    cJSON *refs = root != NULL
        ? cJSON_AddArrayToObject(root, "original_candidate_refs") : NULL;
    cJSON *original_input = input != NULL
        ? cJSON_ParseWithOpts(input, NULL, true) : NULL;
    bool ok = root != NULL && errors != NULL && refs != NULL &&
        original_input != NULL &&
        cJSON_AddStringToObject(
            root, "schema_version",
            "rehab_task_decision_repair.v4") != NULL &&
        cJSON_AddStringToObject(
            root, "instruction",
            "Return one complete rehab_task_decision_output.v4 JSON object. Preserve candidate_ref order 0..4 exactly once. Never change input blocked to available; input available may change to blocked only for an explicit strong medical prohibition, with advice_evidence copied as a continuous raw_text substring. Soft negative advice must not hard-block. Return non-scoring advice_basis audit metadata and per-candidate advice_evidence/advice under decision_preparation.advice_policy. For every available candidate copy the four frozen components from decision_preparation unchanged, set final_raw once to fixed_total plus advice, and set score to its 0..100 clamped three-decimal value; do not recalculate or explain prepared components. Only when original_input is the existing full-v4 adapter-failure fallback without decision_preparation, calculate its frozen policy once. Summary and reason are readable presentation text with no fixed wording. Do not output task_id, task_name, winner task identity, advice_interpretation, hidden reasoning, or unknown fields. P4 supplies validation errors and independently recomputes deterministic values but never chooses candidate advice or winner. Output JSON only.") != NULL &&
        cJSON_AddStringToObject(
            root, "target_schema", LLM_OUTPUT_SCHEMA_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "allowed_fields", LLM_ALLOWED_FIELDS_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "forbidden_fields", LLM_FORBIDDEN_FIELDS_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "advice_semantics", LLM_ADVICE_SEMANTICS_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "reason_contract", LLM_REASON_CONTRACT_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "blocked_reason_contract",
            LLM_BLOCKED_REASON_CONTRACT_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "summary_contract", LLM_SUMMARY_CONTRACT_TEXT) != NULL &&
        cJSON_AddStringToObject(
            root, "tie_rule",
            "winner_ref is the highest positive available score; ties use decision_preparation.tie_break_candidate_refs") != NULL &&
        cJSON_AddItemToArray(
            errors, cJSON_CreateString(validation_error)) &&
        cJSON_AddStringToObject(
            root, "previous_output",
            previous_output != NULL ? previous_output : "") != NULL;
    for (size_t i = 0; ok && i < rehab_task_policy_task_count(); ++i) {
        ok = cJSON_AddItemToArray(refs, cJSON_CreateNumber((double)i));
    }
    if (ok) {
        cJSON_AddItemToObject(root, "original_input", original_input);
        original_input = NULL;
    }
    char *rendered = ok ? cJSON_PrintUnformatted(root) : NULL;
    cJSON_Delete(original_input);
    cJSON_Delete(root);
    if (rendered != NULL && strlen(rendered) >=
        REHAB_TASK_DECISION_LLM_INPUT_MAX) {
        cJSON_free(rendered);
        rendered = NULL;
    }
    return rendered;
}

static cJSON *parse_json_response(const char *text)
{
    if (text == NULL) {
        return NULL;
    }
    return cJSON_ParseWithOpts(text, NULL, true);
}

static const char *validation_field(const char *reason)
{
    if (reason == NULL) return "none";
    if (strstr(reason, "scene_scan") != NULL) return "scene_scan_id";
    if (strstr(reason, "scene_sha") != NULL) return "scene_sha256";
    if (strstr(reason, "candidate") != NULL) return "candidates";
    if (strstr(reason, "winner_ref") != NULL) return "winner_ref";
    if (strstr(reason, "decision") != NULL) return "decision_id";
    if (strstr(reason, "schema") != NULL) return "schema_version";
    return "output";
}

static const cJSON *candidate_by_ref(const cJSON *candidates,
                                     size_t candidate_ref)
{
    const cJSON *candidate = NULL;
    cJSON_ArrayForEach(candidate, candidates) {
        const cJSON *ref = cJSON_GetObjectItemCaseSensitive(
            candidate, "candidate_ref");
        if (cJSON_IsNumber(ref) && isfinite(ref->valuedouble) &&
            ref->valuedouble == (double)candidate_ref) {
            return candidate;
        }
    }
    return NULL;
}

static const char *validate_output(rehab_task_decision_handle_t handle,
                                   const formal_scene_t *scene,
                                   const char *input_text,
                                   const char *text,
                                   const char *decision_id,
                                   formal_decision_t *decision)
{
    memset(decision, 0, sizeof(*decision));
    cJSON *input = cJSON_ParseWithOpts(input_text, NULL, true);
    decision->root = parse_json_response(text);
    if (!cJSON_IsObject(input) || !cJSON_IsObject(decision->root)) {
        cJSON_Delete(input);
        return "json_invalid";
    }
    char validation_error[2048] = {0};
    if (!rehab_task_scoring_validate(
            input, decision->root, validation_error,
            sizeof(validation_error))) {
        cJSON_Delete(input);
        static char error_code[2080];
        snprintf(error_code, sizeof(error_code), "scoring_contract:%s",
                 validation_error[0] != '\0'
                    ? validation_error : "invalid");
        return error_code;
    }
    const cJSON *candidates = cJSON_GetObjectItemCaseSensitive(
        decision->root, "candidates");
    const cJSON *input_candidates = cJSON_GetObjectItemCaseSensitive(
        input, "candidates");
    const cJSON *winner = cJSON_GetObjectItemCaseSensitive(
        decision->root, "winner_ref");
    const size_t winner_ref = (size_t)winner->valuedouble;
    const rehab_task_policy_definition_t *selected_task =
        rehab_task_policy_task_at(winner_ref);
    const cJSON *selected_entry = candidate_by_ref(candidates, winner_ref);
    const cJSON *selected_input = candidate_by_ref(
        input_candidates, winner_ref);
    const cJSON *selected_reason = cJSON_GetObjectItemCaseSensitive(
        selected_entry, "reason");
    const cJSON *facts = cJSON_GetObjectItemCaseSensitive(
        selected_input, "target_facts");
    const cJSON *target_id = cJSON_GetObjectItemCaseSensitive(
        facts, "target_object_id");
    if (selected_task == NULL || !cJSON_IsObject(selected_entry) ||
        !cJSON_IsObject(selected_input) ||
        !cJSON_IsString(selected_reason) ||
        (!cJSON_IsString(target_id) && !cJSON_IsNull(target_id))) {
        cJSON_Delete(input);
        return "winner_ref_mapping_invalid";
    }
    rehab_task_target_resolution_t resolution = {0};
    esp_err_t resolve_err = rehab_task_target_resolve(
        scene->objects, selected_task->task_id,
        cJSON_IsString(target_id) ? target_id->valuestring : NULL,
        decision_id, &resolution);
    if (resolve_err != ESP_OK) {
        cJSON_Delete(input);
        return "target_resolve_failed";
    }
    if (rehab_task_target_task_has_mapping(selected_task->task_id) &&
        resolution.compatible_count > 0U && resolution.object == NULL) {
        cJSON_Delete(input);
        return "target_required";
    }
    decision->candidates = candidates;
    decision->selected_entry = selected_entry;
    decision->target_object = resolution.object;
    decision->winner_ref = winner_ref;
    decision->selected_task_id = selected_task->task_id;
    decision->selected_reason = selected_reason->valuestring;
    decision->target_object_id = resolution.object_id;
    decision->input_root = input;
    decision->input_candidates = input_candidates;
    ESP_LOGI(TAG, "LLM_OUTPUT_VALIDATE_OK decision_id=%s winner_ref=%u",
             decision_id, (unsigned)winner_ref);
    ESP_LOGI(TAG,
             "LLM_WINNER_REF_RESOLVED candidate_ref=%u task_id=%s",
             (unsigned)winner_ref, selected_task->task_id);
    (void)handle;
    return NULL;
}

static void formal_decision_release(formal_decision_t *decision)
{
    if (decision != NULL) {
        cJSON_Delete(decision->root);
        cJSON_Delete(decision->input_root);
        memset(decision, 0, sizeof(*decision));
    }
}

static void created_at_now(char output[40])
{
    time_t now = time(NULL);
    struct tm utc = {0};
    if (now > 0 && gmtime_r(&now, &utc) != NULL &&
        strftime(output, 40, "%Y-%m-%dT%H:%M:%SZ", &utc) > 0) {
        return;
    }
    strlcpy(output, "1970-01-01T00:00:00Z", 40);
}

static bool selected_target_matches_source(const cJSON *root,
                                           const cJSON *source)
{
    const cJSON *target_id = cJSON_GetObjectItemCaseSensitive(
        root, "target_object_id");
    const cJSON *target = cJSON_GetObjectItemCaseSensitive(
        root, "target_object");
    const cJSON *source_id = cJSON_GetObjectItemCaseSensitive(
        source, "object_id");
    const cJSON *target_object_id = cJSON_IsObject(target)
        ? cJSON_GetObjectItemCaseSensitive(target, "object_id") : NULL;
    const cJSON *source_class = cJSON_GetObjectItemCaseSensitive(
        source, "class_name");
    const cJSON *target_class = cJSON_IsObject(target)
        ? cJSON_GetObjectItemCaseSensitive(target, "class") : NULL;
    if (!cJSON_IsString(target_id) || !cJSON_IsString(source_id) ||
        !cJSON_IsString(target_object_id) ||
        strcmp(target_id->valuestring, source_id->valuestring) != 0 ||
        strcmp(target_object_id->valuestring, source_id->valuestring) != 0 ||
        !cJSON_IsString(source_class) || !cJSON_IsString(target_class) ||
        strcmp(target_class->valuestring, source_class->valuestring) != 0) {
        return false;
    }
    const char *fields[] = {
        "source_frame_id", "source_frame_sequence", "bbox_xyxy", "geometry",
    };
    for (size_t i = 0; i < sizeof(fields) / sizeof(fields[0]); ++i) {
        const cJSON *source_value = cJSON_GetObjectItemCaseSensitive(
            source, fields[i]);
        const cJSON *target_value = cJSON_GetObjectItemCaseSensitive(
            target, fields[i]);
        if ((source_value == NULL) != (target_value == NULL) ||
            (source_value != NULL &&
             !cJSON_Compare(source_value, target_value, true))) {
            return false;
        }
    }
    return true;
}

static bool selected_target_binding_valid(const formal_scene_t *scene,
                                          const formal_decision_t *decision)
{
    if (scene == NULL || decision == NULL ||
        decision->selected_task_id == NULL) {
        return false;
    }
    size_t compatible_count = 0;
    bool target_is_current_compatible_object = false;
    const cJSON *object = NULL;
    cJSON_ArrayForEach(object, scene->objects) {
        if (!rehab_task_target_object_is_compatible(
                decision->selected_task_id, object)) {
            continue;
        }
        ++compatible_count;
        const cJSON *object_id = cJSON_GetObjectItemCaseSensitive(
            object, "object_id");
        if (object == decision->target_object &&
            decision->target_object_id != NULL &&
            cJSON_IsString(object_id) && object_id->valuestring != NULL &&
            strcmp(object_id->valuestring,
                   decision->target_object_id) == 0) {
            target_is_current_compatible_object = true;
        }
    }
    if (!rehab_task_target_task_has_mapping(
            decision->selected_task_id) || compatible_count == 0U) {
        return decision->target_object == NULL &&
            decision->target_object_id == NULL;
    }
    return target_is_current_compatible_object;
}

static cJSON *build_selected_candidate_scores(const cJSON *candidates)
{
    if (!cJSON_IsArray(candidates) ||
        cJSON_GetArraySize(candidates) !=
            (int)rehab_task_decision_rules_task_count()) {
        return NULL;
    }
    cJSON *output = cJSON_CreateArray();
    if (output == NULL) {
        return NULL;
    }
    for (size_t i = 0U; i < rehab_task_decision_rules_task_count(); ++i) {
        const cJSON *source = candidate_by_ref(candidates, i);
        const rehab_task_policy_definition_t *task =
            rehab_task_policy_task_at(i);
        const cJSON *score = cJSON_GetObjectItemCaseSensitive(
            source, "score");
        const cJSON *reason = cJSON_GetObjectItemCaseSensitive(
            source, "reason");
        cJSON *entry = cJSON_CreateObject();
        if (!cJSON_IsObject(source) || task == NULL ||
            !finite_number(score, 0.0, 100.0) ||
            !json_string(reason, 1U,
                         REHAB_TASK_DECISION_REASON_MAX - 1U) ||
            entry == NULL ||
            cJSON_AddStringToObject(entry, "task_name",
                                    task->task_name) == NULL ||
            cJSON_AddNumberToObject(entry, "score",
                                    score->valuedouble) == NULL ||
            cJSON_AddStringToObject(entry, "reason",
                                    reason->valuestring) == NULL ||
            !cJSON_AddItemToArray(output, entry)) {
            cJSON_Delete(entry);
            cJSON_Delete(output);
            return NULL;
        }
    }
    return output;
}

static cJSON *build_selected_v2(rehab_task_decision_handle_t handle,
                                const formal_scene_t *scene,
                                const formal_decision_t *decision,
                                uint32_t generation,
                                const char *decision_id)
{
    if (!selected_target_binding_valid(scene, decision)) {
        return NULL;
    }
    char task_name[REHAB_TASK_DECISION_SELECTED_TASK_NAME_MAX] = {0};
    char rules_version[REHAB_TASK_DECISION_RULES_VERSION_MAX] = {0};
    if (rehab_task_decision_rules_task_metadata(
            handle, decision->selected_task_id, task_name,
            sizeof(task_name), rules_version, sizeof(rules_version)) !=
        ESP_OK) {
        return NULL;
    }
    const cJSON *selected_score = cJSON_GetObjectItemCaseSensitive(
        decision->selected_entry, "score");
    char created_at[40] = {0};
    created_at_now(created_at);
    cJSON *root = cJSON_CreateObject();
    if (root == NULL ||
        cJSON_AddStringToObject(root, "schema_version",
                               FORMAL_SELECTED_SCHEMA) == NULL ||
        cJSON_AddStringToObject(root, "task_id",
                               decision->selected_task_id) == NULL ||
        cJSON_AddStringToObject(root, "task_name", task_name) == NULL ||
        cJSON_AddStringToObject(root, "reason",
                               decision->selected_reason) == NULL ||
        cJSON_AddNumberToObject(root, "score",
                               selected_score->valuedouble) == NULL ||
        cJSON_AddStringToObject(root, "created_at", created_at) == NULL ||
        cJSON_AddStringToObject(root, "decision_id", decision_id) == NULL ||
        cJSON_AddNumberToObject(root, "decision_generation",
                               generation) == NULL ||
        cJSON_AddStringToObject(root, "source_scan_id",
                               scene->scan_id) == NULL ||
        cJSON_AddStringToObject(root, "source_scene_sha256",
                               scene->sha256) == NULL ||
        cJSON_AddNumberToObject(root, "suggested_distance_cm", 0) == NULL) {
        cJSON_Delete(root);
        return NULL;
    }
    cJSON *candidate_scores = build_selected_candidate_scores(
        decision->candidates);
    if (candidate_scores == NULL) {
        cJSON_Delete(root);
        return NULL;
    }
    if (!cJSON_AddItemToObject(root, "candidate_scores", candidate_scores)) {
        cJSON_Delete(candidate_scores);
        cJSON_Delete(root);
        return NULL;
    }

    if (decision->target_object == NULL) {
        if (cJSON_AddNullToObject(root, "target_object_id") == NULL ||
            cJSON_AddNullToObject(root, "target_object") == NULL) {
            cJSON_Delete(root);
            return NULL;
        }
        return root;
    }
    const cJSON *source = decision->target_object;
    const cJSON *source_class = cJSON_GetObjectItemCaseSensitive(
        source, "class_name");
    cJSON *target = cJSON_Duplicate(source, true);
    if (!cJSON_IsString(source_class) || source_class->valuestring == NULL ||
        target == NULL ||
        cJSON_AddStringToObject(root, "target_object_id",
                               decision->target_object_id) == NULL ||
        cJSON_AddStringToObject(target, "class",
                               source_class->valuestring) == NULL ||
        !cJSON_AddItemToObject(root, "target_object", target)) {
        cJSON_Delete(target);
        cJSON_Delete(root);
        return NULL;
    }
    if (!selected_target_matches_source(root, source)) {
        cJSON_Delete(root);
        return NULL;
    }
    return root;
}

static void coordinator_set_state(rehab_task_decision_handle_t handle,
                                  uint32_t generation,
                                  rehab_task_decision_coordinator_state_t state)
{
    if (coordinator_lock(handle)) {
        if (handle->coordinator_generation == generation) {
            handle->coordinator_status.state = state;
            handle->coordinator_status.active =
                state == REHAB_TASK_DECISION_COORDINATOR_PREPARING ||
                state == REHAB_TASK_DECISION_COORDINATOR_DECIDING ||
                state == REHAB_TASK_DECISION_COORDINATOR_VALIDATING ||
                state == REHAB_TASK_DECISION_COORDINATOR_COMMITTING;
            if (state == REHAB_TASK_DECISION_COORDINATOR_VALIDATING) {
                trace_transition_locked(
                    handle, generation, REHAB_DECISION_TRACE_VALIDATING,
                    NULL);
            } else if (state ==
                       REHAB_TASK_DECISION_COORDINATOR_COMMITTING) {
                trace_transition_locked(
                    handle, generation, REHAB_DECISION_TRACE_COMMITTING,
                    NULL);
            }
        }
        coordinator_unlock(handle);
    }
}

static void coordinator_fail(rehab_task_decision_handle_t handle,
                             uint32_t generation,
                             const char *code)
{
    if (!coordinator_lock(handle)) {
        return;
    }
    if (handle->coordinator_generation == generation) {
        handle->coordinator_status.state =
            REHAB_TASK_DECISION_COORDINATOR_FAILED;
        handle->coordinator_status.active = false;
        handle->coordinator_status.task_id[0] = '\0';
        handle->coordinator_status.task_name[0] = '\0';
        handle->coordinator_status.reason[0] = '\0';
        handle->coordinator_status.target_available = false;
        strlcpy(handle->coordinator_status.last_error,
                code != NULL ? code : "decision_failed",
                sizeof(handle->coordinator_status.last_error));
        trace_transition_locked(
            handle, generation, REHAB_DECISION_TRACE_FAILED,
            code != NULL ? code : "decision_failed");
    }
    coordinator_unlock(handle);
    ESP_LOGE(TAG, "FORMAL_DECISION_FAILED generation=%" PRIu32
             " code=%s", generation, code != NULL ? code : "decision_failed");
}

static const char *commit_validated_decision(
    rehab_task_decision_handle_t handle,
    const formal_scene_t *scene,
    formal_decision_t *decision,
    uint32_t generation,
    const char *decision_id,
    rehab_task_decision_start_source_t entry_source)
{
    cJSON *selected = NULL;
    const char *failure = NULL;
    esp_err_t err = formal_scene_is_current(handle, scene, generation);
    if (err != ESP_OK) {
        return "stale_scene_or_generation";
    }
    selected = build_selected_v2(handle, scene, decision,
                                 generation, decision_id);
    if (selected == NULL) {
        return "selected_v2_build_failed";
    }
    coordinator_set_state(handle, generation,
                          REHAB_TASK_DECISION_COORDINATOR_COMMITTING);
    if (!selected_target_binding_valid(scene, decision)) {
        failure = "selected_target_binding_invalid";
        goto done;
    }
    err = rehab_task_decision_selected_commit_v2(handle, selected);
    if (err != ESP_OK) {
        failure = "selected_v2_commit_failed";
        goto done;
    }

    rehab_task_decision_selected_snapshot_t snapshot = {0};
    err = rehab_task_decision_selected_get(handle, &snapshot);
    if (err != ESP_OK || !snapshot.available ||
        strcmp(snapshot.decision_id, decision_id) != 0) {
        failure = "selected_v2_final_readback_failed";
        goto done;
    }
    ESP_LOGI(TAG,
             "SELECTED_TASK_TRANSACTION_COMMIT decision_id=%s candidate_ref=%u task_id=%s",
             decision_id, (unsigned)decision->winner_ref, snapshot.task_id);
    cJSON *details = rehab_task_decision_details_build(
        handle, rehab_task_decision_start_source_name(entry_source),
        decision->input_root, decision->root, selected, &snapshot);
    if (details == NULL) {
        ESP_LOGW(TAG,
                 "SELECTED_TASK_DETAILS_WRITE_FAILED decision_id=%s stage=build error=%s",
                 decision_id, esp_err_to_name(ESP_ERR_NO_MEM));
    } else {
        const char *details_stage = "serialize";
        esp_err_t details_err = rehab_task_decision_details_write_atomic(
            handle, details, &snapshot, &details_stage);
        if (details_err != ESP_OK) {
            ESP_LOGW(TAG,
                     "SELECTED_TASK_DETAILS_WRITE_FAILED decision_id=%s stage=%s error=%s",
                     decision_id, details_stage, esp_err_to_name(details_err));
        }
        cJSON_Delete(details);
    }
    if (coordinator_lock(handle)) {
        if (handle->coordinator_generation == generation) {
            handle->coordinator_status.state =
                REHAB_TASK_DECISION_COORDINATOR_SELECTED;
            handle->coordinator_status.active = false;
            strlcpy(handle->coordinator_status.task_id, snapshot.task_id,
                    sizeof(handle->coordinator_status.task_id));
            strlcpy(handle->coordinator_status.task_name, snapshot.task_name,
                    sizeof(handle->coordinator_status.task_name));
            strlcpy(handle->coordinator_status.reason, snapshot.reason,
                    sizeof(handle->coordinator_status.reason));
            handle->coordinator_status.score = snapshot.score;
            handle->coordinator_status.target_available =
                snapshot.target_available;
            if (snapshot.target_available) {
                strlcpy(handle->coordinator_status.target_object_id,
                        snapshot.target_object_id,
                        sizeof(handle->coordinator_status.target_object_id));
                strlcpy(handle->coordinator_status.target_class,
                        snapshot.target_class,
                        sizeof(handle->coordinator_status.target_class));
                strlcpy(handle->coordinator_status.target_source_frame_id,
                        snapshot.target_source_frame_id,
                        sizeof(handle->coordinator_status
                                   .target_source_frame_id));
                handle->coordinator_status.target_source_frame_sequence =
                    snapshot.target_source_frame_sequence;
                memcpy(handle->coordinator_status.target_bbox_xyxy,
                       snapshot.target_bbox_xyxy,
                       sizeof(handle->coordinator_status.target_bbox_xyxy));
                handle->coordinator_status.target_geometry_available =
                    snapshot.target_geometry_available;
                handle->coordinator_status.target_geometry_valid =
                    snapshot.target_geometry_valid;
                handle->coordinator_status.target_object_height_available =
                    snapshot.target_object_height_available;
                handle->coordinator_status.target_object_height_mm =
                    snapshot.target_object_height_mm;
                handle->coordinator_status.target_support_height_available =
                    snapshot.target_support_height_available;
                handle->coordinator_status.target_support_height_mm =
                    snapshot.target_support_height_mm;
                handle->coordinator_status.target_top_height_available =
                    snapshot.target_top_height_available;
                handle->coordinator_status.target_top_height_above_floor_mm =
                    snapshot.target_top_height_above_floor_mm;
                handle->coordinator_status.target_distance_available =
                    snapshot.target_distance_available;
                handle->coordinator_status.target_distance_mm =
                    snapshot.target_distance_mm;
                strlcpy(
                    handle->coordinator_status.target_distance_status,
                    snapshot.target_distance_status,
                    sizeof(handle->coordinator_status
                               .target_distance_status));
            }
            strlcpy(handle->coordinator_status.last_error, "none",
                    sizeof(handle->coordinator_status.last_error));
        }
        coordinator_unlock(handle);
    }
    ESP_LOGI(TAG,
             "FORMAL_DECISION_SELECTED generation=%" PRIu32
             " decision_id=%s task_id=%s target=%s",
             generation, decision_id, snapshot.task_id,
             snapshot.target_available ? snapshot.target_object_id : "none");

done:
    cJSON_Delete(selected);
    return failure;
}

static void coordinator_finish_task(rehab_task_decision_handle_t handle)
{
    if (coordinator_lock(handle)) {
        handle->coordinator_task = NULL;
        coordinator_unlock(handle);
    }
    xSemaphoreGive(handle->coordinator_done);
    vTaskDelete(NULL);
}

static void coordinator_worker(void *arg)
{
    rehab_task_decision_handle_t handle = arg;
    uint32_t generation = 0;
    rehab_task_decision_start_source_t entry_source =
        REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX] = {0};
    if (coordinator_lock(handle)) {
        generation = handle->coordinator_generation;
        entry_source = handle->coordinator_entry_source;
        strlcpy(decision_id, handle->coordinator_status.decision_id,
                sizeof(decision_id));
        coordinator_unlock(handle);
    }
    formal_scene_t scene = {0};
    formal_decision_t decision = {0};
    char *prompt = NULL;
    char *llm_input = NULL;
    char *llm_text = NULL;
    const char *failure = NULL;

    esp_err_t err = formal_scene_load(handle, &scene);
    if (err != ESP_OK) {
        failure = "scene_unavailable";
        goto done;
    }
    if (coordinator_lock(handle)) {
        if (handle->coordinator_generation == generation) {
            strlcpy(handle->coordinator_status.source_scan_id,
                    scene.scan_id,
                    sizeof(handle->coordinator_status.source_scan_id));
            strlcpy(handle->coordinator_status.source_scene_sha256,
                    scene.sha256,
                    sizeof(handle->coordinator_status.source_scene_sha256));
        }
        coordinator_unlock(handle);
    }
    err = build_llm_input(handle, &scene, generation, decision_id, &prompt);
    if (err != ESP_OK) {
        failure = "decision_input_invalid";
        goto done;
    }
    esp_err_t artifact_err =
        rehab_task_decision_formal_input_artifact_save_latest(
            prompt, strlen(prompt));
    if (artifact_err != ESP_OK) {
        ESP_LOGW(TAG, "FORMAL_INPUT_ARTIFACT_SAVE_FAILED err=%s",
                 esp_err_to_name(artifact_err));
    }
    esp_err_t compact_err = rehab_task_decision_llm_input_compact(
        prompt, &llm_input);
    if (compact_err != ESP_OK) {
        ESP_LOGW(TAG, "LLM_COMPACT_INPUT_FAILED err=%s fallback=full",
                 esp_err_to_name(compact_err));
    } else {
        artifact_err = rehab_task_decision_llm_input_artifact_save_latest(
            llm_input, strlen(llm_input));
        if (artifact_err != ESP_OK) {
            ESP_LOGW(TAG, "LLM_COMPACT_INPUT_ARTIFACT_SAVE_FAILED err=%s",
                     esp_err_to_name(artifact_err));
        }
    }
    const char *model_input = llm_input != NULL ? llm_input : prompt;
    coordinator_set_state(handle, generation,
                          REHAB_TASK_DECISION_COORDINATOR_DECIDING);
    err = llm_call(handle, generation, 0, decision_id, model_input,
                   &llm_text);
    if (err != ESP_OK) {
        failure = "llm_request_failed";
        goto done;
    }
    coordinator_set_state(handle, generation,
                          REHAB_TASK_DECISION_COORDINATOR_VALIDATING);
    failure = validate_output(handle, &scene, prompt, llm_text, decision_id,
                              &decision);
    if (failure != NULL) {
        ESP_LOGW(TAG, "LLM_OUTPUT_VALIDATE_FAILED field=%s reason=%s",
                 validation_field(failure), failure);
        if (coordinator_lock(handle)) {
            if (handle->coordinator_generation == generation) {
                handle->coordinator_status.retry_used = true;
            }
            coordinator_unlock(handle);
        }
        char *retry = build_repair_prompt(model_input, llm_text, failure);
        formal_decision_release(&decision);
        free(llm_text);
        llm_text = NULL;
        if (retry == NULL) {
            failure = "retry_no_memory";
            goto done;
        }
        ESP_LOGW(TAG, "LLM_OUTPUT_REPAIR_ATTEMPT generation=%" PRIu32
                 " reason=%s", generation, failure);
        coordinator_set_state(handle, generation,
                              REHAB_TASK_DECISION_COORDINATOR_DECIDING);
        err = llm_call(handle, generation, 1, decision_id, retry, &llm_text);
        free(retry);
        if (err != ESP_OK) {
            failure = "llm_retry_failed";
            goto done;
        }
        coordinator_set_state(handle, generation,
                              REHAB_TASK_DECISION_COORDINATOR_VALIDATING);
        failure = validate_output(handle, &scene, prompt, llm_text,
                                  decision_id,
                                  &decision);
        if (failure != NULL) {
            ESP_LOGW(TAG, "LLM_OUTPUT_VALIDATE_FAILED field=%s reason=%s",
                     validation_field(failure), failure);
            goto done;
        }
    }
    failure = commit_validated_decision(
        handle, &scene, &decision, generation, decision_id, entry_source);

done:
    if (failure != NULL) {
        /* A failed decision never replaces or invalidates the last committed
         * selected task. There is deliberately no local scoring fallback. */
        coordinator_fail(handle, generation, failure);
    }
    formal_decision_release(&decision);
    free(llm_text);
    cJSON_free(llm_input);
    cJSON_free(prompt);
    formal_scene_release(&scene);
    coordinator_finish_task(handle);
}

const char *rehab_task_decision_coordinator_state_name(
    rehab_task_decision_coordinator_state_t state)
{
    switch (state) {
    case REHAB_TASK_DECISION_COORDINATOR_IDLE: return "IDLE";
    case REHAB_TASK_DECISION_COORDINATOR_PREPARING: return "PREPARING";
    case REHAB_TASK_DECISION_COORDINATOR_DECIDING: return "DECIDING";
    case REHAB_TASK_DECISION_COORDINATOR_VALIDATING: return "VALIDATING";
    case REHAB_TASK_DECISION_COORDINATOR_COMMITTING: return "COMMITTING";
    case REHAB_TASK_DECISION_COORDINATOR_SELECTED: return "SELECTED";
    case REHAB_TASK_DECISION_COORDINATOR_FAILED: return "FAILED";
    case REHAB_TASK_DECISION_COORDINATOR_CANCELLED: return "CANCELLED";
    default: return "UNKNOWN";
    }
}

esp_err_t rehab_task_decision_bind_environment_scan(
    rehab_task_decision_handle_t handle,
    rehab_environment_scan_handle_t environment_scan)
{
    if (handle == NULL || environment_scan == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_status.active) {
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    handle->environment_scan = environment_scan;
    coordinator_unlock(handle);
    return ESP_OK;
}

static rehab_task_decision_formal_package_t *formal_package_detach(
    rehab_task_decision_handle_t handle, uint32_t generation)
{
    rehab_task_decision_formal_package_t *package = NULL;
    if (coordinator_lock(handle)) {
        if (generation == 0U || handle->coordinator_generation == generation) {
            package = handle->coordinator_formal_package;
            handle->coordinator_formal_package = NULL;
        }
        coordinator_unlock(handle);
    }
    return package;
}

static esp_err_t coordinator_reserve_formal(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation)
{
    if (out_generation != NULL) {
        *out_generation = 0U;
    }
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (handle->training_active != NULL &&
        handle->training_active(handle->training_active_ctx)) {
        if (coordinator_lock(handle)) {
            strlcpy(handle->coordinator_status.last_error, "training_active",
                    sizeof(handle->coordinator_status.last_error));
            coordinator_unlock(handle);
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    const bool busy = handle->coordinator_status.active ||
        handle->coordinator_task != NULL;
    coordinator_unlock(handle);
    if (busy) {
        return ESP_ERR_INVALID_STATE;
    }

    formal_scene_t *preflight_scene = calloc(1U, sizeof(*preflight_scene));
    esp_err_t err = preflight_scene == NULL
        ? ESP_ERR_NO_MEM : formal_scene_load(handle, preflight_scene);
    if (preflight_scene != NULL) {
        formal_scene_release(preflight_scene);
        free(preflight_scene);
    }
    if (err != ESP_OK) {
        if (coordinator_lock(handle)) {
            if (!handle->coordinator_status.active) {
                strlcpy(handle->coordinator_status.last_error,
                        err == ESP_ERR_NOT_FOUND ? "scene_unavailable" :
                        (err == ESP_ERR_NO_MEM
                            ? "scene_preflight_no_memory" : "scene_invalid"),
                        sizeof(handle->coordinator_status.last_error));
            }
            coordinator_unlock(handle);
        }
        return err;
    }

    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_status.active ||
        handle->coordinator_task != NULL) {
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    if (source == REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI) {
        claw_core_handle_t root = claw_agent_mgr_get_root_core();
        if (root == NULL) {
            strlcpy(handle->coordinator_status.last_error,
                    "root_core_unavailable",
                    sizeof(handle->coordinator_status.last_error));
            ESP_LOGW(TAG,
                     "FORMAL_UI_SESSION_HISTORY_RESET_SKIPPED reason=root_unavailable");
            coordinator_unlock(handle);
            return ESP_ERR_INVALID_STATE;
        }
        const claw_core_agent_loop_phase_t root_phase =
            claw_core_get_agent_loop_phase(root);
        if (root_phase != CLAW_CORE_AGENT_LOOP_PHASE_IDLE) {
            strlcpy(handle->coordinator_status.last_error, "root_not_idle",
                    sizeof(handle->coordinator_status.last_error));
            ESP_LOGW(TAG,
                     "FORMAL_UI_SESSION_HISTORY_RESET_SKIPPED reason=root_not_idle phase=%d",
                     (int)root_phase);
            coordinator_unlock(handle);
            return ESP_ERR_INVALID_STATE;
        }
        bool deleted_any = false;
        err = claw_memory_delete_session_history(
            REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID, &deleted_any);
        if (err != ESP_OK) {
            strlcpy(handle->coordinator_status.last_error,
                    "session_history_reset_failed",
                    sizeof(handle->coordinator_status.last_error));
            ESP_LOGE(TAG,
                     "FORMAL_UI_SESSION_HISTORY_RESET_FAILED err=%s",
                     esp_err_to_name(err));
            coordinator_unlock(handle);
            return err;
        }
        ESP_LOGI(TAG,
                 "FORMAL_UI_SESSION_HISTORY_RESET deleted_any=%d",
                 deleted_any);
    }
    rehab_task_decision_formal_package_t *previous_package =
        handle->coordinator_formal_package;
    handle->coordinator_formal_package = NULL;
    handle->coordinator_root_request_id = 0U;
    ++handle->coordinator_generation;
    if (handle->coordinator_generation == 0U) {
        ++handle->coordinator_generation;
    }
    const uint32_t generation = handle->coordinator_generation;
    memset(&handle->coordinator_status, 0,
           sizeof(handle->coordinator_status));
    handle->coordinator_status.state =
        REHAB_TASK_DECISION_COORDINATOR_PREPARING;
    handle->coordinator_status.active = true;
    handle->coordinator_status.generation = generation;
    handle->coordinator_entry_source = source;
    const uint64_t trace_start_ms = decision_monotonic_ms();
    snprintf(handle->coordinator_status.decision_id,
             sizeof(handle->coordinator_status.decision_id),
             "decision-%08" PRIx32 "-%" PRIu64,
             generation, trace_start_ms);
    rehab_decision_trace_begin(
        &handle->coordinator_trace, generation,
        handle->coordinator_status.decision_id, trace_start_ms);
    strlcpy(handle->coordinator_status.last_error, "none",
            sizeof(handle->coordinator_status.last_error));
    coordinator_unlock(handle);
    formal_package_release(previous_package);
    if (out_generation != NULL) {
        *out_generation = generation;
    }

    rehab_task_decision_selected_snapshot_t *previous = calloc(
        1U, sizeof(*previous));
    if (previous == NULL) {
        coordinator_fail(handle, generation, "selection_snapshot_no_memory");
        return ESP_ERR_NO_MEM;
    }
    char previous_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX] = {0};
    err = rehab_task_decision_selected_begin(
        handle, previous, previous_sha256);
    free(previous);
    if (err != ESP_OK) {
        coordinator_fail(handle, generation, "selection_snapshot_failed");
        return err;
    }
    ESP_LOGI(TAG,
             "FORMAL_DECISION_ROOT_RESERVE generation=%" PRIu32
             " decision_id=%s entry_source=%s",
             generation, handle->coordinator_status.decision_id,
             rehab_task_decision_start_source_name(source));
    (void)rehab_business_audio_notify(REHAB_BUSINESS_AUDIO_DECISION_STARTED);
    return ESP_OK;
}

static bool session_binding_matches(const char *expected,
                                    const char *actual)
{
    const char *left = expected != NULL ? expected : "";
    const char *right = actual != NULL ? actual : "";
    return strcmp(left, right) == 0;
}

static esp_err_t formal_input_render(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t request_id,
    const char *session_id,
    char *output,
    size_t output_size,
    rehab_task_decision_tool_error_t *out_error,
    bool require_reserved)
{
    if (handle == NULL || request_id == 0U || output == NULL ||
        output_size == 0U || out_error == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_error, 0, sizeof(*out_error));
    uint32_t generation = 0U;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX] = {0};
    bool reserve = false;
    if (!coordinator_lock(handle)) {
        tool_error_set(out_error, "coordinator_lock_timeout", "request_id",
                       "coordinator lock timeout", false);
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->coordinator_status.active) {
        if (require_reserved) {
            coordinator_unlock(handle);
            tool_error_set(out_error, "formal_generation_not_reserved",
                           "request_id",
                           "LOCAL_UI generation must be reserved before "
                           "request-start context collection",
                           false);
            return ESP_ERR_INVALID_STATE;
        }
        reserve = true;
    } else if (handle->coordinator_entry_source != source ||
               handle->coordinator_formal_package != NULL) {
        coordinator_unlock(handle);
        tool_error_set(out_error, "formal_decision_busy", "request_id",
                       "another frozen package is active", false);
        return ESP_ERR_INVALID_STATE;
    } else if (handle->coordinator_root_request_id != 0U &&
               handle->coordinator_root_request_id != request_id) {
        coordinator_unlock(handle);
        tool_error_set(out_error, "request_binding_mismatch", "request_id",
                       "request does not own the reserved generation", false);
        return ESP_ERR_INVALID_STATE;
    } else {
        handle->coordinator_root_request_id = request_id;
        generation = handle->coordinator_generation;
        strlcpy(decision_id, handle->coordinator_status.decision_id,
                sizeof(decision_id));
    }
    coordinator_unlock(handle);

    esp_err_t err = ESP_OK;
    if (reserve) {
        err = coordinator_reserve_formal(handle, source, &generation);
        if (err != ESP_OK) {
            tool_error_set(out_error,
                           err == ESP_ERR_NOT_FOUND ? "scene_unavailable" :
                           (err == ESP_ERR_INVALID_STATE
                                ? "formal_decision_busy" :
                                "formal_input_reserve_failed"),
                           "scene", esp_err_to_name(err), false);
            return err;
        }
        if (!coordinator_lock(handle)) {
            coordinator_fail(handle, generation, "coordinator_lock_timeout");
            tool_error_set(out_error, "coordinator_lock_timeout", "request_id",
                           "coordinator lock timeout", false);
            return ESP_ERR_TIMEOUT;
        }
        handle->coordinator_root_request_id = request_id;
        strlcpy(decision_id, handle->coordinator_status.decision_id,
                sizeof(decision_id));
        coordinator_unlock(handle);
    }

    rehab_task_decision_formal_package_t *package = calloc(
        1U, sizeof(*package));
    if (package == NULL) {
        coordinator_fail(handle, generation, "formal_package_no_memory");
        tool_error_set(out_error, "formal_package_no_memory", "package",
                       "unable to allocate frozen package", false);
        return ESP_ERR_NO_MEM;
    }
    err = formal_scene_load(handle, &package->scene);
    if (err == ESP_OK) {
        err = build_llm_input(handle, &package->scene, generation,
                              decision_id, &package->input);
    }
    if (err != ESP_OK || package->input == NULL) {
        formal_package_release(package);
        coordinator_fail(handle, generation, "decision_input_invalid");
        tool_error_set(out_error, "decision_input_invalid", "formal_input",
                       esp_err_to_name(err), false);
        return err != ESP_OK ? err : ESP_FAIL;
    }
    const size_t input_length = strlen(package->input);
    if (input_length >= output_size) {
        formal_package_release(package);
        coordinator_fail(handle, generation, "formal_input_bridge_too_small");
        tool_error_set(out_error, "formal_input_bridge_too_small",
                       "formal_input", "complete v4 package does not fit",
                       false);
        return ESP_ERR_INVALID_SIZE;
    }
    package->request_id = request_id;
    package->generation = generation;
    package->entry_source = source;
    strlcpy(package->session_id, session_id != NULL ? session_id : "",
            sizeof(package->session_id));

    if (!coordinator_lock(handle)) {
        formal_package_release(package);
        coordinator_fail(handle, generation, "coordinator_lock_timeout");
        tool_error_set(out_error, "coordinator_lock_timeout", "request_id",
                       "coordinator lock timeout", false);
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_generation != generation ||
        !handle->coordinator_status.active ||
        handle->coordinator_formal_package != NULL ||
        handle->coordinator_root_request_id != request_id) {
        coordinator_unlock(handle);
        formal_package_release(package);
        tool_error_set(out_error, "request_binding_mismatch", "request_id",
                       "generation changed while freezing input", false);
        return ESP_ERR_INVALID_STATE;
    }
    handle->coordinator_formal_package = package;
    strlcpy(handle->coordinator_status.source_scan_id,
            package->scene.scan_id,
            sizeof(handle->coordinator_status.source_scan_id));
    strlcpy(handle->coordinator_status.source_scene_sha256,
            package->scene.sha256,
            sizeof(handle->coordinator_status.source_scene_sha256));
    handle->coordinator_status.state =
        REHAB_TASK_DECISION_COORDINATOR_DECIDING;
    trace_transition_locked(
        handle, generation, REHAB_DECISION_TRACE_INPUT_READY, NULL);
    coordinator_unlock(handle);
    memcpy(output, package->input, input_length + 1U);
    ESP_LOGI(TAG,
             "FORMAL_INPUT_GET request_id=%" PRIu32
             " generation=%" PRIu32 " decision_id=%s scan_id=%s sha=%s entry_source=%s bytes=%u",
             request_id, generation, decision_id, package->scene.scan_id,
             package->scene.sha256,
             rehab_task_decision_start_source_name(source),
             (unsigned)input_length);
    const esp_err_t artifact_err =
        rehab_task_decision_formal_input_artifact_save_latest(
            output, input_length);
    if (artifact_err != ESP_OK) {
        ESP_LOGW(TAG,
                 "FORMAL_INPUT_ARTIFACT_SAVE_FAILED err=%s",
                 esp_err_to_name(artifact_err));
    }
    return ESP_OK;
}

esp_err_t rehab_task_decision_formal_input_render(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t request_id,
    const char *session_id,
    char *output,
    size_t output_size,
    rehab_task_decision_tool_error_t *out_error)
{
    return formal_input_render(handle, source, request_id, session_id,
                               output, output_size, out_error, false);
}

esp_err_t rehab_task_decision_formal_fast_request_validate(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id)
{
    if (handle == NULL || request_id == 0U || session_id == NULL ||
        strcmp(session_id, REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    const bool valid = handle->coordinator_status.active &&
        handle->coordinator_entry_source ==
            REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI &&
        handle->coordinator_formal_package == NULL &&
        (handle->coordinator_root_request_id == 0U ||
         handle->coordinator_root_request_id == request_id);
    coordinator_unlock(handle);
    return valid ? ESP_OK : ESP_ERR_INVALID_STATE;
}

esp_err_t rehab_task_decision_formal_fast_input_render(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id,
    char *output,
    size_t output_size,
    rehab_task_decision_tool_error_t *out_error)
{
    if (session_id == NULL ||
        strcmp(session_id, REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    return formal_input_render(
        handle, REHAB_TASK_DECISION_START_SOURCE_LOCAL_UI, request_id,
        session_id, output, output_size, out_error, true);
}

esp_err_t rehab_task_decision_trace_context_ready(
    rehab_task_decision_handle_t handle,
    uint32_t request_id)
{
    if (handle == NULL || request_id == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (!handle->coordinator_status.active ||
        handle->coordinator_root_request_id != request_id ||
        handle->coordinator_formal_package == NULL) {
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    const uint32_t generation = handle->coordinator_generation;
    trace_transition_locked(
        handle, generation, REHAB_DECISION_TRACE_CONTEXT_READY, NULL);
    /* The context provider has completed. The next externally observable
     * boundary is waiting for the Root Agent result, not DeepSeek HTTP. */
    trace_transition_locked(
        handle, generation, REHAB_DECISION_TRACE_MODEL_WAIT, NULL);
    coordinator_unlock(handle);
    return ESP_OK;
}

static bool validation_failure_retryable(const char *failure)
{
    return failure != NULL &&
        strstr(failure, ".availability") == NULL &&
        strstr(failure, "request_binding") == NULL;
}

esp_err_t rehab_task_decision_formal_commit(
    rehab_task_decision_handle_t handle,
    uint32_t request_id,
    const char *session_id,
    const char *decision_output_json,
    rehab_task_decision_tool_error_t *out_error)
{
    if (handle == NULL || request_id == 0U || decision_output_json == NULL ||
        out_error == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out_error, 0, sizeof(*out_error));
    rehab_task_decision_formal_package_t *package = NULL;
    char decision_id[REHAB_TASK_DECISION_DECISION_ID_MAX] = {0};
    bool retry_used = false;
    if (!coordinator_lock(handle)) {
        tool_error_set(out_error, "coordinator_lock_timeout", "request_id",
                       "coordinator lock timeout", false);
        return ESP_ERR_TIMEOUT;
    }
    package = handle->coordinator_formal_package;
    if (!handle->coordinator_status.active || package == NULL) {
        coordinator_unlock(handle);
        tool_error_set(out_error, "formal_package_missing", "request_id",
                       "call formal_input_get once before commit", false);
        return ESP_ERR_INVALID_STATE;
    }
    if (package->request_id != request_id ||
        !session_binding_matches(package->session_id, session_id)) {
        coordinator_unlock(handle);
        tool_error_set(out_error, "request_binding_mismatch", "request_id",
                       "commit must use the frozen package owner request",
                       false);
        return ESP_ERR_INVALID_STATE;
    }
    if (handle->coordinator_status.state !=
        REHAB_TASK_DECISION_COORDINATOR_DECIDING) {
        coordinator_unlock(handle);
        tool_error_set(out_error, "commit_already_in_progress", "request_id",
                       "commit is not available in the current state", false);
        return ESP_ERR_INVALID_STATE;
    }
    retry_used = handle->coordinator_status.retry_used;
    trace_transition_locked(
        handle, package->generation, REHAB_DECISION_TRACE_TOOL_CALL, NULL);
    handle->coordinator_status.state =
        REHAB_TASK_DECISION_COORDINATOR_VALIDATING;
    trace_transition_locked(
        handle, package->generation, REHAB_DECISION_TRACE_VALIDATING, NULL);
    strlcpy(decision_id, handle->coordinator_status.decision_id,
            sizeof(decision_id));
    coordinator_unlock(handle);

    formal_decision_t decision = {0};
    const char *failure = validate_output(
        handle, &package->scene, package->input, decision_output_json,
        decision_id, &decision);
    if (failure != NULL) {
        const bool retryable = validation_failure_retryable(failure) &&
            !retry_used;
        const char *field = validation_field(failure);
        ESP_LOGW(TAG,
                 "FORMAL_COMMIT_VALIDATE_FAILED request_id=%" PRIu32
                 " generation=%" PRIu32 " field=%s retry_allowed=%d reason=%s",
                 request_id, package->generation, field, retryable, failure);
        formal_decision_release(&decision);
        if (retryable && coordinator_lock(handle)) {
            if (handle->coordinator_formal_package == package &&
                handle->coordinator_generation == package->generation) {
                handle->coordinator_status.retry_used = true;
                handle->coordinator_status.state =
                    REHAB_TASK_DECISION_COORDINATOR_DECIDING;
                trace_transition_locked(
                    handle, package->generation,
                    REHAB_DECISION_TRACE_MODEL_WAIT, NULL);
            }
            coordinator_unlock(handle);
            tool_error_set(out_error, "validation_failed", field, failure,
                           true);
            return ESP_ERR_INVALID_RESPONSE;
        }
        coordinator_fail(handle, package->generation, "validation_failed");
        formal_package_release(formal_package_detach(
            handle, package->generation));
        tool_error_set(out_error,
                       retry_used ? "validation_retry_exhausted" :
                           "validation_failed",
                       field, failure, false);
        return ESP_ERR_INVALID_RESPONSE;
    }

    failure = commit_validated_decision(
        handle, &package->scene, &decision, package->generation,
        decision_id, package->entry_source);
    formal_decision_release(&decision);
    if (failure != NULL) {
        const uint32_t generation = package->generation;
        coordinator_fail(handle, generation, failure);
        formal_package_release(formal_package_detach(handle, generation));
        tool_error_set(out_error, failure, validation_field(failure),
                       failure, false);
        return ESP_FAIL;
    }
    const uint32_t generation = package->generation;
    formal_package_release(formal_package_detach(handle, generation));
    if (coordinator_lock(handle)) {
        trace_transition_locked(
            handle, generation, REHAB_DECISION_TRACE_SUCCESS, NULL);
        coordinator_unlock(handle);
    }
    ESP_LOGI(TAG,
             "FORMAL_COMMIT_OK request_id=%" PRIu32
             " generation=%" PRIu32 " decision_id=%s",
             request_id, generation, decision_id);
    return ESP_OK;
}

static void formal_root_completion_waiter(void *arg)
{
    formal_root_waiter_context_t *context = arg;
    rehab_task_decision_handle_t handle = context->handle;
    const uint32_t generation = context->generation;
    const uint32_t request_id = context->request_id;
    free(context);
    claw_core_response_t response = {0};
    esp_err_t err = claw_agent_mgr_receive_root_for(
        request_id, &response, FORMAL_ROOT_COMPLETION_TIMEOUT_MS);
    rehab_task_decision_coordinator_status_t status = {0};
    esp_err_t status_err = rehab_task_decision_coordinator_get_status(
        handle, &status);
    if (status_err == ESP_OK && status.generation == generation &&
        status.active) {
        const char *failure = err == ESP_OK &&
            response.status == CLAW_CORE_RESPONSE_STATUS_OK
                ? "root_completed_without_commit" : "root_request_failed";
        coordinator_fail(handle, generation, failure);
        formal_package_release(formal_package_detach(handle, generation));
    }
    claw_core_response_free(&response);
    if (coordinator_lock(handle)) {
        handle->coordinator_task = NULL;
        coordinator_unlock(handle);
    }
    xSemaphoreGive(handle->coordinator_done);
    vTaskDelete(NULL);
}

esp_err_t rehab_task_decision_coordinator_start_root(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation)
{
    uint32_t generation = 0U;
    esp_err_t err = coordinator_reserve_formal(handle, source, &generation);
    if (out_generation != NULL) {
        *out_generation = generation;
    }
    if (err != ESP_OK) {
        return err;
    }
    formal_root_waiter_context_t *context = calloc(1U, sizeof(*context));
    if (context == NULL) {
        coordinator_fail(handle, generation, "root_waiter_no_memory");
        return ESP_ERR_NO_MEM;
    }
    if (!coordinator_lock(handle)) {
        free(context);
        coordinator_fail(handle, generation, "coordinator_lock_timeout");
        return ESP_ERR_TIMEOUT;
    }
    handle->coordinator_task = (TaskHandle_t)1;
    coordinator_unlock(handle);
    static const char internal_request[] =
        REHAB_TASK_DECISION_FORMAL_ROOT_REQUEST_TEXT;
    const claw_core_llm_request_options_t llm_options =
        formal_reasoning_options();
    uint32_t request_id = 0U;
    err = claw_agent_mgr_submit_root_text_with_llm_options(
        internal_request, REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID, 0U,
        &llm_options, FORMAL_ROOT_SUBMIT_TIMEOUT_MS, &request_id);
    if (err != ESP_OK) {
        free(context);
        coordinator_fail(handle, generation, "root_submit_failed");
        if (coordinator_lock(handle)) {
            handle->coordinator_task = NULL;
            coordinator_unlock(handle);
        }
        return err;
    }
    if (!coordinator_lock(handle)) {
        free(context);
        coordinator_fail(handle, generation, "coordinator_lock_timeout");
        return ESP_ERR_TIMEOUT;
    }
    handle->coordinator_root_request_id = request_id;
    coordinator_unlock(handle);
    context->handle = handle;
    context->generation = generation;
    context->request_id = request_id;
    TaskHandle_t task = NULL;
    if (xTaskCreate(formal_root_completion_waiter,
                    "rehab_formal_root_wait", FORMAL_ROOT_WAITER_STACK,
                    context, FORMAL_COORDINATOR_PRIORITY, &task) != pdPASS) {
        free(context);
        coordinator_fail(handle, generation, "root_waiter_create_failed");
        if (coordinator_lock(handle)) {
            handle->coordinator_task = NULL;
            coordinator_unlock(handle);
        }
        return ESP_ERR_NO_MEM;
    }
    if (coordinator_lock(handle)) {
        if (handle->coordinator_task == (TaskHandle_t)1) {
            handle->coordinator_task = task;
        }
        coordinator_unlock(handle);
    }
    ESP_LOGI(TAG,
             "FORMAL_UI_ROOT_SUBMIT generation=%" PRIu32
             " request_id=%" PRIu32 " session=%s flags=0",
             generation, request_id,
             REHAB_TASK_DECISION_FORMAL_UI_SESSION_ID);
    return ESP_OK;
}

esp_err_t rehab_task_decision_coordinator_start(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_start_source_t source,
    uint32_t *out_generation)
{
    if (out_generation != NULL) {
        *out_generation = 0U;
    }
    if (handle == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (handle->training_active != NULL &&
        handle->training_active(handle->training_active_ctx)) {
        if (coordinator_lock(handle)) {
            strlcpy(handle->coordinator_status.last_error,
                    "training_active",
                    sizeof(handle->coordinator_status.last_error));
            coordinator_unlock(handle);
        }
        return ESP_ERR_INVALID_STATE;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_status.active ||
        handle->coordinator_task != NULL) {
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    if (!handle->coordinator_llm_ready) {
        strlcpy(handle->coordinator_status.last_error,
                "llm_not_configured",
                sizeof(handle->coordinator_status.last_error));
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    coordinator_unlock(handle);

    /* Pure preflight failures are rejections, not generation-0 executions. */
    formal_scene_t *preflight_scene = calloc(1U, sizeof(*preflight_scene));
    esp_err_t preflight_err = preflight_scene == NULL
        ? ESP_ERR_NO_MEM : formal_scene_load(handle, preflight_scene);
    if (preflight_scene != NULL) {
        formal_scene_release(preflight_scene);
        free(preflight_scene);
    }
    if (preflight_err != ESP_OK) {
        if (coordinator_lock(handle)) {
            if (!handle->coordinator_status.active) {
                strlcpy(handle->coordinator_status.last_error,
                        preflight_err == ESP_ERR_NOT_FOUND
                            ? "scene_unavailable" :
                        (preflight_err == ESP_ERR_NO_MEM
                            ? "scene_preflight_no_memory" : "scene_invalid"),
                        sizeof(handle->coordinator_status.last_error));
            }
            coordinator_unlock(handle);
        }
        return preflight_err;
    }

    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_status.active ||
        handle->coordinator_task != NULL) {
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    if (!handle->coordinator_llm_ready) {
        strlcpy(handle->coordinator_status.last_error,
                "llm_not_configured",
                sizeof(handle->coordinator_status.last_error));
        coordinator_unlock(handle);
        return ESP_ERR_INVALID_STATE;
    }
    ++handle->coordinator_generation;
    if (handle->coordinator_generation == 0U) {
        ++handle->coordinator_generation;
    }
    uint32_t generation = handle->coordinator_generation;
    char decision_id[
        sizeof(handle->coordinator_status.decision_id)] = {0};
    memset(&handle->coordinator_status, 0,
           sizeof(handle->coordinator_status));
    handle->coordinator_status.state =
        REHAB_TASK_DECISION_COORDINATOR_PREPARING;
    handle->coordinator_status.active = true;
    handle->coordinator_entry_source = source;
    /* Reserve the single worker slot before dropping the lock.  The task can
     * otherwise finish before xTaskCreate() returns its handle, leaving a
     * stale non-NULL handle that permanently reports BUSY. */
    handle->coordinator_task = (TaskHandle_t)1;
    handle->coordinator_status.generation = generation;
    const uint64_t trace_start_ms = decision_monotonic_ms();
    snprintf(handle->coordinator_status.decision_id,
             sizeof(handle->coordinator_status.decision_id),
             "decision-%08" PRIx32 "-%" PRIu64,
             generation, trace_start_ms);
    rehab_decision_trace_begin(
        &handle->coordinator_trace, generation,
        handle->coordinator_status.decision_id, trace_start_ms);
    strlcpy(decision_id, handle->coordinator_status.decision_id,
            sizeof(decision_id));
    strlcpy(handle->coordinator_status.last_error, "none",
            sizeof(handle->coordinator_status.last_error));
    coordinator_unlock(handle);
    if (out_generation != NULL) {
        *out_generation = generation;
    }

    rehab_task_decision_selected_snapshot_t *previous = calloc(
        1U, sizeof(*previous));
    if (previous == NULL) {
        coordinator_fail(handle, generation, "selection_snapshot_no_memory");
        if (coordinator_lock(handle)) {
            handle->coordinator_task = NULL;
            coordinator_unlock(handle);
        }
        return ESP_ERR_NO_MEM;
    }
    char previous_sha256[REHAB_TASK_DECISION_SCENE_SHA256_MAX] = {0};
    esp_err_t err = rehab_task_decision_selected_begin(
        handle, previous, previous_sha256);
    free(previous);
    if (err != ESP_OK) {
        coordinator_fail(handle, generation, "selection_snapshot_failed");
        if (coordinator_lock(handle)) {
            handle->coordinator_task = NULL;
            coordinator_unlock(handle);
        }
        return err;
    }
    (void)xSemaphoreTake(handle->coordinator_done, 0);
    TaskHandle_t task = NULL;
    if (xTaskCreate(coordinator_worker, "rehab_task_decision",
                    FORMAL_COORDINATOR_STACK, handle,
                    FORMAL_COORDINATOR_PRIORITY, &task) != pdPASS) {
        coordinator_fail(handle, generation, "worker_create_failed");
        if (coordinator_lock(handle)) {
            handle->coordinator_task = NULL;
            coordinator_unlock(handle);
        }
        return ESP_ERR_NO_MEM;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    if (handle->coordinator_task == (TaskHandle_t)1) {
        handle->coordinator_task = task;
    }
    coordinator_unlock(handle);
    ESP_LOGI(TAG,
             "FORMAL_DECISION_START state=PREPARING generation=%" PRIu32
             " decision_id=%s",
             generation, decision_id);
    (void)rehab_business_audio_notify(REHAB_BUSINESS_AUDIO_DECISION_STARTED);
    return ESP_OK;
}

esp_err_t rehab_task_decision_coordinator_get_status(
    rehab_task_decision_handle_t handle,
    rehab_task_decision_coordinator_status_t *out_status)
{
    if (handle == NULL || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!coordinator_lock(handle)) {
        return ESP_ERR_TIMEOUT;
    }
    memcpy(out_status, &handle->coordinator_status, sizeof(*out_status));
    rehab_decision_trace_project(
        &handle->coordinator_trace, decision_monotonic_ms(),
        &out_status->trace);
    coordinator_unlock(handle);
    return ESP_OK;
}

esp_err_t rehab_task_decision_coordinator_wait(
    rehab_task_decision_handle_t handle,
    uint32_t generation,
    uint32_t timeout_ms,
    rehab_task_decision_coordinator_status_t *out_status)
{
    if (handle == NULL || generation == 0U || out_status == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    uint64_t deadline = (uint64_t)(esp_timer_get_time() / 1000) +
        (timeout_ms > 0U ? timeout_ms : FORMAL_DEFAULT_LLM_TIMEOUT_MS);
    do {
        esp_err_t err = rehab_task_decision_coordinator_get_status(
            handle, out_status);
        if (err != ESP_OK) {
            return err;
        }
        if (out_status->generation != generation) {
            return ESP_ERR_INVALID_STATE;
        }
        if (!out_status->active) {
            return ESP_OK;
        }
        vTaskDelay(pdMS_TO_TICKS(FORMAL_WAIT_SLICE_MS));
    } while ((uint64_t)(esp_timer_get_time() / 1000) < deadline);
    return ESP_ERR_TIMEOUT;
}

esp_err_t rehab_task_decision_coordinator_initialize(
    rehab_task_decision_handle_t handle,
    const rehab_task_decision_config_t *config)
{
    if (handle == NULL || config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    handle->coordinator_lock = xSemaphoreCreateMutex();
    handle->coordinator_done = xSemaphoreCreateBinary();
    if (handle->coordinator_lock == NULL ||
        handle->coordinator_done == NULL) {
        return ESP_ERR_NO_MEM;
    }
    handle->coordinator_status.state =
        REHAB_TASK_DECISION_COORDINATOR_IDLE;
    rehab_decision_trace_reset(&handle->coordinator_trace);
    handle->coordinator_entry_source =
        REHAB_TASK_DECISION_START_SOURCE_UNKNOWN;
    strlcpy(handle->coordinator_status.last_error, "none",
            sizeof(handle->coordinator_status.last_error));
    /* Phase A leaves the legacy raw-completion code for audited Phase B
     * deletion, but deliberately creates no 0x8C1/2241 core. Formal business
     * decisions are owned only by the standard Root Agent tool loop. */
    (void)config;
    ESP_LOGI(TAG,
             "FORMAL_DECISION_ROOT ready=1 legacy_2241=disabled input_max=%u output_max=%u",
             (unsigned)REHAB_TASK_DECISION_LLM_INPUT_MAX,
             (unsigned)REHAB_TASK_DECISION_LLM_OUTPUT_MAX);
    return ESP_OK;
}

void rehab_task_decision_coordinator_release(
    rehab_task_decision_handle_t handle)
{
    if (handle == NULL) {
        return;
    }
    if (handle->coordinator_llm_core != NULL) {
        (void)claw_core_stop(handle->coordinator_llm_core, 2000U);
        (void)claw_core_destroy(handle->coordinator_llm_core);
        handle->coordinator_llm_core = NULL;
    }
    formal_package_release(handle->coordinator_formal_package);
    handle->coordinator_formal_package = NULL;
    if (handle->coordinator_done != NULL) {
        vSemaphoreDelete(handle->coordinator_done);
        handle->coordinator_done = NULL;
    }
    if (handle->coordinator_lock != NULL) {
        vSemaphoreDelete(handle->coordinator_lock);
        handle->coordinator_lock = NULL;
    }
}
