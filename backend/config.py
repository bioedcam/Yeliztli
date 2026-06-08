"""Yeliztli configuration via Pydantic Settings.

Layered: defaults -> ~/.yeliztli/config.toml ([yeliztli] table) -> environment
variables (YELIZTLI_*, with a one-release deprecated GENOMEINSIGHT_* fallback).
"""

import logging
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from pydantic_settings.sources import EnvSettingsSource

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


DEFAULT_DATA_DIR = Path.home() / ".yeliztli"
# One-release back-compat: the pre-rebrand data dir, auto-migrated on first boot.
LEGACY_DATA_DIR = Path.home() / ".genomeinsight"

# config.toml table key (was "genomeinsight" before the rebrand).
CONFIG_SECTION = "yeliztli"
LEGACY_CONFIG_SECTION = "genomeinsight"

# Env-var prefixes. YELIZTLI_ is canonical; GENOMEINSIGHT_ is read for one
# release as a deprecated fallback (see settings_customise_sources).
ENV_PREFIX = "YELIZTLI_"
LEGACY_ENV_PREFIX = "GENOMEINSIGHT_"

# Fields never sourced from config.toml: data_dir is location-defining (it says
# *where* config.toml lives), so reading it back from config.toml is circular and
# would re-introduce a stale absolute path after the data-dir rename. It is
# resolved from the default or an explicit env/init override only — which also
# matches the pre-rebrand behaviour (the old top-level TomlConfigSettingsSource
# never saw the wizard's [genomeinsight].data_dir either).
_TOML_EXCLUDED_FIELDS = frozenset({"data_dir"})


class _ConfigTomlTableSource(PydanticBaseSettingsSource):
    """Load settings from the ``[yeliztli]`` table of config.toml.

    pydantic-settings' built-in ``TomlConfigSettingsSource`` reads only
    *top-level* TOML keys, but everything the setup wizard persists lives under a
    named table (``[yeliztli]``, formerly ``[genomeinsight]``), so the built-in
    source silently ignored all of it — wizard-saved auth/theme never reached the
    runtime ``Settings`` (the Q13 latent bug). This source descends into that
    table, with a one-release fallback to the legacy ``[genomeinsight]`` table.
    """

    def __init__(self, settings_cls: type[BaseSettings], toml_path: Path) -> None:
        super().__init__(settings_cls)
        self._table: dict[str, Any] = {}
        if tomllib is not None and toml_path.exists():
            try:
                data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError):
                data = {}
            table = data.get(CONFIG_SECTION)
            if not isinstance(table, dict):
                table = data.get(LEGACY_CONFIG_SECTION)
            if isinstance(table, dict):
                self._table = table

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # noqa: ARG002
        return self._table.get(field_name), field_name, False

    def prepare_field_value(
        self, field_name: str, field: Any, value: Any, value_is_complex: bool
    ) -> Any:  # noqa: ARG002
        return value

    def __call__(self) -> dict[str, Any]:
        return {
            name: self._table[name]
            for name in self.settings_cls.model_fields
            if name in self._table and name not in _TOML_EXCLUDED_FIELDS
        }


class Settings(BaseSettings):
    """Application settings with layered config resolution."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        extra="ignore",
    )

    # --- Paths ---
    data_dir: Path = Field(
        default=DEFAULT_DATA_DIR,
        description="Root directory for all Yeliztli data (DBs, samples, logs).",
    )

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False

    # --- Database ---
    wal_mode: bool = Field(default=True, description="Enable WAL mode on all SQLite DBs.")

    # --- Authentication (optional) ---
    auth_enabled: bool = False
    auth_password_hash: str = Field(default="", description="bcrypt hash of PIN/password.")
    session_timeout_hours: int = 4

    # --- External services ---
    pubmed_email: str = Field(default="", description="Email for NCBI Entrez (required by TOS).")
    pubmed_api_key: str = Field(
        default="", description="Optional NCBI API key for higher rate limits."
    )
    omim_api_key: str = Field(default="", description="Optional OMIM API key for enrichment.")

    # --- Update manager ---
    update_check_interval: Literal["startup", "daily", "weekly"] = "daily"
    update_download_window: str | None = Field(
        default=None,
        description='Optional time window for large downloads, e.g. "02:00-06:00".',
    )

    # --- LAI (Local Ancestry Inference) ---
    lai_bundle_path: Path | None = Field(
        default=None,
        description="Path to LAI bundle directory. Defaults to data_dir / 'lai_bundle'.",
    )
    lai_java_mem: str = Field(
        default="4g",
        description="JVM memory allocation for Beagle phasing (e.g. '4g').",
    )

    # --- UI preferences ---
    theme: Literal["light", "dark", "system"] = "system"

    # --- Logging ---
    log_level: str = "INFO"
    log_dir: Path | None = None  # Defaults to data_dir / "logs" at runtime

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def resolved_log_dir(self) -> Path:
        return self.log_dir or (self.data_dir / "logs")

    @property
    def reference_db_path(self) -> Path:
        return self.data_dir / "reference.db"

    @property
    def vep_bundle_db_path(self) -> Path:
        return self.data_dir / "vep_bundle.db"

    @property
    def gnomad_db_path(self) -> Path:
        return self.data_dir / "gnomad_af.db"

    @property
    def dbnsfp_db_path(self) -> Path:
        return self.data_dir / "dbnsfp.db"

    @property
    def encode_ccres_db_path(self) -> Path:
        return self.data_dir / "encode_ccres.db"

    @property
    def resolved_lai_bundle_path(self) -> Path:
        return self.lai_bundle_path or (self.data_dir / "lai_bundle")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type["BaseSettings"],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Precedence: init > YELIZTLI_ env > GENOMEINSIGHT_ env > [yeliztli] TOML > dotenv."""
        # Deprecated one-release fallback: read GENOMEINSIGHT_* env vars at lower
        # precedence than the canonical YELIZTLI_* (env_settings).
        legacy_env = EnvSettingsSource(settings_cls, env_prefix=LEGACY_ENV_PREFIX)
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            legacy_env,
        ]
        sources.append(_ConfigTomlTableSource(settings_cls, DEFAULT_DATA_DIR / "config.toml"))
        sources.append(dotenv_settings)
        return tuple(sources)


