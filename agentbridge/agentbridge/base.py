"""Abstract base adapter for provider CLIs."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from .events import BridgeEvent
from .subprocess_runner import SubprocessRunner
from .types import ProviderType, SessionConfig


class BaseAdapter(ABC):
    """Translate a provider CLI's JSONL output into unified BridgeEvents."""

    provider: ProviderType

    @abstractmethod
    def build_command(self, config: SessionConfig) -> list[str]:
        """Build the CLI command + args."""
        ...

    @abstractmethod
    def build_env(self, config: SessionConfig) -> dict[str, str]:
        """Build extra environment variables for the subprocess."""
        ...

    @abstractmethod
    async def stream(
        self,
        runner: SubprocessRunner,
        config: SessionConfig,
    ) -> AsyncGenerator[BridgeEvent, None]:
        """Read raw JSONL from the runner, yield unified BridgeEvents."""
        ...
        yield  # pragma: no cover — make this a generator

    async def send_message(self, runner: SubprocessRunner, message: str) -> None:
        """Send a follow-up message (bidirectional protocols only)."""
        raise NotImplementedError(
            f"{self.provider.value} adapter does not support bidirectional input"
        )
