from __future__ import annotations

import os
import threading
import time
from typing import Callable

from logging_config import get_logger

logger = get_logger("voice_interface")


class VoiceInterface:
    """
    Voice-first loop (wake word + record + transcribe + speak).

    Feature-flagged via VOICE_ENABLED. If dependencies are missing, the interface
    can be instantiated but will not start.
    """

    def __init__(self, *, on_transcript: Callable[[str], str], wake_word: str = "jarvis"):
        self._on_transcript = on_transcript
        self._wake_word = wake_word
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if self._running:
            return True
        if os.getenv("VOICE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            logger.info("VOICE_ENABLED is false; voice interface not starting")
            return False
        try:
            import pvporcupine  # type: ignore
            import pyaudio  # type: ignore
        except Exception as exc:
            logger.warning(f"Voice dependencies missing; voice interface disabled: {exc}")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Voice interface started")
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Voice interface stopped")

    def _listen_loop(self) -> None:
        try:
            import pvporcupine  # type: ignore
            import pyaudio  # type: ignore
        except Exception:
            return

        access_key = os.getenv("PORCUPINE_ACCESS_KEY", "").strip()
        keyword = (os.getenv("WAKE_WORD", self._wake_word) or self._wake_word).strip()
        if not access_key:
            logger.warning("PORCUPINE_ACCESS_KEY not set; voice interface cannot start")
            return

        porcupine = None
        pa = None
        stream = None
        try:
            porcupine = pvporcupine.create(access_key=access_key, keywords=[keyword])
            pa = pyaudio.PyAudio()
            stream = pa.open(
                rate=porcupine.sample_rate,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=porcupine.frame_length,
            )

            logger.info(f"Listening for wake word: {keyword}")
            while self._running:
                pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
                import struct

                frame = struct.unpack_from("h" * porcupine.frame_length, pcm)
                result = porcupine.process(frame)
                if result >= 0:
                    # Wake word detected: record a short chunk, transcribe, respond.
                    logger.info("Wake word detected")
                    user_text = self._record_once(pa, seconds=6)
                    if not user_text:
                        continue
                    try:
                        from tools import transcribe_audio_file, generate_tts_file

                        transcript = transcribe_audio_file(user_text)
                        if not transcript:
                            continue
                        reply = self._on_transcript(transcript)
                        # Optional: generate a tts file (playback is environment-specific).
                        _ = generate_tts_file(reply, "voice_reply.mp3")
                        logger.info("Voice turn completed")
                    except Exception as exc:
                        logger.warning(f"Voice turn failed: {exc}")
                time.sleep(0.01)
        except Exception as exc:
            logger.warning(f"Voice loop stopped: {exc}")
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except Exception:
                pass
            try:
                if pa:
                    pa.terminate()
            except Exception:
                pass
            try:
                if porcupine:
                    porcupine.delete()
            except Exception:
                pass

    def _record_once(self, pa, *, seconds: int = 6) -> str:
        """
        Records raw mic audio to a temporary wav file and returns its path.
        This is a simple implementation; silence-based stop can be added later.
        """
        try:
            import wave
            import pyaudio  # type: ignore
        except Exception:
            return ""

        rate = 16000
        frames = []
        stream = pa.open(rate=rate, channels=1, format=pyaudio.paInt16, input=True, frames_per_buffer=1024)
        try:
            for _ in range(int(rate / 1024 * max(1, seconds))):
                frames.append(stream.read(1024, exception_on_overflow=False))
        finally:
            stream.stop_stream()
            stream.close()

        out_path = os.path.join(os.getenv("TEMP", "."), f"victor_voice_{int(time.time())}.wav")
        with wave.open(out_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(pa.get_sample_size(pyaudio.paInt16))
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))
        return out_path

