from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_VISION_MODEL = "gpt-4o-mini"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_GOOGLE_TRANSCRIPTION_MODEL = "gemini-3-flash-preview"


class OpenAIAdapter:
    def __init__(
        self,
        api_key: str,
        *,
        chat_model: str = DEFAULT_CHAT_MODEL,
        vision_model: str = DEFAULT_VISION_MODEL,
        transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")

        self.client = OpenAI(api_key=api_key)
        self.chat_model = chat_model
        self.vision_model = vision_model
        self.transcription_model = transcription_model

    def chat(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model: str | None = None,
    ) -> str:
        input_items: list[dict[str, object]] = []
        if system_prompt:
            input_items.append(
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": system_prompt}],
                }
            )
        input_items.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        )

        response = self.client.responses.create(
            model=model or self.chat_model,
            input=input_items,
        )
        return response.output_text.strip()

    def describe_image(
        self,
        image_path: str | Path,
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        image_path = Path(image_path).expanduser()
        if not image_path.exists():
            raise FileNotFoundError(f"image not found: {image_path}")

        response = self.client.responses.create(
            model=model or self.vision_model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {
                            "type": "input_image",
                            "image_url": self._to_data_url(image_path),
                        },
                    ],
                }
            ],
        )
        return response.output_text.strip()

    def transcribe_audio(
        self,
        audio_path: str | Path,
        *,
        model: str | None = None,
    ) -> str:
        audio_path = Path(audio_path).expanduser()
        if not audio_path.exists():
            raise FileNotFoundError(f"audio not found: {audio_path}")

        with audio_path.open("rb") as audio_file:
            transcript = self.client.audio.transcriptions.create(
                model=model or self.transcription_model,
                file=audio_file,
            )
        return transcript.text.strip()

    @staticmethod
    def _to_data_url(file_path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(file_path.name)
        if mime_type is None:
            mime_type = "application/octet-stream"
        file_base64 = base64.b64encode(file_path.read_bytes()).decode("utf-8")
        return f"data:{mime_type};base64,{file_base64}"


class GoogleAIAdapter:
    def __init__(
        self,
        api_key: str,
        *,
        transcription_model: str = DEFAULT_GOOGLE_TRANSCRIPTION_MODEL,
    ) -> None:
        api_key = api_key.strip()
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required")
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(api_version="v1alpha", timeout=120_000),
        )
        self.transcription_model = transcription_model

    def transcribe_audio(
        self,
        audio_path: str | Path,
        *,
        model: str | None = None,
    ) -> str:
        audio_path = Path(audio_path).expanduser()
        if not audio_path.exists():
            raise FileNotFoundError(f"audio not found: {audio_path}")

        selected_model = model or self.transcription_model
        prompt = "Generate a transcript of the speech."

        try:
            uploaded_file = self.client.files.upload(file=str(audio_path))
            response = self.client.models.generate_content(
                model=selected_model,
                contents=[
                    prompt,
                    types.Part.from_uri(
                        file_uri=uploaded_file.uri,
                        mime_type=uploaded_file.mime_type,
                    ),
                ],
            )
            return (response.text or "").strip()
        except Exception:
            mime_type, _ = mimetypes.guess_type(audio_path.name)
            if mime_type is None:
                mime_type = "audio/wav"
            response = self.client.models.generate_content(
                model=selected_model,
                contents=[
                    prompt,
                    types.Part.from_bytes(
                        data=audio_path.read_bytes(),
                        mime_type=mime_type,
                    ),
                ],
            )
            return (response.text or "").strip()

    def describe_image(
        self,
        image_path: str | Path,
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        image_path = Path(image_path).expanduser()
        if not image_path.exists():
            raise FileNotFoundError(f"image not found: {image_path}")

        uploaded_file = self.client.files.upload(file=str(image_path))
        response = self.client.models.generate_content(
            model=model or self.transcription_model,
            contents=[
                prompt,
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type=uploaded_file.mime_type,
                ),
            ],
        )
        return (response.text or "").strip()


