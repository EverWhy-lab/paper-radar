from __future__ import annotations

import os
from pathlib import Path

from paper_radar.cli import main
from paper_radar.environment import load_project_dotenv


def test_existing_environment_value_wins_over_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENALEX_API_KEY=dotenv-openalex\nDEEPSEEK_API_KEY=dotenv-deepseek\n",
        encoding="utf-8",
    )
    environment = {"OPENALEX_API_KEY": "injected-openalex"}

    loaded = load_project_dotenv(tmp_path, environment=environment)

    assert loaded == 1
    assert environment == {
        "OPENALEX_API_KEY": "injected-openalex",
        "DEEPSEEK_API_KEY": "dotenv-deepseek",
    }


def test_missing_environment_value_loads_from_project_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "OPENALEX_API_KEY=dotenv-openalex\n",
        encoding="utf-8",
    )
    environment: dict[str, str] = {}

    loaded = load_project_dotenv(tmp_path, environment=environment)

    assert loaded == 1
    assert environment["OPENALEX_API_KEY"] == "dotenv-openalex"


def test_missing_dotenv_is_a_noop(tmp_path: Path) -> None:
    environment = {"EXISTING": "value"}

    assert load_project_dotenv(tmp_path, environment=environment) == 0
    assert environment == {"EXISTING": "value"}


def test_dotenv_supports_comments_empty_lines_and_quoted_values(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        """
# local secrets
PLAIN=plain-value
SINGLE='single quoted value'
DOUBLE="double quoted # value"
INLINE=visible # local comment
EMPTY=""
export EXPORTED='exported value'
INVALID KEY=ignored
UNCLOSED="ignored
""".lstrip(),
        encoding="utf-8",
    )
    environment: dict[str, str] = {}

    loaded = load_project_dotenv(tmp_path, environment=environment)

    assert loaded == 6
    assert environment == {
        "PLAIN": "plain-value",
        "SINGLE": "single quoted value",
        "DOUBLE": "double quoted # value",
        "INLINE": "visible",
        "EMPTY": "",
        "EXPORTED": "exported value",
    }


def test_cli_loads_project_dotenv_without_printing_secrets(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    openalex_secret = "fixture-openalex-secret"
    deepseek_secret = "fixture-deepseek-secret"
    (tmp_path / ".env").write_text(
        (
            f'OPENALEX_API_KEY="{openalex_secret}"\n'
            f"DEEPSEEK_API_KEY='{deepseek_secret}'\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    assert main(["serve"]) == 1

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert os.environ["OPENALEX_API_KEY"] == openalex_secret
    assert os.environ["DEEPSEEK_API_KEY"] == deepseek_secret
    assert openalex_secret not in output
    assert deepseek_secret not in output
