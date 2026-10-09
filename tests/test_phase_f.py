"""Tests for Phase F (slices 36-40): the Hailo neural pipeline.

Covers the runtime abstraction, embedding pipeline, mock TTS/STT,
and the neural workload scheduler.  All tests are deterministic and
require no Hailo hardware.
"""

import io
import math
import wave

import pytest

from hlidskjalf.hailo import (
    DONE,
    FAILED,
    QUEUED,
    REAL_BACKEND,
    MOCK_BACKEND,
    Backend,
    HailoRuntime,
    MockBackend,
    MockSttError,
    NeuralScheduler,
    RealBackend,
    VoiceConfig,
    batch_embed,
    cosine_similarity,
    embed,
    get_voice,
    resolve_backend_name,
    save_wav,
    synthesize,
    transcribe,
)


# ----------------------------------------------------------------------
# Slice 36 — runtime abstraction
# ----------------------------------------------------------------------
class TestBackendSelection:
    def test_default_is_mock(self):
        assert resolve_backend_name() == MOCK_BACKEND

    def test_explicit_mock(self):
        assert resolve_backend_name("mock") == MOCK_BACKEND

    def test_explicit_real(self):
        assert resolve_backend_name("real") == REAL_BACKEND

    def test_invalid_backend_rejected(self):
        with pytest.raises(ValueError):
            resolve_backend_name("tpu")

    def test_config_dict_mock_false_selects_real(self):
        assert resolve_backend_name(config={"hailo": {"mock": False}}) == REAL_BACKEND

    def test_config_dict_mock_true_selects_mock(self):
        assert resolve_backend_name(config={"hailo": {"mock": True}}) == MOCK_BACKEND

    def test_env_var_selects_real(self, monkeypatch):
        monkeypatch.setenv("HLIDSKJALF_HAILO_MOCK", "0")
        assert resolve_backend_name() == REAL_BACKEND

    def test_missing_yaml_still_defaults_to_mock(self, tmp_path, monkeypatch):
        # A missing config file must never crash backend selection.
        import hlidskjalf.hailo.runtime as rt

        monkeypatch.setattr(rt, "_default_config_path", lambda: tmp_path / "nope.yaml")
        assert rt.resolve_backend_name() == MOCK_BACKEND


class TestMockBackend:
    def test_is_available(self):
        assert MockBackend().is_available() is True

    def test_device_info(self):
        info = MockBackend().device_info()
        assert info["backend"] == "mock"
        assert info["real_hardware"] is False

    def test_embed_pipeline(self):
        vecs = MockBackend().embed(["hello"])
        assert len(vecs) == 1 and len(vecs[0]) == 128

    def test_synthesize_pipeline(self):
        wav = MockBackend().synthesize("hi")
        assert wav[:4] == b"RIFF"

    def test_transcribe_round_trip(self):
        assert MockBackend().transcribe(MockBackend().synthesize("hi")) == "hi"


class TestRealBackend:
    def test_not_available_without_hailort(self):
        assert RealBackend().is_available() is False

    def test_device_info_raises_clear_message(self):
        with pytest.raises(NotImplementedError) as exc:
            RealBackend().device_info()
        msg = str(exc.value)
        assert "Hailo" in msg and "hailo.mock" in msg

    def test_inference_calls_raise(self):
        backend = RealBackend()
        with pytest.raises(NotImplementedError):
            backend.embed(["x"])
        with pytest.raises(NotImplementedError):
            backend.synthesize("x")
        with pytest.raises(NotImplementedError):
            backend.transcribe(b"x")


class TestHailoRuntime:
    def test_default_runtime_uses_mock(self):
        rt = HailoRuntime()
        assert rt.backend_name == MOCK_BACKEND
        assert isinstance(rt.backend, MockBackend)
        assert isinstance(rt.backend, Backend)

    def test_real_runtime_selection(self):
        rt = HailoRuntime(backend="real")
        assert rt.backend_name == REAL_BACKEND
        assert isinstance(rt.backend, RealBackend)

    def test_neural_surface(self):
        rt = HailoRuntime()
        vec = rt.embed("wyrd")
        assert len(vec) == 128
        wav = rt.synthesize("wyrd")
        assert transcribe(wav) == "wyrd"

    def test_context_manager(self):
        with HailoRuntime() as rt:
            assert rt.is_available() is True


