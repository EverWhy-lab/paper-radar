from pathlib import Path
import json

import pytest

from paper_radar.config import ResearchProfile, load_profile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile() -> ResearchProfile:
    return load_profile(PROJECT_ROOT / "config" / "research_profile.yaml")


@pytest.fixture
def atom_xml() -> str:
    return (PROJECT_ROOT / "tests" / "fixtures" / "arxiv_response.xml").read_text(encoding="utf-8")


@pytest.fixture
def openalex_payload() -> dict:
    return json.loads(
        (PROJECT_ROOT / "tests" / "fixtures" / "openalex_works.json").read_text(
            encoding="utf-8"
        )
    )
