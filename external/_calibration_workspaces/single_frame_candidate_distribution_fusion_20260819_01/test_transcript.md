# Test Transcript

## audit_script_compile

Exit code: `0`

```text


```

## single_frame_independence

Exit code: `0`

```text
{
  "pass": true,
  "frames": 66,
  "static_hits": []
}

```

## worker_pytest_ownership_probe

Exit code: `0`

```text
worker_pytest_available= False
Tests use the repository test interpreter; the frozen CUDA worker remains dependency-minimal.

```

## focused_production_regression

Exit code: `0`

```text
..............................................                           [100%]
============================== warnings summary ===============================
..\..\Users\Weiwo\AppData\Roaming\Python\Python313\site-packages\fastapi\testclient.py:1
  C:\Users\Weiwo\AppData\Roaming\Python\Python313\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

tests/test_runtime_import_boundaries.py::test_worker_start_failure_keeps_http_health_diagnostic
  C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\scan_service.py:572: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("startup")

tests/test_runtime_import_boundaries.py::test_worker_start_failure_keeps_http_health_diagnostic
tests/test_runtime_import_boundaries.py::test_worker_start_failure_keeps_http_health_diagnostic
  C:\Users\Weiwo\AppData\Roaming\Python\Python313\site-packages\fastapi\applications.py:4681: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    return self.router.on_event(event_type)  # ty: ignore[deprecated]

tests/test_runtime_import_boundaries.py::test_worker_start_failure_keeps_http_health_diagnostic
  C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\scan_service.py:664: DeprecationWarning: 
          on_event is deprecated, use lifespan event handlers instead.
  
          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).
          
    @app.on_event("shutdown")

tests/test_chatgpt_review_bundle.py::test_validator_rejects_duplicate_archive_paths
  C:\ProgramData\anaconda3\Lib\zipfile\__init__.py:1642: UserWarning: Duplicate name: 'same.json'
    return self._open_to_write(zinfo, force_zip64=force_zip64)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
46 passed, 6 warnings in 2.65s

```

## pipeline_diff_check

Exit code: `0`

```text


```

Overall: `PASS`

No device write, `idf.py`, bmgr, build, flash, monitor, reconfigure, clean, or fullclean operation was run.
