from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from rehab_tts_service.cache import AudioCache
from rehab_tts_service.provider import EdgeTTSProvider, ProviderError
from rehab_tts_service.validation import TextValidationError, validate_text


def cache(tmp_path: Path, *, max_files=8, max_bytes=1024 * 1024):
    result = AudioCache(
        tmp_path,
        ttl_seconds=3600,
        max_files=max_files,
        max_bytes=max_bytes,
    )
    result.prepare()
    return result


def test_cache_key_preserves_exact_text_and_options(tmp_path):
    active = cache(tmp_path)
    exact = "  请在14点30分启动P4X和ESP32演示。  "
    key = active.make_key(exact, "voice-a", "+0%", "+0Hz", "provider-v1")
    assert key == active.make_key(
        exact, "voice-a", "+0%", "+0Hz", "provider-v1"
    )
    assert key != active.make_key(
        exact.strip(), "voice-a", "+0%", "+0Hz", "provider-v1"
    )
    assert key != active.make_key(
        exact, "voice-b", "+0%", "+0Hz", "provider-v1"
    )
    assert key != active.make_key(
        exact, "voice-a", "+10%", "+0Hz", "provider-v1"
    )
    assert key != active.make_key(
        exact, "voice-a", "+0%", "+2Hz", "provider-v1"
    )


def test_atomic_failure_leaves_no_cache_entry(tmp_path):
    active = cache(tmp_path)

    async def fail(destination: Path):
        destination.write_bytes(b"partial")
        raise RuntimeError("boom")

    async def run():
        try:
            await active.get_or_create("a" * 64, fail)
        except RuntimeError:
            pass

    asyncio.run(run())
    assert not list(tmp_path.glob("*.mp3"))
    assert not list(tmp_path.glob("*.tmp"))


def test_concurrent_same_key_is_coalesced(tmp_path):
    active = cache(tmp_path)
    calls = 0

    async def produce(destination: Path):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        destination.write_bytes(b"ID3same")
        return 20

    async def run():
        return await asyncio.gather(
            active.get_or_create("b" * 64, produce),
            active.get_or_create("b" * 64, produce),
        )

    entries = asyncio.run(run())
    assert calls == 1
    assert entries[0].audio_id == entries[1].audio_id
    assert sorted(item.cache_hit for item in entries) == [False, True]


def test_cache_prunes_to_count_limit(tmp_path):
    active = cache(tmp_path, max_files=2)
    for index in range(3):
        path = tmp_path / f"{index:064x}.mp3"
        path.write_bytes(b"ID3")
        path.touch()
    active.prune()
    assert len(list(tmp_path.glob("*.mp3"))) == 2


def test_edge_provider_mock_preserves_exact_text(tmp_path, monkeypatch):
    captured = {}

    class Communicate:
        def __init__(self, *, text, voice, rate, pitch):
            captured.update(
                text=text, voice=voice, rate=rate, pitch=pitch
            )

        async def save(self, destination):
            Path(destination).write_bytes(b"ID3mock")

    async def list_voices():
        return [{"ShortName": "zh-CN-XiaoxiaoNeural"}]

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        SimpleNamespace(Communicate=Communicate, list_voices=list_voices),
    )
    provider = EdgeTTSProvider()
    text = "  中文、数字14:30、ESP32、P4X  "
    destination = tmp_path / "out.mp3"

    async def run():
        await provider.validate_voice("zh-CN-XiaoxiaoNeural")
        await provider.synthesize(
            text,
            "zh-CN-XiaoxiaoNeural",
            "+0%",
            "+0Hz",
            destination,
        )

    asyncio.run(run())
    assert captured["text"] == text
    assert destination.read_bytes() == b"ID3mock"


