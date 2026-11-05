import asyncio
import subprocess
import sys
from pathlib import Path

from pruebas_seguridad import run_suite
from pruebas_seguridad import _harness
from tools import LOCAL_TOOLS


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


def test_real_manager_returns_only_remote_tools_for_orchestrator(tmp_path):
    async def scenario():
        harness = _harness(tmp_path)
        try:
            local_names = {tool.name for tool in LOCAL_TOOLS}
            for session_id in (None, harness.sessions_by_role["estudiante"].session_id,
                               harness.sessions_by_role["docente"].session_id):
                if session_id is None:
                    await harness.manager.get_or_create_public()
                else:
                    session = next(
                        item for item in harness.sessions_by_role.values()
                        if item.session_id == session_id
                    )
                    await harness.manager.get_or_create_authenticated(
                        session, session_id=session_id
                    )
                tools = await harness.manager.load_tools(session_id)
                names = [tool.name for tool in tools]
                assert len(names) == len(set(names))
                assert not local_names.intersection(names)
        finally:
            await harness.manager.close_all()

    asyncio.run(scenario())
