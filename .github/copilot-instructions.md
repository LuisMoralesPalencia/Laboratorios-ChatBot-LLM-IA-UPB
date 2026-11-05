# Instrucciones del proyecto — Examen Final IA

## Contexto
Proyecto académico de Ingeniería de Sistemas e Informática.
Se desarrolla en Python y utiliza uv para gestionar dependencias.

El proyecto parte de los laboratorios del profesor:
- Lab 01: LLM.
- Lab 02: RAG.
- Lab 03: MCP.
- Lab 04: Orquestación.

El examen se desarrolla en labs/05-examen-final/.

## Reglas de arquitectura

1. Reutilizar el Lab 04. No reconstruir el orquestador.
2. Mantener Telegram separado de la lógica de negocio.
3. El núcleo debe poder probarse sin Telegram.
4. Utilizar código determinista para autenticación
   y autorización.
5. Nunca confiar en el rol o código escrito en el chat.
6. Filtrar documentos RAG antes de enviarlos al LLM.
7. Verificar los permisos MCP antes de mostrar y ejecutar tools.
8. Inyectar la identidad autenticada desde la sesión.
9. Exigir confirmación para las operaciones de escritura.

## Seguridad

- Roles: público, estudiante y docente.
- Identidad asociada a telegram_user_id.
- Reconocimiento facial 1:1 mediante YuNet y SFace.
- Prueba de vida mediante video-notas.
- Retos aleatorios, temporales y de un solo uso.
- Sesiones con expiración y bloqueo por intentos fallidos.
- Auditoría JSONL.
- No guardar fotos ni embeddings en los logs.

## RAG

Los documentos están clasificados en:
- publico/
- restringido/estudiante/
- restringido/docente/

Cada fragmento debe conservar su nivel de acceso.
Los fragmentos no autorizados nunca deben llegar al contexto
del LLM.

## MCP

Herramientas públicas, de estudiante y de docente.
Las herramientas de estudiante deben obtener el código
desde la sesión y no desde un parámetro proporcionado
por el LLM.

El servidor debe proteger también la ejecución de las tools.

## Calidad del código

- Python 3.11 o superior.
- Utilizar las dependencias gestionadas por uv.
- Mantener responsabilidades separadas.
- Reutilizar módulos existentes cuando sea posible.
- Manejar errores sin detener el bot.
- Escribir pruebas reproducibles.
- No introducir dependencias sin justificar.
- No modificar archivos ajenos a la tarea solicitada.

## Reglas de trabajo con Copilot

Antes de cada implementación:
1. Revisar los archivos involucrados.
2. Explicar qué se va a modificar.
3. Identificar los riesgos de regresión.
4. Proponer las pruebas correspondientes.

Después de implementar:
1. Ejecutar las pruebas.
2. Explicar los cambios realizados.
3. Indicar qué requisitos del examen se cumplen.
4. Reportar lo que todavía no está comprobado.

Nunca afirmar que una funcionalidad está completa
si únicamente se ha escrito el código y no se ha probado.

## Correspondencia oficial de requisitos del examen

La numeración de requisitos debe respetarse exactamente según el PDF del profesor.

- R1: Canal Telegram integrado al orquestador.
- R2: Reconocimiento facial.
- R3: Prueba de vida.
- R4: Sesiones, roles y auditoría.
- R5: Control de acceso en el RAG.
- R6: Control de acceso en MCP.
- R7: Personalización.
- R8: Suite de seguridad.

### Detalles obligatorios

**R1:** Telegram, historial por chat, comandos /start, /quiensoy, /login, /logout y /ayuda, integración con Orchestrator.answer, núcleo independiente del canal y manejo de errores.

**R2:** Enrolamiento local con 3–5 fotografías, verificación 1:1, YuNet, SFace, rechazo de cero o múltiples rostros, al menos 10 pares genuinos y 20 impostores, tabla FAR/FRR con tres umbrales o más.

**R3:** Retos aleatorios de al menos tres opciones, video-notas de Telegram, tiempo límite, validación por frames y landmarks, mismo rostro, reto vencido, reenviado y de un solo uso.

**R4:** Sesiones por telegram_user_id, datos de usuarios.json, rol y código confiables, expiración, logout, bloqueo después de tres fallos y auditoría JSONL con los eventos exigidos. La auditoría no almacena fotografías ni embeddings.

**R5:** Ingesta de data/publico/ y data/restringido/, clasificación de cada fragmento y filtrado por rol antes de construir el contexto del LLM.

**R6:** Herramientas MCP públicas, de estudiante y de docente según la tabla oficial. Las herramientas de estudiante no reciben código del modelo. El software obtiene el código de la sesión. Los permisos se verifican antes de ofrecer y al ejecutar herramientas. Las operaciones solicitar_supletorio y registrar_nota requieren confirmación mediante botones de Telegram.

**R7:** Nombre de uno de los integrantes en el system prompt y /start. Después del login, saludo con el nombre procedente de la sesión.

**R8:** pruebas_seguridad.py, casos de data/ataques.json, al menos cinco ataques propios, incluyendo prompt injection indirecta desde un documento público. Ejecución sobre el núcleo sin Telegram. Tabla con caso, rol, resultado y herramientas llamadas.

### Ponderación oficial

- R1 + R7: 30 puntos.
- R2 + R3: 30 puntos.
- R4 + R5 + R6: 25 puntos.
- R8 + preguntas de análisis: 15 puntos.

No reasignar requisitos entre categorías. No considerar implementado un requisito solamente porque exista un archivo relacionado.

## Metodología de trabajo del equipo

El equipo está formado por dos integrantes que desarrollan el proyecto conjuntamente mediante una sesión compartida de VS Code.

El desarrollo es secuencial, no paralelo.

Reglas:

1. Implementar una tarea a la vez.
2. No comenzar otra funcionalidad hasta validar la actual.
3. Antes de modificar archivos, explicar qué se va a cambiar.
4. Evitar modificar archivos ajenos a la tarea solicitada.
5. Ejecutar pruebas después de cada implementación.
6. No continuar si existen errores críticos sin resolver.
7. Recomendar commits pequeños después de completar cada funcionalidad.
8. Ambos integrantes revisan y comprenden los cambios antes de aceptarlos.

No dividir el desarrollo en ramas o tareas paralelas para los dos integrantes. Priorizar una integración sencilla y un historial Git fácil de revisar.