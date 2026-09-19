"""Microphone capture and Whisper speech-to-text for Satpuda."""
from __future__ import annotations

import threading

from core.voice.mic_devices import resolve_device, save_device
from core.voice.voice_config import (
    AUDIO_CHUNK_SECONDS,
    AUDIO_GAIN_TARGET,
    COMMAND_LISTEN_MAX_SECONDS,
    COMMAND_SILENCE_SECONDS,
    FULL_LISTEN_MAX_SECONDS,
    FULL_LISTEN_SILENCE_SECONDS,
    get_command_listen_prompt,
    get_recognition_profile,
    get_wake_listen_prompt,
    get_whisper_initial_prompt,
    get_whisper_language,
    MIC_MAX_SECONDS,
    MIC_MIN_SECONDS,
    MIC_MIN_SPEECH_ENERGY,
    MIC_SILENCE_SECONDS,
    MIC_SILENCE_THRESHOLD,
    MODEL_SIZE,
    USE_VAD_FILTER,
    WAKE_IDLE_TIMEOUT_SECONDS,
    WHISPER_BEAM_SIZE,
    WHISPER_CPU_THREADS,
    WHISPER_LANGUAGE,
)
from core.voice.voice_log import voice_log

SAMPLE_RATE = 16000
DEFAULT_MODEL_SIZE = MODEL_SIZE


def _np():
    import numpy as np
    return np


class VoiceRecognitionError(Exception):
    pass


def _check_mic_backend():
    from core.voice.audio_input import mic_backend_available

    if not mic_backend_available():
        raise VoiceRecognitionError(
            "No microphone found on this PC. Check Windows sound settings "
            "and that a mic is plugged in."
        )


def _check_whisper_backend():
    if not _native_whisper_supported():
        raise VoiceRecognitionError(
            "Voice recognition needs Windows 10 (version 1607) or newer.\n"
            "On Windows 7 use SatpudaCore_Win7.exe, or upgrade Windows."
        )
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError as exc:
        raise VoiceRecognitionError(
            "Voice packages missing. Install with: "
            "pip install faster-whisper sounddevice numpy"
        ) from exc


def _native_whisper_supported() -> bool:
    import sys

    if sys.platform != "win32":
        return True
    ver = sys.getwindowsversion()
    if ver.major < 10:
        return False
    return ver.build >= 14393