# ----------------------------------------------------------------------
# Slice 37 — embeddings
# ----------------------------------------------------------------------
class TestEmbeddings:
    def test_dim_and_normalization(self):
        vec = embed("the norns weave fate")
        assert len(vec) == 128
        assert all(isinstance(x, float) for x in vec)
        assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-9)

    def test_deterministic(self):
        assert embed("heimdall watches") == embed("heimdall watches")

    def test_different_texts_differ(self):
        assert embed("odin") != embed("loki")

    def test_empty_text_still_valid(self):
        vec = embed("")
        assert len(vec) == 128
        assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-9)

    def test_custom_dim(self):
        assert len(embed("yggdrasil", dim=64)) == 64

    def test_type_and_value_errors(self):
        with pytest.raises(TypeError):
            embed(123)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            embed("x", dim=0)

    def test_cosine_self_is_one(self):
        vec = embed("frith")
        assert math.isclose(cosine_similarity(vec, vec), 1.0, rel_tol=1e-9)

    def test_cosine_bounded(self):
        a, b = embed("sun"), embed("moon")
        assert -1.0 <= cosine_similarity(a, b) <= 1.0

    def test_cosine_known_values(self):
        assert math.isclose(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        assert math.isclose(cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_cosine_length_mismatch(self):
        with pytest.raises(ValueError):
            cosine_similarity([1.0], [1.0, 2.0])

    def test_batch_embed_matches_individual(self):
        texts = ["one", "two", "three"]
        assert batch_embed(texts) == [embed(t) for t in texts]


# ----------------------------------------------------------------------
# Slice 38 — TTS
# ----------------------------------------------------------------------
def _read_wav_params(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.getparams()


class TestTts:
    def test_valid_wav_header(self):
        wav = synthesize("hello world")
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"

    def test_wave_module_can_parse(self):
        params = _read_wav_params(synthesize("test"))
        assert params.nchannels == 1
        assert params.sampwidth == 2
        assert params.framerate == 22050
        assert params.nframes > 0

    def test_duration_scales_with_text(self):
        short = _read_wav_params(synthesize("hi")).nframes
        long_ = _read_wav_params(synthesize("hi " * 100)).nframes
        assert long_ > short

    def test_duration_bounds(self):
        tiny = _read_wav_params(synthesize("")).nframes
        assert tiny >= int(22050 * 0.25)
        huge = _read_wav_params(synthesize("x" * 100000)).nframes
        assert huge <= int(22050 * 12.0) + 1

    def test_rate_speeds_up(self):
        slow = _read_wav_params(
            synthesize("hello", config=VoiceConfig(rate=1.0))
        ).nframes
        fast = _read_wav_params(
            synthesize("hello", config=VoiceConfig(rate=2.0))
        ).nframes
        assert fast < slow

    def test_voices_differ(self):
        a = synthesize("hello", voice="default")
        b = synthesize("hello", voice="deep")
        assert a != b

    def test_unknown_voice_raises(self):
        with pytest.raises(KeyError):
            synthesize("hello", voice="soprano")

    def test_invalid_voice_config(self):
        with pytest.raises(ValueError):
            VoiceConfig(rate=0)
        with pytest.raises(ValueError):
            VoiceConfig(pitch=-1.0)

    def test_deterministic(self):
        assert synthesize("wyrd") == synthesize("wyrd")

    def test_save_wav_writes_file(self, tmp_path):
        path = save_wav(tmp_path / "out" / "speech", synthesize("hi"))
        assert path.suffix == ".wav"
        assert path.read_bytes()[:4] == b"RIFF"


# ----------------------------------------------------------------------
# Slice 39 — STT
# ----------------------------------------------------------------------
def _plain_wav_without_comment() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 100)
    return buf.getvalue()


class TestStt:
    def test_round_trip(self):
        text = "the high seat sees all"
        assert transcribe(synthesize(text)) == text

    def test_round_trip_unicode(self):
        text = "Hliðskjálf — Óðinn sees ᚱᚢᚾ"
        assert transcribe(synthesize(text)) == text

    def test_round_trip_empty(self):
        assert transcribe(synthesize("")) == ""

    def test_garbage_bytes_rejected(self):
        with pytest.raises(MockSttError):
            transcribe(b"not a wav at all")

    def test_foreign_wav_rejected(self):
        with pytest.raises(MockSttError):
            transcribe(_plain_wav_without_comment())

    def test_wrong_type_rejected(self):
        with pytest.raises(TypeError):
            transcribe("not bytes")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# Slice 40 — scheduler
# ----------------------------------------------------------------------
def _job_fn(value):
    return lambda: value


class TestScheduler:
    def test_submit_returns_ids_and_fifo(self):
        sched = NeuralScheduler()
        a = sched.submit(_job_fn("a"), kind="embed")
        b = sched.submit(_job_fn("b"), kind="embed")
        assert a != b
        assert sched.get(a).id == a
        assert sched.run_next().result == "a"
        assert sched.run_next().result == "b"

    def test_priority_ordering(self):
        sched = NeuralScheduler()
        sched.submit(_job_fn("low"), kind="t", priority=1)
        sched.submit(_job_fn("high"), kind="t", priority=10)
        sched.submit(_job_fn("mid"), kind="t", priority=5)
        order = [sched.run_next().result for _ in range(3)]
        assert order == ["high", "mid", "low"]

    def test_fifo_within_priority(self):
        sched = NeuralScheduler()
        for i in range(5):
            sched.submit(_job_fn(i), kind="t", priority=3)
        order = [sched.run_next().result for _ in range(5)]
        assert order == [0, 1, 2, 3, 4]

    def test_no_starvation_via_aging(self):
        # A low-priority job must eventually outrank a stream of fresh
        # high-priority jobs instead of waiting forever.
        sched = NeuralScheduler()
        low_id = sched.submit(_job_fn("low"), kind="t", priority=0)
        ran = []
        for _ in range(11):
            sched.submit(_job_fn("high"), kind="t", priority=10)
            ran.append(sched.run_next().id)
        assert ran[-1] == low_id
        assert ran[:-1] != [low_id]

    def test_run_next_empty_returns_none(self):
        assert NeuralScheduler().run_next() is None

    def test_job_lifecycle(self):
        sched = NeuralScheduler()
        job_id = sched.submit(_job_fn(42), kind="embed", priority=2)
        job = sched.get(job_id)
        assert job.status == QUEUED
        assert job.kind == "embed"
        assert job.priority == 2
        sched.run_next()
        assert job.status == DONE
        assert job.result == 42

    def test_failed_job_records_error(self):
        def boom():
            raise RuntimeError("npu exploded")

        sched = NeuralScheduler()
        job_id = sched.submit(boom, kind="stt")
        with pytest.raises(RuntimeError):
            sched.run_next()
        job = sched.get(job_id)
        assert job.status == FAILED
        assert isinstance(job.error, RuntimeError)

    def test_run_all_continues_past_failure(self):
        def boom():
            raise RuntimeError("bad")

        sched = NeuralScheduler()
        sched.submit(boom, kind="t")
        sched.submit(_job_fn("ok"), kind="t")
        finished = sched.run_all()
        assert [j.status for j in finished] == [FAILED, DONE]
        assert finished[1].result == "ok"
        assert sched.run_next() is None

    def test_clear(self):
        sched = NeuralScheduler()
        sched.submit(_job_fn(1), kind="t")
        sched.submit(_job_fn(2), kind="t")
        sched.clear()
        assert len(sched) == 0
        assert sched.run_next() is None

    def test_unknown_job_id(self):
        with pytest.raises(KeyError):
            NeuralScheduler().get("job-999")

    def test_submit_non_callable(self):
        with pytest.raises(TypeError):
            NeuralScheduler().submit("not callable", kind="t")  # type: ignore[arg-type]
