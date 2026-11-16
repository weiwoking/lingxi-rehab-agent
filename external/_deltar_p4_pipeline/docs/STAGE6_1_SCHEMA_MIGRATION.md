# Stage 6.1 Schema Migration

Input contracts and CLI commands are unchanged. `scan-run` now writes frame/result/job `v1.1`. `scan-inspect` accepts historical scan-result `v1`, marks it `legacy: true`, and applies only the historical shallow validator; it never presents v1 as v1.1. New v1.1 data receives strict nested runtime validation and schema failures return exit code 7.

P4 must consume v1.1 only. The schemas use closed critical objects and explicit required/type/enum/null/range/hash/error-code rules. Missing `distance.valid`, `visibility.status`, a height `absolute_valid`, a provenance model hash, or job `exit_code` is rejected.