class WhisperRecognizer:
    """Record from a selected microphone and transcribe with faster-whisper."""

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL_SIZE,
        device: str = "cpu",
        input_device: int | None = None,
    ):
        _check_mic_backend()
        self.model_size = model_size
        self.device = device
        picked = resolve_device(input_device)
        self.input_device = picked["index"] if picked else None
        self.input_device_backend = (picked or {}).get("backend", "winmm")
        self._model = None
        self._model_lock = threading.Lock()
        self._load_error = None

    def set_input_device(self, device_or_index):
        if isinstance(device_or_index, dict):
            picked = device_or_index
        else:
            picked = resolve_device(device_or_index, getattr(self, "input_device_backend", None))
        if not picked:
            picked = resolve_device(device_or_index)
        self.input_device = picked["index"] if picked else None
        self.input_device_backend = (picked or {}).get("backend", "winmm")
        if picked:
            save_device(picked)

    @property
    def is_ready(self) -> bool:
        return self._model is not None

    @property
    def load_error(self):
        return self._load_error

    def ensure_model(self, on_progress=None):
        if self._model is not None:
            return True
        with self._model_lock:
            if self._model is not None:
                return True
            try:
                msg = "Loading speech model (first time may take a minute)..."
                voice_log(msg)
                if on_progress:
                    on_progress(msg)
                _check_whisper_backend()
                from faster_whisper import WhisperModel
                profile = get_recognition_profile()
                compute_type = profile.get("compute_type", "int8")
                try:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type=compute_type,
                        cpu_threads=WHISPER_CPU_THREADS,
                    )
                except Exception:
                    self._model = WhisperModel(
                        self.model_size,
                        device=self.device,
                        compute_type="int8",
                        cpu_threads=WHISPER_CPU_THREADS,
                    )
                voice_log("Speech model ready")
                if on_progress:
                    on_progress("Speech model ready")
                return True
            except Exception as exc:
                self._load_error = str(exc)
                voice_log(f"Speech model failed: {exc}", level="error")
                return False

    def warmup(self) -> bool:
        """One cheap decode so the first real command is not cold-start slow."""
        if not self.ensure_model():
            return False
        try:
            sample = _np().zeros(int(SAMPLE_RATE * 0.15), dtype=_np().float32)
            prompt = get_whisper_initial_prompt()
            self._transcribe_lang(sample, get_whisper_language(), prompt)
            voice_log("Speech model warmed up")
            return True
        except Exception as exc:
            voice_log(f"Speech warmup skipped: {exc}")
            return False

    def _amplify_audio(self, audio):
        np = _np()
        """Boost quiet speech so Whisper hears soft / fast phrases."""
        if len(audio) == 0:
            return audio
        peak = float(np.max(np.abs(audio)))
        if peak < 1e-6:
            return audio
        target = AUDIO_GAIN_TARGET
        if peak < target:
            gain = min(target / peak, 10.0)
            audio = np.clip(audio * gain, -1.0, 1.0)
        # Light RMS normalize — helps laptop mics that clip quietly.
        rms = float(np.sqrt(np.mean(audio ** 2)))
        if 0 < rms < 0.045:
            audio = np.clip(audio * min(0.055 / rms, 2.5), -1.0, 1.0)
        return audio

    def record_phrase(
        self,
        max_seconds: float = MIC_MAX_SECONDS,
        silence_threshold: float = MIC_SILENCE_THRESHOLD,
        silence_seconds: float = MIC_SILENCE_SECONDS,
        min_seconds: float = MIC_MIN_SECONDS,
        stop_event: threading.Event | None = None,
        on_audio_level=None,
        on_listening_start=None,
        on_listening_stop=None,
        on_partial_audio=None,
        idle_timeout_seconds: float | None = None,
    ) -> object | None:
        from core.voice.audio_input import record_phrase as _record_phrase

        np = _np()

        stop_event = stop_event or threading.Event()
        device = self.input_device
        if on_listening_start:
            on_listening_start(device)

        voice_log(f"LISTENING on mic #{device} — speak now")

        try:
            audio = _record_phrase(
                device_index=device,
                backend=getattr(self, "input_device_backend", "winmm"),
                sample_rate=SAMPLE_RATE,
                chunk_seconds=AUDIO_CHUNK_SECONDS,
                max_seconds=max_seconds,
                silence_threshold=silence_threshold,
                silence_seconds=silence_seconds,
                min_seconds=min_seconds,
                stop_event=stop_event,
                on_audio_level=on_audio_level,
                idle_timeout_seconds=idle_timeout_seconds,
            )
        finally:
            if callable(on_audio_level):
                on_audio_level(0.0)
            if on_listening_stop:
                on_listening_stop()
            voice_log("Stopped listening — processing audio")

        if audio is None:
            voice_log("No audio captured")
            return None
        peak = float(np.max(np.abs(audio)))
        if peak < MIC_MIN_SPEECH_ENERGY:
            voice_log(f"Audio too quiet (peak={peak:.4f}) — ignored")
            return None
        return self._amplify_audio(audio)

    def _transcribe_lang(
        self,
        audio,
        language: str,
        initial_prompt: str,
        *,
        beam_size: int | None = None,
    ) -> tuple[str, float | None]:
        profile = get_recognition_profile()
        prompt_limit = int(profile.get("prompt_max_chars", 220))
        duration_sec = len(audio) / float(SAMPLE_RATE)
        use_vad = bool(profile.get("use_vad", USE_VAD_FILTER)) and duration_sec > 2.5
        decode_beam = beam_size if beam_size is not None else WHISPER_BEAM_SIZE
        segments, _info = self._model.transcribe(
            audio,
            language=language,
            task="transcribe",
            vad_filter=use_vad,
            vad_parameters={
                "min_silence_duration_ms": 120,
                "speech_pad_ms": 60,
                "threshold": 0.35,
            },
            beam_size=decode_beam,
            best_of=min(2, max(1, decode_beam)),
            temperature=0.0,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=initial_prompt[:prompt_limit] if initial_prompt else None,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.05,
            no_speech_threshold=0.48,
        )
        parts = []
        logprobs = []
        for seg in segments:
            if seg.text and seg.text.strip():
                parts.append(seg.text.strip())
            lp = getattr(seg, "avg_logprob", None)
            if lp is not None:
                logprobs.append(float(lp))
        text = " ".join(parts).strip()
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else None
        return text, avg_logprob

    def _rerank_prompts(self, primary: str) -> list[str]:
        profile = get_recognition_profile()
        if not profile.get("rerank_prompts"):
            return [primary]
        wake = get_wake_listen_prompt()
        command = get_command_listen_prompt()
        alt = f"{wake} {command}".strip()
        max_passes = max(1, int(profile.get("rerank_max_passes", 2)))
        prompts = []
        for item in (primary, alt, wake):
            p = (item or "").strip()
            if p and p not in prompts:
                prompts.append(p)
        return prompts[:max_passes]

    def _accept_logprob(self, avg_logprob: float | None) -> bool:
        profile = get_recognition_profile()
        min_logprob = profile.get("min_avg_logprob")
        if min_logprob is None or avg_logprob is None:
            return True
        return avg_logprob >= min_logprob

    def _pick_best_transcript(self, candidates: list[tuple[str, float | None]]) -> str:
        from core.voice.command_parser import has_wake_word, score_transcript_candidate

        profile = get_recognition_profile()
        replace_margin = int(profile.get("rerank_replace_margin", 3500))
        scored: list[tuple[int, str, float | None]] = []
        for raw, avg_logprob in candidates:
            if not raw:
                continue
            if not self._accept_logprob(avg_logprob):
                voice_log(f'Low-confidence transcript skipped ({avg_logprob:.2f}): "{raw[:60]}"')
                continue
            score = score_transcript_candidate(raw, for_rerank=True)
            scored.append((score, raw, avg_logprob))

        if not scored:
            return ""

        any_wake = any(has_wake_word(raw) for _, raw, _ in scored)
        if not any_wake:
            first_raw, first_lp = candidates[0]
            if first_raw and self._accept_logprob(first_lp):
                voice_log(f'No wake in candidates — kept first pass: "{first_raw[:60]}"')
                return first_raw
            return ""

        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best_text, _best_lp = scored[0]
        first_raw, first_lp = candidates[0]
        if len(candidates) > 1 and first_raw and best_text != first_raw:
            first_score = (
                score_transcript_candidate(first_raw, for_rerank=True)
                if self._accept_logprob(first_lp)
                else 0
            )
            if has_wake_word(first_raw) and not has_wake_word(best_text):
                voice_log(f"Rerank kept wake first pass: \"{first_raw[:60]}\"")
                return first_raw
            if best_score - first_score < replace_margin:
                if first_raw and self._accept_logprob(first_lp):
                    voice_log(
                        f"Rerank kept first pass ({first_score} vs {best_score}): "
                        f"\"{first_raw[:60]}\""
                    )
                    return first_raw
        return best_text

    def transcribe(
        self,
        audio,
        language: str | None = None,
        whisper_prompt: str | None = None,
        on_transcribed=None,
    ) -> str:
        if audio is None or len(audio) == 0:
            return ""
        if not self.ensure_model():
            raise VoiceRecognitionError(self._load_error or "Speech model not loaded")

        from core.voice.text_normalize import is_garbage_transcript, normalize_transcript

        lang = language or get_whisper_language()
        prompt = whisper_prompt or get_whisper_initial_prompt()
        prompts = self._rerank_prompts(prompt)
        profile = get_recognition_profile()
        raw = ""
        if len(prompts) == 1:
            raw, avg_logprob = self._transcribe_lang(audio, lang, prompts[0])
            if raw and not self._accept_logprob(avg_logprob):
                voice_log(
                    f'Low-confidence transcript rejected ({avg_logprob:.2f}): "{raw[:60]}"'
                )
                raw = ""
        else:
            from core.voice.command_parser import score_transcript_candidate

            first_raw, first_logprob = self._transcribe_lang(audio, lang, prompts[0])
            first_score = (
                score_transcript_candidate(first_raw, for_rerank=True)
                if first_raw
                else 0
            )
            skip_score = int(profile.get("skip_rerank_score", 9000))
            skip_rerank = bool(
                first_raw
                and self._accept_logprob(first_logprob)
                and first_score >= skip_score
            )
            if skip_rerank and self._accept_logprob(first_logprob):
                raw = first_raw
                lp = f"{first_logprob:.2f}" if first_logprob is not None else "n/a"
                voice_log(f"Fast decode — skipped rerank (score={first_score}, logprob={lp})")
            else:
                if skip_rerank:
                    lp = f"{first_logprob:.2f}" if first_logprob is not None else "n/a"
                    voice_log(
                        f"Fast decode rejected — low confidence ({lp}), reranking: "
                        f'"{first_raw[:60]}"'
                    )
                else:
                    voice_log(f"Transcribing ({lang}, {self.model_size}, {len(prompts)} passes)...")
                candidates = []
                if first_raw:
                    candidates.append((first_raw, first_logprob))
                for idx, listen_prompt in enumerate(prompts[1:], start=2):
                    candidate_raw, avg_logprob = self._transcribe_lang(audio, lang, listen_prompt)
                    if candidate_raw:
                        candidates.append((candidate_raw, avg_logprob))
                        voice_log(f'Transcript candidate {idx}: "{candidate_raw}"' + (
                            f" (logprob={avg_logprob:.2f})" if avg_logprob is not None else ""
                        ))
                raw = self._pick_best_transcript(candidates)
        if not raw:
            voice_log("No speech recognized")
            if callable(on_transcribed):
                on_transcribed("", "")
            return ""
        if is_garbage_transcript(raw):
            voice_log(f'Rejected garbage transcript: "{raw[:80]}"')
            if callable(on_transcribed):
                on_transcribed(raw, "")
            return ""

        text = normalize_transcript(raw) or raw
        if text != raw:
            voice_log(f'Heard: "{raw}" -> "{text}"')
        else:
            voice_log(f'Heard: "{text}"')
        if callable(on_transcribed):
            on_transcribed(raw, text)
        return text

    def listen_once(
        self,
        stop_event: threading.Event | None = None,
        on_progress=None,
        on_audio_level=None,
        on_listening_start=None,
        on_listening_stop=None,
        on_processing=None,
        max_seconds: float | None = None,
        silence_seconds: float | None = None,
        idle_timeout_seconds: float | None = None,
        on_transcribed=None,
        whisper_prompt: str | None = None,
    ) -> str:
        if not self.ensure_model(on_progress=on_progress):
            raise VoiceRecognitionError(self._load_error or "Speech model not loaded")

        audio = self.record_phrase(
            stop_event=stop_event,
            on_audio_level=on_audio_level,
            on_listening_start=on_listening_start,
            on_listening_stop=on_listening_stop,
            max_seconds=max_seconds or MIC_MAX_SECONDS,
            silence_seconds=silence_seconds or MIC_SILENCE_SECONDS,
            idle_timeout_seconds=idle_timeout_seconds,
        )
        if audio is None:
            return ""

        if on_processing:
            on_processing()

        return self.transcribe(
            audio,
            whisper_prompt=whisper_prompt,
            on_transcribed=on_transcribed,
        )

    def _resolve_preview_device(self, device=None):
        if isinstance(device, dict):
            return device
        if device is not None:
            return resolve_device(device)
        return resolve_device(self.input_device, getattr(self, "input_device_backend", None))

    def preview_levels(
        self,
        seconds: float = 3.0,
        stop_event: threading.Event | None = None,
        on_audio_level=None,
        device=None,
    ) -> float:
        """Live mic level preview — returns peak level 0..1."""
        from core.voice.audio_input import preview_levels

        stop_event = stop_event or threading.Event()
        dev = self._resolve_preview_device(device)
        if not dev:
            raise VoiceRecognitionError("No microphone selected.")
        return preview_levels(
            device_index=dev["index"],
            backend=dev.get("backend", "winmm"),
            sample_rate=SAMPLE_RATE,
            seconds=seconds,
            stop_event=stop_event,
            on_audio_level=on_audio_level,
        )

    def test_voice(
        self,
        max_seconds: float = 5.0,
        stop_event: threading.Event | None = None,
        on_audio_level=None,
        on_progress=None,
        device=None,
    ) -> str:
        """Record once and transcribe — for mic testing in settings dialog."""
        prev_index = self.input_device
        prev_backend = getattr(self, "input_device_backend", "winmm")
        if device is not None:
            self.set_input_device(device)
        try:
            if not self.ensure_model(on_progress=on_progress):
                raise VoiceRecognitionError(self._load_error or "Speech model not loaded")
            voice_log("Voice test — speak a short phrase")
            audio = self.record_phrase(
                max_seconds=max_seconds,
                stop_event=stop_event,
                on_audio_level=on_audio_level,
            )
            if audio is None:
                return ""
            return self.transcribe(audio)
        finally:
            self.input_device = prev_index
            self.input_device_backend = prev_backend
