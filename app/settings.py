from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="DE_", extra="ignore")

    examples_dir: Path = Path(__file__).resolve().parent.parent / "examples"
    host: str = "0.0.0.0"
    port: int = 8000

    def example_files(self) -> list[str]:
        if not self.examples_dir.is_dir():
            return []
        return sorted(
            p.name
            for p in self.examples_dir.iterdir()
            if p.suffix.lower() in {".pdf", ".docx", ".txt"}
        )

    def example_path(self, name: str) -> Path:
        if name not in self.example_files():
            raise HTTPException(404, "Unknown example")
        return self.examples_dir / name


settings = Settings()
