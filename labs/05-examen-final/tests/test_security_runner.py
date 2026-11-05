import asyncio
import subprocess
import sys
from pathlib import Path

from pruebas_seguridad import run_suite


LAB_DIR = Path(__file__).resolve().parents[1]


def test_security_runner_executes_official_and_custom_cases():
    results = asyncio.run(run_suite())
    assert len(results) == 13
    assert {result.case_id for result in results} >= {"A1", "A8", "P1", "P2", "P3", "P4", "P5"}
    assert all(result.passed for result in results)


def test_indirect_injection_has_no_unauthorized_execution():
    result = next(
        result for result in asyncio.run(run_suite()) if result.case_id == "P5"
    )
    assert result.unauthorized_attempt is False
    assert "registrar_nota" not in result.tools_executed
    assert "consultar_notas" not in result.tools_executed


def test_security_runner_returns_nonzero_when_a_case_fails(monkeypatch):
    import pruebas_seguridad

    monkeypatch.setattr(
        pruebas_seguridad,
        "run_suite",
        lambda: asyncio.sleep(0, result=[
            pruebas_seguridad.SecurityResult("X", "forced", "publico", "control", False)
        ]),
    )
    assert pruebas_seguridad.print_results(asyncio.run(pruebas_seguridad.run_suite())) == 1


def test_security_runner_is_executable():
    result = subprocess.run(
        [sys.executable, str(LAB_DIR / "pruebas_seguridad.py")],
        capture_output=True,
        text=True,
        cwd=LAB_DIR,
    )
    assert result.returncode == 0
    assert "Estado general: PASA" in result.stdout