def migrate_legacy_data_dir() -> None:
    """First-boot, one-release migration: rename ``~/.genomeinsight`` → ``~/.yeliztli``.

    Atomic same-filesystem rename (no multi-GB copy); falls back to ``shutil.move``
    on EXDEV. **Best-effort — never raises** (a failed migration must not crash
    startup; the user can ``mv`` manually).

    Heavily guarded so it can never touch the developer's real home directory
    during tests or when an explicit data dir is configured:
      * skipped entirely under pytest (``PYTEST_CURRENT_TEST``);
      * skipped when an explicit ``YELIZTLI_DATA_DIR`` / ``GENOMEINSIGHT_DATA_DIR``
        override is set;
      * no-op once ``~/.yeliztli`` exists (idempotent); if BOTH exist, the new
        dir wins and the legacy dir is left untouched (never auto-merged).

    Called only from explicit production entry points (FastAPI lifespan,
    installer) — never from ``get_settings()`` (which is imported eagerly by
    background workers).
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    if os.environ.get(f"{ENV_PREFIX}DATA_DIR") or os.environ.get(f"{LEGACY_ENV_PREFIX}DATA_DIR"):
        return

    target, legacy = DEFAULT_DATA_DIR, LEGACY_DATA_DIR
    try:
        if target.exists():
            if legacy.exists():
                logger.warning(
                    "Both %s and %s exist; using %s and leaving the legacy dir "
                    "untouched (not auto-merged).",
                    legacy,
                    target,
                    target,
                )
            return
        if not legacy.exists():
            return
        try:
            os.rename(legacy, target)
        except OSError:
            shutil.move(str(legacy), str(target))
        logger.info("Migrated data directory %s -> %s", legacy, target)
    except Exception as exc:  # noqa: BLE001 - migration must never crash startup
        logger.warning("Data-dir migration %s -> %s failed: %s", legacy, target, exc)


def warn_deprecated_env() -> None:
    """Log a one-time deprecation notice if any GENOMEINSIGHT_* env var is set."""
    if any(k.startswith(LEGACY_ENV_PREFIX) for k in os.environ):
        logger.warning(
            "Deprecated %s* environment variables detected; please migrate to %s* "
            "(the legacy prefix will be removed in a future release).",
            LEGACY_ENV_PREFIX,
            ENV_PREFIX,
        )


def read_config_section(content: dict[str, Any]) -> dict[str, Any]:
    """Return a mutable copy of the persisted config table from a parsed config.toml.

    Reads the canonical ``[yeliztli]`` table, with a one-release fallback to the
    legacy ``[genomeinsight]`` table so a pre-rebrand config.toml keeps working.
    Returns ``{}`` when neither table is present. The result is a shallow copy so
    callers can mutate it and persist it via :func:`write_config_section`.
    """
    section = content.get(CONFIG_SECTION)
    if not isinstance(section, dict):
        section = content.get(LEGACY_CONFIG_SECTION)
    return dict(section) if isinstance(section, dict) else {}


def write_config_section(content: dict[str, Any], section: dict[str, Any]) -> None:
    """Store ``section`` under the canonical ``[yeliztli]`` key in ``content``.

    Any legacy ``[genomeinsight]`` table is dropped so the rebranded section fully
    supersedes it (otherwise both would persist and the legacy one would linger).
    """
    content[CONFIG_SECTION] = section
    content.pop(LEGACY_CONFIG_SECTION, None)


@lru_cache
def get_settings() -> Settings:
    """Create and return application settings instance."""
    return Settings()
