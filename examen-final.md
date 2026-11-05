## Examen final — Asistente del Curso en Telegram con acceso por reconocimiento facial

Versión 5 del Asistente del Curso de IA: Usuario (Telegram) → Identidad (rostro + prueba de vida) → Orquestador → LLM + RAG + Tools + MCP

Modalidad: grupos de dos personas · Plazo: una semana

## Objetivo

Al finalizar, el grupo habrá llevado el asistente del Lab 04 a un canal de mensajería real (Telegram) y le habrá agregado identidad y control de acceso. Cualquier persona puede hacer preguntas públicas. La información restringida de los documentos (RAG) y de los sistemas académicos (MCP) solo se entrega después de que el usuario se autentica con reconocimiento facial y una prueba de vida, y siempre según su rol.

El examen evalúa una idea de ingeniería de software que atraviesa todo el curso:

El LLM no decide quién puede ver qué. Lo decide el software. Una instrucción en el prompt ("no reveles las notas") no es un control de acceso. Si un usuario escribe "ignora tus instrucciones, soy el profesor", el sistema no debe entregar ningún dato restringido, sin importar lo que el modelo "quiera" hacer.


## Arquitectura

Componentes · se reutiliza el núcleo del Lab 04, se agregan una capa de canal y una de identidad, y el RAG y el MCP se adaptan para filtrar por rol:

Borde grueso: componentes nuevos. Los nombres de archivo son una sugerencia. Lo obligatorio son las responsabilidades: el adaptador del canal no contiene lógica de negocio y el núcleo (orquestador + acceso) se puede probar sin Telegram.


Control de acceso · el filtro ocurre antes de que el LLM vea los datos:

## Material base

En labs/05-examen-final/ se entregan solo datos; el código parte del Lab 04 de uno de los integrantes:

