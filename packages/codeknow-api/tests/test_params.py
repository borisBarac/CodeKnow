"""Tests for codeknow_api.params — GitHub SSH URL validation."""

from __future__ import annotations

import pytest

from codeknow_api.params import is_valid_github_ssh_url, validate_github_ssh_url


class TestIsValidGithubSshUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/repo.git",
            "git@github.com:owner/repo",
            "git@github.com:my-org/my.repo.git",
            "git@github.com:user_123/proj.v2.git",
            "git@github.com:A-B.C/1_2.3.git",
        ],
    )
    def test_valid(self, url: str) -> None:
        assert is_valid_github_ssh_url(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/owner/repo.git",
            "git@gitlab.com:owner/repo.git",
            "git@github.com:bad",
            "",
            "git@github.com:/repo.git",
            "git@github.com:owner/",
            " git@github.com:owner/repo.git",
            "git@github.com:owner/repo.git ",
            "git@github.com:owner/repo/",
        ],
    )
    def test_invalid(self, url: str) -> None:
        assert is_valid_github_ssh_url(url) is False


class TestValidateGithubSshUrl:
    def test_valid_does_not_raise(self) -> None:
        validate_github_ssh_url("git@github.com:owner/repo.git")

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub SSH URL"):
            validate_github_ssh_url("https://github.com/owner/repo.git")

    def test_error_message_contains_bad_input(self) -> None:
        bad = "not-a-url"
        with pytest.raises(ValueError, match=bad):
            validate_github_ssh_url(bad)
