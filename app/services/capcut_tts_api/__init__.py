"""
CapCut TTS API Python SDK & Package.

Pure Python client for CapCut Text-to-Speech (TTS), Speech-to-Text (STT),
and VOD chunked audio upload workflows.
"""

from .client import CapCutClient
from .exceptions import (
    CapCutAPIError,
    CapCutError,
    CapCutSignError,
    CapCutTaskError,
    CapCutUploadError,
)
from .models import (
    DeviceConfig,
    SubtitleResult,
    UploadResult,
    Utterance,
    VoiceInfo,
    Word,
)

__version__ = "1.0.0"

__all__ = [
    "CapCutClient",
    "DeviceConfig",
    "UploadResult",
    "SubtitleResult",
    "Utterance",
    "Word",
    "VoiceInfo",
    "CapCutError",
    "CapCutAPIError",
    "CapCutUploadError",
    "CapCutSignError",
    "CapCutTaskError",
    "__version__",
]
