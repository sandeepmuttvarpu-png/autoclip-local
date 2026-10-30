"""
Unit tests for the AutoClip-Local Click CLI command suite.
Verifies CLI command parsing, flag validation, and help outputs.
"""

from click.testing import CliRunner
import pytest

from autoclip import __version__
from autoclip.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_version(runner: CliRunner):
    """Verify autoclip --version outputs the correct version."""
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_cli_help(runner: CliRunner):
    """Verify main autoclip help screen lists all subcommands."""
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "scan" in result.output
    assert "slice" in result.output
    assert "studio" in result.output
    assert "fuse-story" in result.output
    assert "publish" in result.output
    assert "detect-hype" in result.output
    assert "gaming" in result.output
    assert "run" in result.output


def test_cli_slice_flags(runner: CliRunner):
    """Verify autoclip slice options and defaults."""
    result = runner.invoke(main, ["slice", "--help"])
    assert result.exit_code == 0
    assert "--duration" in result.output
    assert "--max-clips" in result.output
    assert "--output-dir" in result.output
    assert "--direct / --download" in result.output


def test_cli_studio_flags(runner: CliRunner):
    """Verify autoclip studio options."""
    result = runner.invoke(main, ["studio", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--reload" in result.output


def test_cli_fuse_story_flags(runner: CliRunner):
    """Verify autoclip fuse-story options."""
    result = runner.invoke(main, ["fuse-story", "--help"])
    assert result.exit_code == 0
    assert "--category" in result.output
    assert "--voice" in result.output
    assert "--headline" in result.output


def test_cli_publish_missing_args(runner: CliRunner):
    """Verify autoclip publish requires title and publish-at."""
    result = runner.invoke(main, ["publish"])
    assert result.exit_code != 0
    assert "Missing argument" in result.output or "Error" in result.output
