// Host/target ABI size probe and compile-time stack regression contract.
#include "rehab_task_decision.h"

_Static_assert(sizeof(rehab_task_decision_start_result_t) <= 320U,
               "confirmed-start result must remain stack-compact");

const unsigned char rehab_size_coordinator_status[
    sizeof(rehab_task_decision_coordinator_status_t)] = {0};
const unsigned char rehab_size_start_result[
    sizeof(rehab_task_decision_start_result_t)] = {0};
const unsigned char rehab_size_selected_snapshot[
    sizeof(rehab_task_decision_selected_snapshot_t)] = {0};
