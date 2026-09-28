from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
VAR_DIR = BACKEND_ROOT / "var"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CUBEGEN_",
        env_file=BACKEND_ROOT / ".env",
        extra="ignore",
    )

    # The app's own database: saved connections, evidence bundles, generated
    # models. Never the database being modelled.
    app_database_url: str = "postgresql://postgres@127.0.0.1:5432/your_app_database"

    # Encrypts stored passwords. Generated on first use.
    secret_key_path: Path = VAR_DIR / "secret.key"

    # Semantic generation.
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    # Retrieval. The local Qdrant is a long-lived service shared with other
    # projects, so this owns one collection rather than writing into theirs.
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "cubegen_view_members"
    # 1536 dimensions, matching the collections already on this Qdrant.
    embedding_model: str = "text-embedding-3-small"

    # Tracing. Self-hosted from docker/langfuse. Everything works without
    # it -- the tracing layer becomes a no-op when the keys are absent.
    # The off switch. Set CUBEGEN_TRACING=false to stop tracing without
    # clearing the credentials -- "configured" and "wanted" are different
    # things, and blanking keys to silence it loses them.
    tracing: bool = True
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "http://localhost:3000"

    # The dev frontend. Tightened for any real deployment.
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Standalone Cube from docker/compose.yaml.
    cube_api_url: str = "http://localhost:4000"
    # Cube's Postgres-wire SQL API, which the agent queries through.
    cube_sql_host: str = "127.0.0.1"
    cube_sql_port: int = 15432
    cube_sql_user: str = "cube"
    cube_sql_password: str = "cube"
    cube_sql_database: str = "cube"
    # Where the renderer writes. Mounted into the Cube container.
    cube_model_dir: Path = BACKEND_ROOT.parent / "docker" / "cube" / "model"

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def tracing_enabled(self) -> bool:
        return self.tracing and bool(
            self.langfuse_public_key.strip() and self.langfuse_secret_key.strip()
        )

    @property
    def qdrant_configured(self) -> bool:
        # Embedding runs through OpenAI, so retrieval needs both.
        return bool(self.qdrant_url.strip()) and self.openai_configured


settings = Settings()
