import sys
import unittest
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LAB_DIR))

from access import (  # noqa: E402
    LOCAL_PUBLIC_TOOL_NAMES,
    authorize_resource,
    authorize_tool,
    allowed_tools,
    requires_confirmation,
    resource_policy,
    tool_policy,
)


PUBLIC_TOOLS = {"fecha_actual", "dias_hasta", "calcular_nota_final"}
STUDENT_TOOLS = {"consultar_mis_notas", "consultar_mis_solicitudes", "solicitar_supletorio"}
TEACHER_TOOLS = {"consultar_notas", "consultar_solicitudes", "registrar_nota"}
CALENDAR_URI = "academico://calendario"


class AccessPolicyTests(unittest.TestCase):
    def test_local_public_tools_match_the_real_registry(self):
        self.assertEqual(LOCAL_PUBLIC_TOOL_NAMES, PUBLIC_TOOLS)

    def test_public_role_gets_only_public_tools(self):
        self.assertEqual(allowed_tools("publico"), PUBLIC_TOOLS)

    def test_student_role_gets_public_and_student_tools(self):
        self.assertEqual(allowed_tools("estudiante"), PUBLIC_TOOLS | STUDENT_TOOLS)

    def test_teacher_role_gets_public_and_teacher_tools(self):
        self.assertEqual(allowed_tools("docente"), PUBLIC_TOOLS | TEACHER_TOOLS)

    def test_roles_cannot_execute_tools_from_other_roles(self):
        for tool_name in TEACHER_TOOLS:
            self.assertFalse(authorize_tool("estudiante", tool_name, {}))
        for tool_name in STUDENT_TOOLS:
            self.assertFalse(authorize_tool("docente", tool_name, {}))
        for tool_name in STUDENT_TOOLS | TEACHER_TOOLS:
            self.assertFalse(authorize_tool("publico", tool_name, {}))

    def test_unknown_tools_and_roles_are_denied(self):
        self.assertIsNone(tool_policy("tool_inexistente"))
        self.assertFalse(authorize_tool("publico", "tool_inexistente", {}))
        self.assertEqual(allowed_tools("administrador"), frozenset())
        self.assertFalse(authorize_tool("administrador", "fecha_actual", {}))

    def test_student_tools_do_not_accept_academic_code(self):
        self.assertTrue(authorize_tool("estudiante", "consultar_mis_notas", {}))
        self.assertFalse(authorize_tool("estudiante", "consultar_mis_notas", {"codigo": "S-1"}))
        self.assertFalse(authorize_tool("estudiante", "solicitar_supletorio", {"codigo": "S-1"}))

    def test_only_write_tools_require_confirmation(self):
        self.assertTrue(requires_confirmation("solicitar_supletorio"))
        self.assertTrue(requires_confirmation("registrar_nota"))
        for tool_name in PUBLIC_TOOLS | {"consultar_mis_notas", "consultar_mis_solicitudes",
                                         "consultar_notas", "consultar_solicitudes"}:
            self.assertFalse(requires_confirmation(tool_name))

    def test_calendar_is_a_separate_resource_policy(self):
        policy = resource_policy(CALENDAR_URI)
        self.assertIsNotNone(policy)
        for role in ("publico", "estudiante", "docente"):
            self.assertTrue(authorize_resource(role, CALENDAR_URI))
        self.assertFalse(authorize_resource("administrador", CALENDAR_URI))
        self.assertNotIn(CALENDAR_URI, allowed_tools("publico"))
        self.assertNotIn("calendario", allowed_tools("publico"))


if __name__ == "__main__":
    unittest.main()
