from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from codeknow_cli.config import UserConfig


def _fresh_default(*_args: object, **_kwargs: object) -> UserConfig:
    return UserConfig()


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in (
        "codeknow_cli.endpoint.load_config",
        "codeknow_cli.server.load_config",
        "codeknow_cli.main.load_config",
    ):
        monkeypatch.setattr(
            target, MagicMock(side_effect=_fresh_default), raising=False
        )
    monkeypatch.setattr(
        "codeknow_cli.main.save_config",
        MagicMock(return_value=None),
        raising=False,
    )
