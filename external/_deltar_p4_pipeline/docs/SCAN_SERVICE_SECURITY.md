# PC Scan Service Security

Phase A is intended for a controlled PC or private device link. It does not provide authentication, authorization, TLS, Internet exposure hardening, rate limiting beyond the single-job gate, or durable queue semantics.

Security invariants:

- require bounded `Content-Length`; reject chunked transfer;
- cap the complete multipart request at 3 MiB and independently count every streamed part;
- accept exactly the field set derived from strict `scan.json`;
- reject text fields, duplicate/unknown/missing fields, path separators, absolute names, and traversal;
- construct every destination below the resolved data root from server mappings only;
- use exclusive temporary files, fsync, and no-overwrite rename for uploaded content;
- invoke the frozen worker with an argument list, `shell=False`, timeout, and an environment allowlist;
- expose stable errors only—never traceback, absolute path, command, or log location;
- validate result bytes, strict JSON, finite numbers, schemas, object cap, scan ID, pipeline job ID, and compatible status before publication;
- preserve the pipeline result bytes without rewriting them;
- fail closed and never resume work after restart.

The test matrix covers traversal, duplicate/extra/missing parts, wrong filenames/content types, invalid image size, oversized JSON/request/result, object cap, no-overwrite, root containment, busy/idempotency, and restart behavior.