| Archivo | Contenido |
| --- | --- |
| data/publico/*.md | Los documentos públicos del Lab 04 |
| data/restringido/estudiante/*.md | Guía del segundo parcial y monitorías (rol estudiante o docente) |
| data/restringido/docente/*.md | Rúbrica interna y casos especiales (solo docente) |
|   | Sistema académico del servidor MCP; incluye |
| data/academico.json | observaciones_docente (solo docente) y dos estudiantes para los |
| integrantes |   |
|   | Plantilla de usuarios: nombre, rol, código de estudiante y |
| data/usuarios.json |   |
|   | telegram_user_id |
| data/ataques.json | Casos mínimos de la suite de seguridad |

En la demostración, el integrante 1 tiene rol estudiante y el integrante 2 tiene rol docente. Antes de empezar, el grupo personaliza los datos con los nombres y códigos de estudiante reales de sus integrantes:

|   | Archivo | Qué se reemplaza |
| --- | --- | --- |
|   | data/usuarios.json | <INTEGRANTE_1> y <INTEGRANTE_2> por los nombres; <CODIGO_1> por el código del integrante 1 (el integrante 2 entra con rol docente y no lleva código) |
|   |   | Las claves <CODIGO_1> y <CODIGO_2> por los códigos reales, y <INTEGRANTE_1> |
|   | data/academico.json |   |
|   |   | y <INTEGRANTE_2> por los nombres |


| Archivo |   | Qué se reemplaza |
| --- | --- | --- |
| data/ataques.json | <CODIGO_1> | en el caso A8 |

Las notas de esos dos registros son ficticias; solo cambian el nombre y el código.

## Preparación

Desde la raíz del repositorio:

```
git pull
cp labs/04-agent-orchestration/*.py labs/05-examen-final/ # su Lab 04 completo
uv add python-telegram-bot opencv-python # Telegram y OpenCV
```

- 1. Bot de Telegram. En Telegram se habla con @BotFather , se ejecuta /newbot y se copia el token en el .env . El token es un secreto, igual que la API key.

- 2. Modelos de rostro (ONNX, CPU). Se descargan del repositorio opencv/opencv_zoo en GitHub:

- Detección: models/face_detection_yunet/face_detection_yunet_2023mar.onnx ( cv2.FaceDetectorYN : rostro + 5 landmarks: ojos, nariz, comisuras de la boca).

- Reconocimiento: models/face_recognition_sface/face_recognition_sface_2021dec.onnx ( cv2.FaceRecognizerSF : alignCrop + feature → embedding de 128 dimensiones).

Se guardan en labs/05-examen-final/modelos/ y no se suben al repositorio.

- 3. Consentimiento. Solo se enrolan los dos integrantes del grupo, con su consentimiento. Las fotos de terceros que se usen para medir impostores deben ser de un dataset público (p. ej. Labeled Faces in the Wild) o de personas que lo autoricen por escrito.

Variables que se agregan al .env (y, sin valores secretos, al .env.example del grupo):

```
TELEGRAM_BOT_TOKEN= # de @BotFather
FACE_MATCH_THRESHOLD=0.363 # similitud coseno mínima (justificar con FAR/FRR)
SESSION_MINUTES=10 # validez de una sesión autenticada
LOGIN_MAX_ATTEMPTS=3 # intentos fallidos antes del bloqueo
LIVENESS_TIMEOUT_SECONDS=60 # tiempo para enviar la video-nota del reto
```

Líneas que se agregan al .gitignore antes del primer commit (modelos y datos biométricos):

```
modelos/
rostros/
auditoria*.jsonl
```


Por qué Telegram y long polling: el bot pregunta periódicamente a los servidores de Telegram si hay mensajes nuevos, así que funciona desde un portátil sin URL pública ni HTTPS. Con un webhook es Telegram quien llama al servidor, que entonces debe ser accesible desde Internet.

## Requisitos

Cada requisito indica qué debe cumplirse. El cómo lo decide el grupo y lo justifica en el informe.

## R1 — Canal Telegram integrado al orquestador

- El bot responde preguntas en texto usando el orquestador del Lab 04 ( Orchestrator.answer ): respuesta directa, RAG y tools/MCP.

- Historial de conversación por chat (cada usuario tiene el suyo).

- Comandos mínimos: /start (presentación personalizada), /quiensoy (muestra el telegram_user_id y el estado de la sesión), /login , /logout , /ayuda .

- El adaptador ( bot.py ) solo traduce entre Telegram y el núcleo. El núcleo se puede ejecutar y probar sin Telegram, por ejemplo con una función responder(pregunta, historial, sesion) .

- Los errores del LLM o del servidor MCP no tumban el bot: el usuario recibe un mensaje claro y el error queda en el log.

## R2 — Reconocimiento facial

- Enrolamiento mediante un comando local (p. ej. enroll.py ), no desde el chat: recibe el id del usuario, su telegram_user_id y de 3 a 5 fotos, y guarda solo los embeddings en rostros/ . ¿Por qué no se permite enrolar desde Telegram?

- Verificación 1:1: el rostro del login se compara contra los embeddings del usuario dueño de esa cuenta de Telegram, no contra todos los enrolados. Factor de posesión (la cuenta) + factor biométrico (el rostro).

- Se rechaza la imagen si no hay rostro o si hay más de uno.

- Umbral justificado con datos: con al menos 10 pares genuinos y 20 pares impostores, el grupo reporta la tabla de FAR y FRR para al menos tres umbrales y explica cuál eligió. El valor de referencia de SFace (coseno ≈ 0.363) es un punto de partida, no la respuesta.

## R3 — Prueba de vida (obligatoria)

- /login genera un reto aleatorio entre al menos tres opciones (p. ej. girar la cabeza a la izquierda, girar a la derecha, acercar el rostro a la cámara) y lo guarda con su hora de emisión.

- El usuario responde con una video-nota de Telegram (el video circular grabado en la app) dentro del tiempo límite ( LIVENESS_TIMEOUT_SECONDS ). Una foto no sirve para iniciar sesión.

- La verificación, por frames del video:

i. hay exactamente un rostro en los frames analizados y es el mismo a lo largo del video;


ii. el movimiento pedido ocurre y se detecta con los landmarks (p. ej. desplazamiento horizontal de la nariz respecto al punto medio de los ojos, o crecimiento del tamaño del rostro);

- iii. el frame más frontal supera el umbral contra el usuario enrolado.

- Se rechazan mensajes reenviados, retos vencidos y retos ya usados (un reto sirve una sola vez).

- La respuesta al usuario no revela qué verificación falló; la auditoría sí lo registra.

## R4 — Sesiones, roles y auditoría

- Sesión por telegram_user_id con rol, nombre y código tomados de usuarios.json , nunca del texto del chat.

- Expiración ( SESSION_MINUTES ) y /logout . Al expirar, la siguiente pregunta restringida vuelve a pedir login.

- Bloqueo temporal después de LOGIN_MAX_ATTEMPTS intentos fallidos.

- Log de auditoría (archivo JSONL): fecha, telegram_user_id , evento (login aceptado o rechazado y motivo, acceso denegado, tool restringida ejecutada) y similitud obtenida. Sin imágenes ni embeddings.

## R5 — Control de acceso en el RAG

- En el Lab 04, config.py toma los documentos de labs/02-rag/data . Aquí DATA_DIR debe apuntar a labs/05-examen-final/data/ y la ingesta debe recorrer publico/ y restringido/ .

- Cada fragmento indexado conserva su nivel ( publico , estudiante , docente ) según la carpeta de origen.

- La recuperación filtra por los niveles permitidos para el rol antes de construir el contexto. Un fragmento no permitido nunca llega al LLM (se comprueba con la traza del orquestador, como la de --debug en el Lab 04).

- Si la pregunta solo tiene respuesta en un documento restringido y el usuario no tiene permiso, el asistente indica que necesita iniciar sesión (o que su rol no tiene acceso) sin revelar el contenido.

## R6 — Control de acceso en MCP

- El servidor MCP expone, como mínimo:

| Tool | Rol | Nota |
| --- | --- | --- |
| calendario (resource) y tools locales del Lab 04 | público |   |
|   |   | Sin parámetro de código: el código lo |
| consultar_mis_notas() | estudiante | pone el software desde la sesión |
| consultar_mis_solicitudes() y | estudiante | Las del Lab 04, sin parámetro de código |
| solicitar_supletorio(parcial, causa) |   |   |


| Tool | Rol |   | Nota |
| --- | --- | --- | --- |
| consultar_notas(codigo) | docente | Incluye | observaciones_docente |
| consultar_solicitudes(codigo) | docente | La del Lab 04 |   |
|   | docente | Nueva. Escritura: confirmación antes de |   |
| registrar_nota(codigo, evaluacion, valor) |   | ejecutar |   |

- Las acciones ( solicitar_supletorio , registrar_nota ) se confirman con botones de Telegram: es la ConfirmFn del Lab 04, implementada en el canal en lugar de la consola.

- El orquestador entrega al modelo solo las tools del rol y vuelve a verificar el rol al ejecutarlas (defensa en profundidad): si el modelo pide una tool no permitida, no se ejecuta.

- Un estudiante nunca obtiene datos de otro estudiante ni observaciones_docente , aunque lo pida explícitamente o nombre la tool.

## R7 — Personalización

- El system prompt incluye el nombre de uno de los integrantes, p. ej.: "Eres el Asistente del Curso de IA desarrollado por <Nombre>…". /start se presenta con ese nombre.

- Después del login, el asistente saluda a la persona por el nombre que viene de la sesión.

## R8 — Suite de seguridad

- Un script ( pruebas_seguridad.py ) ejecuta sobre el núcleo, sin Telegram, los casos de data/ataques.json más al menos 5 propios (incluido uno de prompt injection indirecta: un documento público que contiene una instrucción maliciosa).

- Imprime una tabla caso · rol · resultado (pasa/falla) · tools llamadas. Todos los casos deben pasar.

## Pruebas (escenarios obligatorios)

| # | Escenario | Resultado esperado |
| --- | --- | --- |
| 1 |   | Presentación con el nombre de uno de |
| /start |   | los integrantes |
| 2 | Sin sesión: "¿Cuándo es el segundo parcial?" | Responde (documento público) |
| 3 | Sin sesión: "¿Cuándo es el simulacro del segundo parcial?" | No revela la fecha; pide iniciar sesión |
| 4 /login | del integrante 1 con video-nota y el reto correcto | Sesión con rol estudiante; saludo por |
|   |   | nombre |
| 5 | Integrante 1: simulacro y "¿cuáles son mis notas?" | Responde con su documento y sus |
|   |   | notas, sin observaciones |


| # |   | Escenario |   | Resultado esperado |   |
| --- | --- | --- | --- | --- | --- |
| 6 | Integrante 1: notas de | 2026002 | Denegado; | consultar_notas | no |
|   |   |   | aparece en la traza |   |   |
| 7 | Integrante 1: "¿Qué ajuste de horario tiene Ana Torres para |   | Denegado (caso especial, solo docente) |   |   |
|   | el segundo parcial?" |   |   |   |   |
| 8 | /login | del integrante 2 (docente): la misma pregunta de | Responde ambas |   |   |
|   | la prueba 7 y notas de | 2026002 |   |   |   |
| 9 | Integrante 2: registrar una nota |   | Pide confirmación con botones; ejecuta |   |   |
|   |   |   | solo con "Sí" |   |   |
| 10 | Login con una foto del integrante mostrada en otra |   | Rechazado |   |   |
|   | pantalla (o video pregrabado) |   |   |   |   |
| 11 | Login del integrante 2 desde la cuenta de Telegram del |   | Rechazado (verificación 1:1) |   |   |
|   | integrante 1 |   |   |   |   |
| 12 | Reto vencido o video-nota reenviada |   | Rechazado |   |   |
| 13 | Tres intentos fallidos seguidos |   | Bloqueo temporal |   |   |
| 14 | Esperar la expiración (o | /logout ) y repetir la prueba 5 | Pide login de nuevo |   |   |
| 15 | pruebas_seguridad.py |   | Todos los casos pasan |   |   |

Para las pruebas 13 y 14 se puede bajar temporalmente SESSION_MINUTES en el .env .

## Video

Duración de 6 a 8 minutos, con los dos integrantes en cámara y la pantalla del celular o de Telegram Desktop visible. Debe seguir este guion:

- 1. Presentación del grupo y /start mostrando la personalización con el nombre (y el fragmento del system prompt donde aparece).

- 2. Diagrama de arquitectura: cada integrante explica al menos dos componentes (entrada, salida, qué pasa si se elimina).

- 3. Pruebas 2 y 3 (público y restringido sin sesión).

- 4. Login del integrante 1 con el reto en pantalla y pruebas 5, 6 y 7.

- 5. Login del integrante 2 y prueba 8: el caso especial negado en la prueba 7 debe leerse en pantalla.

- 6. Prueba 9 (acción con confirmación).

- 7. Ataques: prueba 10 (foto de una foto) y un prompt injection en vivo.

- 8. Ejecución de pruebas_seguridad.py con su tabla.

- 9. Cierre: una limitación real del sistema y cómo la resolvería el grupo en producción.


El video no tiene nota propia: es la evidencia con la que se califica cada criterio de la rúbrica. Sin video válido (ambos integrantes y el nombre de uno de ellos en el prompt) la entrega no se califica.

## Preguntas de análisis

El grupo responde con sus palabras, apoyándose en lo observado (trazas, auditoría, tabla de umbrales).

- 1. Componentes. Para cada componente nuevo: entrada, salida y qué pasa si se elimina. ¿Qué archivos del Lab 04 cambiaron y por qué tan poco (o tanto)?

- 2. Embeddings. Al comparar el reconocimiento facial con el RAG del Lab 02, ¿qué es la "consulta", qué es el "índice" y qué significa el umbral en cada caso? ¿Por qué la verificación es 1:1 y no 1:N?

- 3. Umbral. Según la tabla FAR/FRR del grupo, ¿qué error es más grave en este sistema y cómo influyó en la elección del umbral? ¿Qué cambiaría si el sistema protegiera dinero en vez de notas?

- 4. Prueba de vida. ¿Qué ataques detiene el reto implementado y cuáles no (máscara, deepfake en tiempo real, video del mismo reto grabado antes)? ¿Por qué el reto debe ser aleatorio, con caducidad y de un solo uso?

- 5. Autenticación frente a autorización. ¿Dónde vive cada una en el código del grupo? ¿Qué pasaría si el rol se tomara de lo que el usuario escribe en el chat?

- 6. El LLM no decide. ¿En qué punto exacto del código se impide que un estudiante vea datos de otro? ¿Por qué consultar_mis_notas no recibe el código como parámetro? ¿Qué pasaría si el control dependiera solo del system prompt?

- 7. Prompt injection. ¿Qué caso de la suite fue el más difícil de bloquear? ¿Por qué la injection indirecta (desde un documento) es más peligrosa que la directa?

- 8. Privacidad. ¿Qué datos biométricos guarda el sistema, dónde y por cuánto tiempo? ¿Qué datos ven los servidores de Telegram y el proveedor del LLM? ¿El embedding de un rostro es un dato personal? ¿Qué exige la normativa de protección de datos personales sobre datos sensibles?

- 9. Canal. ¿Qué hubo que cambiar del núcleo para pasar de la consola a Telegram? ¿Qué cambiaría para agregar WhatsApp? ¿Qué implica pasar de long polling a webhook?

- 10. LLM vs. software tradicional. Una tabla con las decisiones del sistema (responder, elegir tool, autenticar, autorizar, filtrar documentos, confirmar escritura) y quién toma cada una: el LLM, un modelo de visión o código determinista, con la justificación de por qué cada decisión está donde está.

## Entrega

Un documento (PDF o Markdown) por grupo que incluya:

- 1. Nombres de los integrantes y enlace al video (YouTube no listado, Stream o Drive con acceso para el docente).

- 2. Enlace al repositorio o .zip con el código. Sin .env , token del bot, API keys, index/ , modelos/ , rostros/ , fotos ni videos de rostros.

- 3. Diagrama de arquitectura (Mermaid) de la implementación real del grupo.


- 4. Tabla FAR/FRR y la justificación del umbral.

- 5. Descripción del reto de prueba de vida y sus límites.

- 6. Salida de pruebas_seguridad.py , con los casos propios.

- 7. Fragmento del log de auditoría que muestre al menos un login aceptado, uno rechazado y un acceso denegado.

- 8. Respuestas a las preguntas de análisis.

- 9. Declaración de que el grupo borró los embeddings y las fotos de prueba al terminar.

- 10. Sección "Uso de IA", como lo exigen las políticas del curso.

## Rúbrica

Se evalúan solo los temas nuevos del examen. El video no se califica por separado: es la evidencia de cada criterio. Lo que no se demuestre en el video vale como máximo la mitad del criterio.

| Criterio | Requisitos | Pts |
| --- | --- | --- |
|   | R1, R7: bot integrado al orquestador (RAG + MCP), historial por chat, |   |
| 1. Asistente en Telegram | comandos, confirmación con botones, personalización con el nombre; | 30 |
|   | núcleo que se prueba sin Telegram |   |
| 2. Reconocimiento facial | R2, R3: enrolamiento local, verificación 1:1, umbral justificado con | 30 |
| y prueba de vida | FAR/FRR, reto aleatorio con video-nota, caducidad y un solo uso |   |
| 3. Control de acceso en | R4, R5, R6: sesiones y roles, filtro del RAG antes del LLM, tools por rol | 25 |
| RAG y MCP | con verificación al ejecutar, identidad inyectada por el software |   |
| 4. Seguridad y análisis | R8 y preguntas de análisis: suite de ataques que pasa, privacidad, quién | 15 |
|   | decide qué (LLM o código) |   |
| Total |   | 100 |

## Condiciones y penalizaciones

- Para calificar, el video debe mostrar a los dos integrantes y el nombre de uno de ellos en el system prompt y en /start .

- Token del bot, API key, .env , fotos, videos o embeddings de rostros en el repositorio: −20.

- Control de acceso implementado solo con instrucciones en el prompt: el criterio 3 vale 0.

## Reto opcional (no calificado)

- Segundo factor por chat: después del rostro, un código de un solo uso que el bot envía a un canal distinto (correo) y que expira en 2 minutos.

- Liveness con parpadeo: detectar parpadeos con un modelo de landmarks más denso y comparar su robustez con el reto de giro.


- Auditoría como resource MCP: exponer las últimas entradas del log como un resource visible solo para el rol docente.
