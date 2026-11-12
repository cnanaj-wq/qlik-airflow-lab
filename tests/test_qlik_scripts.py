"""« Lint » des scripts Qlik : règles maison vérifiées en CI avant tout déploiement.

Il n'existe pas de linter officiel pour le langage de script Qlik : on encode nos conventions
en tests. Chaque règle correspond à une erreur déjà rencontrée en mission.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from qlik_lab.deploy import load_manifest, render_script

ROOT = Path(__file__).resolve().parents[1]
SPECS = load_manifest(ROOT / "qlik" / "apps.toml")


@pytest.fixture(params=SPECS, ids=[s.name for s in SPECS])
def script(request: pytest.FixtureRequest) -> tuple[str, str]:
    spec = request.param
    return spec.name, spec.script_path.read_text(encoding="utf-8")


def test_manifest_order() -> None:
    assert [s.name[:2] for s in SPECS] == ["01", "02", "03"]


def _code(text: str) -> str:
    """Script sans les commentaires de ligne."""
    return "\n".join(ln.split("//")[0] for ln in text.splitlines())


def test_no_section_access(script: tuple[str, str]) -> None:
    assert "section access" not in _code(script[1]).lower(), "Section Access hors périmètre de ce lab"


def test_lib_paths_only_through_variable(script: tuple[str, str]) -> None:
    """Un seul chemin en dur (SET vLib) : changer d'espace = changer une ligne."""
    hardcoded = [
        ln
        for ln in script[1].splitlines()
        if "lib://" in ln and not ln.strip().startswith(("SET vLib", "//"))
    ]
    assert hardcoded == []


def test_dq_marker_present(script: tuple[str, str]) -> None:
    name, text = script
    assert re.search(rf"TRACE DQ\|app={re.escape(name)}\|", text), "marqueur TRACE DQ|app=<nom>| manquant"


def test_run_id_has_manual_fallback(script: tuple[str, str]) -> None:
    assert "LET vRunId = If(Len('$(vRunId)') > 0" in script[1]


def test_error_mode_strict(script: tuple[str, str]) -> None:
    assert "SET ErrorMode=1;" in script[1]


def test_no_exit_script_left(script: tuple[str, str]) -> None:
    assert not re.search(r"^\s*exit\s+script", script[1], re.IGNORECASE | re.MULTILINE)


def test_if_blocks_balanced(script: tuple[str, str]) -> None:
    code = _code(script[1])
    opens = len(re.findall(r"^\s*IF\b.*\bTHEN\b", code, re.IGNORECASE | re.MULTILINE))
    closes = len(re.findall(r"^\s*END IF\b", code, re.IGNORECASE | re.MULTILINE))
    assert opens == closes


def test_render_injects_version() -> None:
    rendered = render_script(SPECS[0], "abc1234")
    assert "SET vScriptVersion = 'abc1234';" in rendered.splitlines()[1]