def test_edge_provider_rejects_non_mp3_response(tmp_path, monkeypatch):
    class Communicate:
        def __init__(self, **_):
            pass

        async def save(self, destination):
            Path(destination).write_bytes(b"<html>provider error</html>")

    monkeypatch.setitem(
        sys.modules,
        "edge_tts",
        SimpleNamespace(Communicate=Communicate, list_voices=lambda: None),
    )
    provider = EdgeTTSProvider()

    async def run():
        await provider.synthesize(
            "你好",
            "zh-CN-XiaoxiaoNeural",
            "+0%",
            "+0Hz",
            tmp_path / "out.mp3",
        )

    try:
        asyncio.run(run())
    except ProviderError as exc:
        assert "non-MP3" in str(exc)
    else:
        raise AssertionError("non-MP3 provider output unexpectedly succeeded")


def test_fastapi_source_contract_is_stable():
    source = (
        Path(__file__).resolve().parents[1]
        / "rehab_tts_service"
        / "app.py"
    ).read_text(encoding="utf-8")
    for endpoint in (
        '"/health"',
        '"/api/v1/tts/synthesize"',
        '"/api/v1/tts/audio/{audio_id}"',
    ):
        assert endpoint in source
    assert 'MIME_TYPE = "audio/mpeg"' in source
    assert "validate_text(text)" in source
    assert "runtime.provider.synthesize(" in source
    assert "request.base_url" in source
    assert "{entry.audio_id}.mp3" in source


def test_provider_error_message_has_stable_nonempty_fallback():
    app_path = (
        Path(__file__).resolve().parents[1]
        / "rehab_tts_service"
        / "app.py"
    )
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_exception_message"
    )
    namespace = {}
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[])),
            str(app_path),
            "exec",
        ),
        namespace,
    )
    exception_message = namespace["_exception_message"]

    assert exception_message(TimeoutError()) == "TimeoutError"
    assert exception_message(ProviderError()) == "ProviderError"
    assert exception_message(RuntimeError("stable reason")) == "stable reason"


def test_provider_health_is_snapshot_not_request_gate():
    source = (
        Path(__file__).resolve().parents[1]
        / "rehab_tts_service"
        / "app.py"
    ).read_text(encoding="utf-8")
    synthesize_start = source.index(
        "async def synthesize(payload: SynthesizeRequest"
    )
    validate = source.index("validate_text(text)", synthesize_start)
    cache_key = source.index("runtime.cache.make_key(", validate)
    cache_access = source.index("runtime.cache.get_or_create(", cache_key)

    assert '"provider_unavailable"' not in source
    assert validate < cache_key < cache_access


def test_real_provider_outcome_updates_health_snapshot():
    source = (
        Path(__file__).resolve().parents[1]
        / "rehab_tts_service"
        / "app.py"
    ).read_text(encoding="utf-8")
    synthesize_start = source.index(
        "async def synthesize(payload: SynthesizeRequest"
    )
    provider_call = source.index(
        "await runtime.provider.synthesize(", synthesize_start
    )
    provider_failure = source.index("except ProviderError as exc:", provider_call)
    failure_status = source.index(
        'runtime.provider_status = "error"', provider_failure
    )
    failure_reason = source.index(
        "runtime.provider_error = _exception_message(exc)", failure_status
    )
    success_status = source.index(
        'runtime.provider_status = "ready"', failure_reason
    )
    success_clear = source.index(
        'runtime.provider_error = ""', success_status
    )
    cache_access = source.index(
        "runtime.cache.get_or_create(", success_clear
    )

    assert (
        provider_call
        < provider_failure
        < failure_status
        < failure_reason
        < success_status
        < success_clear
        < cache_access
    )


def test_unicode_codepoint_boundaries():
    assert validate_text("中" * 300) == 300
    for value, category in (("", "empty_text"), (" \t\n", "empty_text")):
        try:
            validate_text(value)
        except TextValidationError as exc:
            assert exc.category == category
        else:
            raise AssertionError("validation unexpectedly succeeded")
    try:
        validate_text("中" * 301)
    except TextValidationError as exc:
        assert exc.category == "text_too_long"
    else:
        raise AssertionError("301 code points unexpectedly succeeded")
