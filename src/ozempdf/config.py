"""OzemPDF domain configuration.

Single source of truth for every knob that changes the compression pipeline.
All surfaces (web UI, CLI, HTTP API) build a :class:`CompressionConfig` here so
that ranges and preset defaults can never drift between them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from enum import Enum
from pathlib import Path
from typing import Any

# Public knob ranges. The web UI, the HTTP API and the CLI all clamp to these.
DPI_MIN, DPI_MAX = 72, 600
JPEG_QUALITY_MIN, JPEG_QUALITY_MAX = 50, 98
ZOPFLI_MIN, ZOPFLI_MAX = 5, 50
PALETTE_MIN, PALETTE_MAX = 64, 256
DOWNSAMPLE_FACTOR = 1.45


class PresetMode(str, Enum):
    """Named compression presets plus the state produced by editing a knob."""

    LOSSLESS = "lossless"
    BALANCED = "balanced"
    MAX_SLIM = "max"
    CUSTOM = "custom"


class OutputMode(str, Enum):
    """Where slimmed files are written."""

    SUFFIX = "suffix"
    CUSTOM_FOLDER = "folder"
    OVERWRITE = "overwrite"


def clamp(value: int, low: int, high: int) -> int:
    """Clamp an integer into an inclusive range."""
    return max(low, min(high, int(value)))


@dataclass(slots=True)
class CompressionConfig:
    """Every knob that affects one compression run.

    Defaults equal the *Visually Lossless* (balanced) preset.
    """

    preset: PresetMode = PresetMode.BALANCED
    strict_lossless: bool = False
    enable_downsampling: bool = True
    target_dpi: int = 150
    downsample_threshold_dpi: int = 218
    jpeg_quality: int = 85
    quantize_ui_screenshots: bool = True
    ui_palette_colors: int = 256
    zopfli_iterations: int = 15
    strip_opaque_smask: bool = True
    deduplicate_resources: bool = True
    use_object_streams: bool = True
    subset_fonts: bool = True
    strip_bloat: bool = True
    unlock_and_flatten_signatures: bool = False
    strip_metadata: bool = False
    output_mode: OutputMode = OutputMode.SUFFIX
    output_suffix: str = "_compressed"
    output_dir: str = ""
    max_workers: int = 0
    password: str = ""

    # Re-signing after compression. The password is never persisted or reported.
    sign_after_slimming: bool = False
    sign_pkcs12_path: str = ""
    sign_pkcs12_password: str = ""
    sign_field_name: str = "OzemPDF Signature"
    sign_reason: str = "Slimmed with OzemPDF"
    sign_location: str = ""
    sign_tsa_url: str = ""

    @classmethod
    def from_preset(cls, preset: str | PresetMode) -> CompressionConfig:
        """Return the full configuration for a named preset.

        ``custom`` has no stored definition; it resolves to the balanced branch
        and is only ever produced by editing a knob at runtime.
        """
        try:
            mode = PresetMode(str(preset))
        except ValueError:
            mode = PresetMode.BALANCED
        if mode is PresetMode.LOSSLESS:
            return cls(
                preset=mode,
                strict_lossless=True,
                enable_downsampling=False,
                target_dpi=600,
                downsample_threshold_dpi=900,
                jpeg_quality=98,
                quantize_ui_screenshots=False,
                ui_palette_colors=256,
                zopfli_iterations=15,
                strip_metadata=False,
            )
        if mode is PresetMode.MAX_SLIM:
            return cls(
                preset=mode,
                strict_lossless=False,
                enable_downsampling=True,
                target_dpi=110,
                downsample_threshold_dpi=160,
                jpeg_quality=75,
                quantize_ui_screenshots=True,
                ui_palette_colors=128,
                zopfli_iterations=25,
                strip_metadata=True,
                unlock_and_flatten_signatures=False,
            )
        return cls(preset=PresetMode.BALANCED if mode is PresetMode.BALANCED else PresetMode.CUSTOM)

    def copy_with(self, **overrides: Any) -> CompressionConfig:
        """Return a clamped copy with overrides applied."""
        known = {f.name for f in fields(self)}
        clean = {k: v for k, v in overrides.items() if k in known}
        return replace(self, **clean).clamped()

    def clamped(self) -> CompressionConfig:
        """Clamp every numeric knob into its public range and fix derived fields."""
        cfg = replace(self)
        cfg.target_dpi = clamp(cfg.target_dpi, DPI_MIN, DPI_MAX)
        if cfg.strict_lossless:
            # Strict Lossless is a mode, not a slider position: a real flag now
            # backs the old "slider >= 300 means no downsampling" display hack (D-03).
            cfg.enable_downsampling = False
        cfg.downsample_threshold_dpi = max(
            cfg.target_dpi + 1, round(cfg.target_dpi * DOWNSAMPLE_FACTOR)
        )
        cfg.jpeg_quality = clamp(cfg.jpeg_quality, JPEG_QUALITY_MIN, JPEG_QUALITY_MAX)
        cfg.zopfli_iterations = clamp(cfg.zopfli_iterations, ZOPFLI_MIN, ZOPFLI_MAX)
        cfg.ui_palette_colors = clamp(cfg.ui_palette_colors, PALETTE_MIN, PALETTE_MAX)
        cfg.max_workers = max(0, int(cfg.max_workers or 0))
        suffix = (cfg.output_suffix or "").strip()
        cfg.output_suffix = suffix or "_compressed"
        if not cfg.sign_pkcs12_path:
            cfg.sign_after_slimming = False
        return cfg

    def to_dict(self, *, public: bool = False) -> dict[str, Any]:
        """Serialise the config. ``public`` drops secrets and report-only fields."""
        data = asdict(self)
        data["preset"] = self.preset.value
        data["output_mode"] = self.output_mode.value
        if public:
            for secret in ("sign_pkcs12_password", "password"):
                data.pop(secret, None)
            data.pop("sign_pkcs12_path", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, base: CompressionConfig | None = None) -> CompressionConfig:
        """Rebuild a config from stored settings, ignoring unusable values."""
        cfg = base.copy_with() if base else cls.from_preset(data.get("preset", "balanced"))
        known = {f.name for f in fields(cls)}
        for key, value in data.items():
            if key not in known or value is None:
                continue
            if key == "preset":
                try:
                    cfg.preset = PresetMode(str(value))
                except ValueError:
                    continue
            elif key == "output_mode":
                try:
                    cfg.output_mode = OutputMode(str(value))
                except ValueError:
                    continue
            elif key in {"strict_lossless", "enable_downsampling", "quantize_ui_screenshots",
                         "strip_opaque_smask", "deduplicate_resources", "use_object_streams",
                         "subset_fonts", "strip_bloat", "unlock_and_flatten_signatures",
                         "strip_metadata", "sign_after_slimming"}:
                setattr(cfg, key, bool(value))
            elif key in {"target_dpi", "jpeg_quality", "zopfli_iterations", "ui_palette_colors",
                         "max_workers", "downsample_threshold_dpi"}:
                try:
                    setattr(cfg, key, int(value))
                except (TypeError, ValueError):
                    continue
            elif isinstance(value, str):
                setattr(cfg, key, value)
        return cfg.clamped()

    @property
    def output_path(self) -> Path | None:
        """Configured custom output directory, if any."""
        return Path(self.output_dir) if self.output_dir else None