class WechatAIClient:
    """统一封装 OpenAI 能力调用。"""

    def __init__(
        self,
        *,
        openai_adapter: OpenAIAdapter | None = None,
        google_adapter: GoogleAIAdapter | None = None,
    ) -> None:
        if openai_adapter is None and google_adapter is None:
            raise ValueError("at least one AI adapter is required")

        self.openai_adapter = openai_adapter
        self.google_adapter = google_adapter

    @staticmethod
    def _parse_model_spec(model_spec: str | None) -> tuple[str | None, str | None]:
        if not model_spec:
            return None, None

        raw_spec = model_spec.strip()
        if not raw_spec:
            return None, None

        provider, separator, model = raw_spec.partition(":")
        if not separator or not provider.strip() or not model.strip():
            raise ValueError(f"invalid model spec: {model_spec}")
        return provider.strip().upper(), model.strip()

    @classmethod
    def from_env(cls) -> "WechatAIClient":
        load_dotenv(ENV_PATH)
        openai_adapter = None
        google_adapter = None

        if os.getenv("OPENAI_API_KEY", "").strip():
            openai_adapter = OpenAIAdapter(
                api_key=os.getenv("OPENAI_API_KEY", ""),
                chat_model=DEFAULT_CHAT_MODEL,
                vision_model=DEFAULT_VISION_MODEL,
                transcription_model=DEFAULT_TRANSCRIPTION_MODEL,
            )

        if os.getenv("GOOGLE_API_KEY", "").strip():
            google_adapter = GoogleAIAdapter(
                api_key=os.getenv("GOOGLE_API_KEY", ""),
                transcription_model=DEFAULT_GOOGLE_TRANSCRIPTION_MODEL,
            )

        return cls(
            openai_adapter=openai_adapter,
            google_adapter=google_adapter,
        )

    def chat(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        model_spec: str | None = None,
    ) -> str:
        provider, model = self._parse_model_spec(model_spec)
        if provider not in {None, "OPENAI"}:
            raise ValueError(f"unsupported chat provider: {provider}")
        if self.openai_adapter is None:
            raise ValueError("OpenAI adapter is not configured")
        return self.openai_adapter.chat(
            prompt,
            system_prompt=system_prompt,
            model=model,
        )

    def describe_image(
        self,
        image_path: str | Path,
        prompt: str,
        *,
        model_spec: str | None = None,
    ) -> str:
        provider, model = self._parse_model_spec(model_spec)
        if provider in {None, "OPENAI"}:
            if self.openai_adapter is None:
                if provider is None and self.google_adapter is not None:
                    return self.google_adapter.describe_image(
                        image_path,
                        prompt,
                        model=model,
                    )
                raise ValueError("OpenAI adapter is not configured")
            return self.openai_adapter.describe_image(
                image_path,
                prompt,
                model=model,
            )
        if provider in {"GOOGLE", "GEMINI"}:
            if self.google_adapter is None:
                raise ValueError("Google adapter is not configured")
            return self.google_adapter.describe_image(
                image_path,
                prompt,
                model=model,
            )
        raise ValueError(f"unsupported image provider: {provider}")

    def transcribe_audio(
        self,
        audio_path: str | Path,
        *,
        model_spec: str | None = None,
    ) -> str:
        provider, model = self._parse_model_spec(model_spec)
        if provider in {None, "GOOGLE", "GEMINI"}:
            if self.google_adapter is not None:
                return self.google_adapter.transcribe_audio(audio_path, model=model)
            if provider in {"GOOGLE", "GEMINI"}:
                raise ValueError("Google adapter is not configured")
        if provider in {None, "OPENAI"}:
            if self.openai_adapter is not None:
                return self.openai_adapter.transcribe_audio(audio_path, model=model)
            if provider == "OPENAI":
                raise ValueError("OpenAI adapter is not configured")
        raise ValueError("No transcription adapter is configured")
