"""Install-time safety helpers for Hermes sidecar integration."""

from .detect import HermesDetection, detect_hermes
from .manifest import file_sha256

__all__ = ["HermesDetection", "detect_hermes", "file_sha256"]
