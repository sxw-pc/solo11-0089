"""Runtime configuration with fixed, explicit time handling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_NOW = "2026-01-01T00:00:00Z"
DEFAULT_IGNORES = ("out", ".git", ".pytest_cache", "__pycache__")


def parse_now(value: str) -> str:
    """Validate an ISO timestamp and return a stable UTC representation."""
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid --now timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RunConfig:
    input_dir: Path
    contract_path: Path
    out_dir: Path
    db_path: Path
    now: str = DEFAULT_NOW
    ignores: tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORES)

    @classmethod
    def create(
        cls,
        input_dir: str | Path,
        contract_path: str | Path,
        out_dir: str | Path,
        now: str = DEFAULT_NOW,
        db_path: str | Path | None = None,
        ignores: tuple[str, ...] = DEFAULT_IGNORES,
    ) -> "RunConfig":
        out = Path(out_dir)
        return cls(
            input_dir=Path(input_dir),
            contract_path=Path(contract_path),
            out_dir=out,
            db_path=Path(db_path) if db_path else out / "ledger.sqlite3",
            now=parse_now(now),
            ignores=tuple(dict.fromkeys(ignores)),
        )

    def with_out_dir(self, out_dir: str | Path) -> "RunConfig":
        out = Path(out_dir)
        return RunConfig(
            input_dir=self.input_dir,
            contract_path=self.contract_path,
            out_dir=out,
            db_path=out / "ledger.sqlite3",
            now=self.now,
            ignores=self.ignores,
        )
