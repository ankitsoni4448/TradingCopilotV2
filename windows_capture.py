"""Privacy-bounded Windows desktop capture for the calibrated Binomo layout."""
from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageGrab


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.left < 0 or self.top < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("capture_region_dimensions_must_be_positive")
        if self.width * self.height > 4_000_000:
            raise ValueError("capture_region_exceeds_privacy_pixel_limit")

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.left + self.width,
                self.top + self.height)


def _display_size() -> tuple[int, int] | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


class BoundedDesktopProvider:
    """Capture only a configured rectangle that fits the current display."""

    def __init__(self, region: CaptureRegion, *,
                 expected_size: tuple[int, int] | None = None,
                 display_size: tuple[int, int] | None = None,
                 grabber: Callable[..., Image.Image] = ImageGrab.grab):
        expected_size = expected_size or (region.width, region.height)
        if expected_size[0] <= 0 or expected_size[1] <= 0:
            raise ValueError("expected_size_must_be_positive")
        if (region.width, region.height) != expected_size:
            raise ValueError("display_size_mismatch")
        display_size = display_size or _display_size()
        if display_size is not None and (
                region.left + region.width > display_size[0] or
                region.top + region.height > display_size[1]):
            raise ValueError("display_size_mismatch")
        self.region = region
        self.expected_size = expected_size
        self.grabber = grabber

    def __call__(self) -> Image.Image:
        image = self.grabber(bbox=self.region.bbox, all_screens=True)
        if not isinstance(image, Image.Image):
            raise ValueError("desktop_grabber_did_not_return_image")
        if image.size != self.expected_size:
            raise ValueError("display_size_mismatch")
        return image.convert("RGB").copy()
