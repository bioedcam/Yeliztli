"""GenomeInsight configuration via Pydantic Settings.

Layered: defaults -> ~/.genomeinsight/config.toml -> environment variables.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

try:
    from pydantic_settings import TomlConfigSettingsSource
except ImportError:
    TomlConfigSettingsSource = None  # type: ignore[assignment,misc]


DEFAULT_DATA_DIR = Path.home() / ".genomeinsight"


class Settings(BaseSettings):
    """Application settings with layered config resolution."""

    model_config = SettingsConfigDict(
        env_prefix="GENOMEINSIGHT_",
        env_file=".env",
        extra="ignore",
    )

    # --- Paths ---
    data_dir: Path = Field(
        default=DEFAULT_DATA_DIR,
        description="Root directory for all GenomeInsight data (DBs, samples, logs).",
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
        """Layer: init > env > TOML file > dotenv > defaults."""
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
        ]
        if TomlConfigSettingsSource is not None:
            toml_path = DEFAULT_DATA_DIR / "config.toml"
            if toml_path.exists():
                sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_path))
        sources.append(dotenv_settings)
        return tuple(sources)


@lru_cache
def get_settings() -> Settings:
    """Create and return application settings instance."""
    return Settings()
