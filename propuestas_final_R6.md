# Plan definitivo para R6

Documento de diseño actualizado con la evidencia E2E de MCP 2.3.0. No se implementó
código funcional ni se modificaron dependencias.

## 1. Separación Lab 04 / Lab 05

### Solo lectura

- `Lab 04 mcp_client.py`
- `Lab 04 mcp_server.py`
- `Lab 04 tools.py`
- El resto de `labs/04-agent-orchestration/`

### Modificables para R6

- `Lab 05 access.py`
- `Lab 05 sessions.py`
- `Lab 05 audit.py`
- `Lab 05 capabilities.py`
- `Lab 05 mcp_client.py`
- `Lab 05 mcp_server.py`
- `Lab 05 orchestrator.py`
- `Lab 05 tools.py`
- `labs/05-examen-final/tests/`

El Lab 04 no será reconstruido ni modificado.

---

## 2. MCP SDK verificado mediante instalación real

El proyecto bloquea:

```text
mcp==2.3.0
mcp-types==2.3.0
```

según `uv.lock`.

La instalación real confirmó:

- `mcp==2.3.0`.
- `mcp-types==2.3.0`.
- `Client` mantiene un ciclo de vida mediante `async with`.
- `StdioServerParameters` lanza subprocesos.
- `MCPServer` admite configuración de lifespan.
- Los handlers pueden recibir un `Context` inyectado.
- La metadata del request está disponible en `ctx.request_context.meta`.

Firmas verificadas:

```python
Client.call_tool(
    self,
    name: str,
    arguments: dict[str, Any] | None = None,
    read_timeout_seconds: float | None = None,
    progress_callback: ProgressFnT | None = None,
    *,
    input_responses: InputResponses | None = None,
    request_state: str | None = None,
    meta: RequestParamsMeta | None = None,
) -> CallToolResult
```

La implementación real de `Client.call_tool()` transmite la metadata mediante:

```python
CallToolRequestParams(_meta=meta)
```

El contexto real se construye con:

```python
Context(
    request_context: ServerRequestContext | None = None,
    mcp_server: MCPServer | None = None,
    input_params: InputResponseRequestParams | None = None,
    subscriptions: SubscriptionBus | None = None,
)
```

Y `ServerRequestContext` incluye:

```python
ServerRequestContext(
    ...,
    meta: RequestParamsMeta | None = None,
    ...
)
```

No se actualizará el SDK.

### Prueba diagnóstica E2E ejecutada

Se ejecutó una prueba aislada con `uv run`, usando transporte stdio real y un servidor
MCP temporal lanzado como subproceso. La herramienta de prueba recibió un argumento
normal separado de la metadata.

El cliente envió:

```python
await client.call_tool(
    "echo_meta",
    {"value": "visible-argument"},
    meta={"x-r6-marker": "R6-E2E-MARKER-20261105"},
)
```

El handler recuperó:

```python
ctx.request_context.meta
```

La metadata recibida fue un diccionario y contenía intacto:

```text
R6-E2E-MARKER-20261105
```

El argumento `visible-argument` permaneció separado de la metadata. Los scripts y
directorios temporales fueron eliminados al terminar la prueba.

Esta prueba diagnóstica confirma el transporte, pero no sustituye las pruebas
funcionales de seguridad de R6, que todavía deben implementarse.

### Prueba diagnóstica adicional del bootstrap

Se ejecutó otra prueba aislada con `uv run`, MCP 2.3.0 y transporte stdio real.
`StdioServerParameters` recibió `env` con:

```text
COM_UPB_MCP_BOOTSTRAP_TOKEN
COM_UPB_MCP_BOOTSTRAP_PUBLIC_KEY
COM_UPB_MCP_USERS_PATH
```

El cliente lanzó el servidor como subproceso. El servidor leyó el bootstrap dentro
del `lifespan` de `MCPServer`, eliminó las variables de entorno después de leerlas,
verificó el JWT con EdDSA, `iss`, `aud`, `iat` y `exp`, y resolvió el usuario
`12345` desde el `usuarios.json` temporal como rol `estudiante`. El contexto quedó
establecido antes del handshake y de `list_tools`.

Resultado observado:

```text
BOOTSTRAP_OK {"jti": "jti-e2e", "role": "estudiante",
             "sid": "sid-e2e", "sub": "12345"}
CLIENT_LIST_TOOLS ["public_probe"]
CLIENT_DONE
```

La prueba confirma que el entorno de `StdioServerParameters` llega al servidor y
permite inicializar el contexto antes de listar herramientas. No demuestra por sí
sola la protección contra repetición ni sustituye las pruebas funcionales de R6.
Los scripts, el `usuarios.json` temporal y sus recursos fueron eliminados al finalizar.

---

## 3. Ciclo de vida de procesos MCP

Se implementará un administrador en Lab 05, conceptualmente:

```text
MCPClientManager
 └── contexts: session_id -> MCPProcessHandle
```

### Estados

```text
AUSENTE
  ↓ login exitoso
INICIANDO
  ↓ handshake correcto
ACTIVO
  ↓ logout / expiración / fallo
CERRANDO
  ↓ limpieza completa
AUSENTE
```

### Inicio de sesión

1. Biometría y liveness crean la sesión mediante `sessions.py`.
2. El administrador obtiene la sesión activa.
3. Genera un contexto firmado.
4. Inicia un proceso MCP dedicado con un bootstrap firmado específico del proceso,
   inyectado mediante el entorno privado del subproceso, no mediante argumentos de
   línea de comandos.
5. El servidor verifica el bootstrap antes del handshake y construye su contexto de
   identidad esperado e inmutable.
6. Ejecuta handshake y obtiene las tools.
7. El cliente filtra tools según la política central antes de presentarlas al LLM.

El `MCPClientManager` garantiza que exista como máximo un proceso MCP autenticado
activo por `session_id`. La creación y consulta de handles se protegerán con un
mecanismo de sincronización, por ejemplo un `asyncio.Lock`: antes de crear un proceso
se comprobará dentro de la sección crítica si ya existe un handle activo para la
sesión. Si existe, se reutiliza; no se inicia un proceso duplicado.

Cada proceso nuevo recibe un bootstrap nuevo y un `jti` nuevo. Si un proceso falla,
el administrador invalida la sesión y exige una nueva autenticación; no lo recrea
con el bootstrap anterior. Un bootstrap ausente o inválido en un proceso solicitado
como autenticado impide su inicialización y produce un error explícito. Nunca se
degrada silenciosamente al contexto público. Estas reglas de ciclo de vida no
proporcionan protección criptográfica general contra repetición; se mantienen las
limitaciones del entorno local ya documentadas.

### Reutilización

El proceso MCP se mantiene asociado a `session_id` y se reutiliza durante múltiples mensajes.

No se creará un proceso por mensaje.

### Expiración

Antes de cada ejecución:

1. Se consulta `SessionManager.get_active_session()`.
2. Se verifica la expiración del contexto firmado.
3. Se comprueba que el proceso siga asociado a la sesión.
4. Si falla alguna condición, se rechaza la operación y se cierra el proceso.

### Logout

1. Se invalida la sesión.
2. Se marca el contexto como revocado en el administrador local.
3. Se cierra el cliente MCP.
4. Se termina el subproceso.
5. Se liberan pipes y tareas pendientes.
6. Se elimina el handle del administrador.

### Cierre forzado

Se utilizará cuando:

- el proceso no responda;
- falle el handshake;
- ocurra una excepción de transporte;
- expire la sesión;
- se detecte contexto inválido.

El cierre debe intentar primero la salida normal y después terminar el proceso de forma forzada.

### Usuarios públicos

Existirá un contexto MCP público independiente, sin identidad autenticada, que solo exponga:

- `calendario`;
- tools locales públicas del Lab 04.

No compartirá estado con procesos autenticados.

El proceso público no necesita JWT. Su servidor solo registrará y ejecutará tools
públicas; no podrá ejecutar tools de estudiante o docente. En cambio, cada proceso
autenticado exigirá un JWT de sesión válido en cada `call_tool` y aplicará la
autorización correspondiente al rol verificado.

El bootstrap autenticado no se escribirá en logs ni se expondrá en schemas, prompts o
argumentos del LLM. El entorno del subproceso no se considera confidencial; la
autenticidad depende de su firma EdDSA. Un proceso que no pueda verificarlo no se
activa.

---

## 4. Contexto de identidad firmado

Se usará firma asimétrica Ed25519.

### Dependencia

Se declarará explícitamente `PyJWT[crypto]` como dependencia directa en el manifiesto real
del proyecto, previsiblemente `pyproject.toml`. Aunque MCP lo trae transitivamente,
importar una dependencia transitiva directamente no es seguro para el mantenimiento del
proyecto. Después se regenerará `uv.lock` usando `uv`; el lockfile no se editará manualmente.

La clave privada permanecerá únicamente en el proceso principal.

El servidor MCP solo recibirá la clave pública.

La validación criptográfica permitirá únicamente el algoritmo `EdDSA`.

Cada proceso autenticado quedará vinculado al contexto esperado e inmutable:

```text
(session_id, telegram_user_id, jti)
```

Ese contexto se fija al arrancar el subproceso y no puede modificarse mediante
argumentos de herramientas ni mediante metadata posterior.

### Claims mínimos

El token contendrá:

```text
iss: emisor interno
aud: contexto MCP
jti: identificador único del contexto
sid: session_id
sub: telegram_user_id
iat: emisión
exp: expiración
```

No incluirá necesariamente rol ni código como fuente de autoridad.

El servidor:

1. Verifica la firma con la clave pública.
2. Comprueba `iss`, `aud`, `iat` y `exp`.
3. Comprueba que los claims `sid`, `sub` y `jti` coincidan exactamente con el
   contexto esperado e inmutable del proceso.
4. Resuelve `telegram_user_id` desde `usuarios.json`.
5. Obtiene rol y código desde ese registro.
6. Rechaza cualquier discrepancia entre ese registro y el contexto esperado.
7. En un proceso autenticado, rechaza cualquier llamada que no presente el JWT de
   sesión correspondiente; el proceso público constituye la excepción y solo permite
   tools públicas sin JWT.

El rol y el código nunca serán argumentos visibles al LLM.

### Invalidación y vigencia

Antes de cada llamada, el proceso principal:

1. Obtendrá la sesión por `telegram_user_id`.
2. Comprobará que siga activa y no haya expirado.
3. Comprobará que el proceso MCP corresponda al `session_id` vigente.
4. Rechazará la llamada y cerrará el proceso si la sesión fue invalidada.

En cada llamada, el servidor MCP:

1. Recuperará el JWT desde `ctx.request_context.meta`.
2. Verificará firma EdDSA, algoritmo permitido, emisor, audiencia, `iat`, `exp`, `jti` y `sid`.
3. Comparará `sid`, `sub` y `jti` con el contexto esperado del proceso.
4. Resolverá el usuario desde `usuarios.json` usando `sub`.
5. Obtendrá rol y código únicamente desde ese registro confiable.
6. Verificará que el token no esté vencido y que el contexto sea coherente.

El proceso hijo no consultará directamente el `SessionManager` del proceso principal.
No existirá una lista global de `jti` revocados entre procesos. La revocación se
materializa en el proceso principal invalidando la sesión, bloqueando nuevas llamadas,
cerrando el proceso MCP dedicado y eliminando su handle. El contexto esperado del hijo
impide sustituir el JWT por otro usuario o sesión mientras el proceso siga vivo.

Al hacer logout o vencer la sesión:

1. Se bloquean nuevas llamadas antes de enviarlas.
2. El administrador marca el handle como `CERRANDO`.
3. Las llamadas pendientes se cancelan o esperan únicamente hasta un timeout controlado.
4. Se cierra el cliente MCP y se termina el subproceso si es necesario.
5. Se registra si cada operación pendiente fue cancelada, completada o falló.
6. El handle se elimina y el contexto deja de ser reutilizable.

Una operación que ya comenzó no se presentará como cancelada si el servidor confirmó
su ejecución; se registrará su resultado real. Si se pierde la respuesta después de
que comenzó una operación de escritura y no puede determinarse si terminó, se
registrará `mcp_operacion_resultado_indeterminado`, no se afirmará que fue cancelada
ni completada, y no se repetirá automáticamente. Será necesario reconciliar el
estado antes de intentar nuevamente. El logout no autoriza nuevas operaciones
después de comenzar el cierre.

---

## 5. Uso del `Context` del SDK

Se utilizará:

- el bootstrap firmado del entorno del subproceso, leído al iniciar el servidor, para
  inicializar el contexto del proceso;
- lifespan del `MCPServer` para conservar ese contexto durante la vida del proceso;
- `Context` inyectado en las tools;
- `ctx.request_context.meta` para leer los tokens transportados.

El `Context` se usará para acceder a la metadata del request. La metadata solo será
un contenedor de transporte: no se confiará en ella sin verificar criptográficamente
su contenido.

No se confiará en:

- headers del cliente;
- argumentos artificiales;
- metadata no firmada;
- valores enviados por el LLM.

No se utilizará pipe ni socket local auxiliar para R6. La prueba E2E confirmó que
`_meta` llega intacto al handler; la autenticidad dependerá exclusivamente de la
verificación EdDSA en el servidor.

La prueba E2E no probó metadata en `list_tools`. Por ello, el servidor no dependerá
de metadata para construir su listado inicial. Para cada proceso autenticado, el
cliente establecerá antes del handshake una variable de entorno exclusiva del
subproceso, por ejemplo `COM_UPB_MCP_BOOTSTRAP_TOKEN`, cuyo valor será un token firmado
con los claims `sid`, `sub` y `jti`. El servidor verificará ese token con la clave
pública, resolverá el usuario y el rol desde `usuarios.json` y fijará el contexto
esperado antes de atender `list_tools`. El JWT no se enviará en argumentos de línea de
comandos.

La firma garantiza autenticidad e integridad, pero no confidencialidad ni protección
automática contra repetición. El bootstrap tendrá audiencia y propósito específicos
(`aud=mcp-bootstrap`, con un emisor dedicado), un vencimiento corto y se eliminará de
`os.environ` inmediatamente después de ser leído. Nunca se registrará su valor. Esta
protección no pretende resistir a un atacante que ya tenga acceso al mismo usuario del
sistema operativo y pueda inspeccionar el entorno o el proceso; el proceso principal
debe cerrar el proceso dedicado y invalidar la sesión ante logout, expiración o fallo.

El filtrado tendrá dos capas:

1. El cliente filtrará la respuesta de `list_tools` con `access.py` antes de exponerla
   al LLM.
2. El servidor podrá restringir su propio listado usando el contexto de arranque; aun
   así, ese listado no será la frontera de seguridad.

La autorización será obligatoria server-side en cada `call_tool`, usando el JWT de
`ctx.request_context.meta` y comparándolo con el contexto esperado. Si una limitación
del SDK impidiera restringir `list_tools`, el servidor podría listar más herramientas,
pero nunca permitirá ejecutarlas sin autorización.

Las claves namespaced definitivas serán:

```text
com.upb.exam/session-token
com.upb.exam/confirmation-token
```

El cliente incluirá el JWT de sesión en cada llamada autenticada. Incluirá el
comprobante de confirmación únicamente en las operaciones de escritura aprobadas.
Ninguno de los dos valores aparecerá en argumentos, schemas, prompts o logs.

---

## 6. Confirmaciones de escritura

Se conservará `ConfirmFn` de `agent.py`.

Aplicará a:

- `solicitar_supletorio`
- `registrar_nota`

La autorización de un solo uso se asociará a:

```text
tool_name
session_id
hash canónico de argumentos
nonce
issued_at
expires_at
```

### Transporte definitivo

El comprobante se transportará mediante:

```text
com.upb.exam/confirmation-token
```

La prueba E2E confirmó que el parámetro `meta` de `Client.call_tool()` llega como
diccionario a `ctx.request_context.meta`. `_meta` no se considera confidencial ni
confiable por sí mismo; el servidor verificará la firma EdDSA antes de aceptar el
comprobante.

La confirmación nunca se agregará al JSON Schema visible al LLM.

Una confirmación:

- no podrá reutilizarse;
- no podrá aplicarse a otros argumentos;
- expirará rápidamente;
- se consumirá atómicamente.

El comprobante solo se emitirá después de que `ConfirmFn` reciba aprobación explícita
del usuario. Contendrá, como mínimo:

```text
tool_name
session_id
arguments_hash
jti
issued_at
expires_at
```

El servidor rechazará toda operación de escritura si falta el comprobante, si está
malformado, si la firma es inválida, si pertenece a otra sesión o herramienta, si el
hash de argumentos no coincide, si está vencido o si su identificador ya fue consumido.

### Registro y consumo atómico

Cada proceso MCP autenticado mantendrá un registro en memoria de los `jti` de
confirmación consumidos y un `asyncio.Lock` asociado. Antes de ejecutar la operación,
el servidor:

1. Verifica la firma EdDSA y el algoritmo permitido.
2. Comprueba herramienta, sesión, hash canónico de argumentos, `iat` y `exp`.
3. Adquiere el lock.
4. Rechaza el comprobante si su `jti` ya está registrado.
5. Registra el `jti` como consumido y libera el lock.
6. Ejecuta la operación una sola vez.

Dos llamadas concurrentes con el mismo comprobante no pueden pasar el paso 4. El
registro se limpia al cerrar el proceso. Si el proceso cae o se reinicia, no se
reutilizarán sus comprobantes: el proceso principal invalidará la sesión asociada,
no recreará el proceso con esa sesión y exigirá una nueva autenticación. Un reinicio
no convierte el registro en un mecanismo de revocación global ni permite repetir una
escritura aprobada previamente.

---

## 7. Matriz oficial de recursos y herramientas

### Recurso MCP

| Recurso | Público | Estudiante | Docente |
|---|---:|---:|---:|
| `calendario` | Sí | Sí | Sí |

`calendario` se protegerá según la naturaleza de un recurso MCP. Su autorización no
se confundirá con el filtrado de `list_tools` ni con la autorización de `call_tool`,
y no se convertirá artificialmente en una herramienta.

### Tools MCP

| Herramienta | Público | Estudiante | Docente | Confirmación |
|---|---:|---:|---:|---:|
| Tools locales públicas del Lab 04 | Sí | Sí | Sí | No |
| `consultar_mis_notas()` | No | Sí | No | No |
| `consultar_mis_solicitudes()` | No | Sí | No | No |
| `solicitar_supletorio(parcial, causa)` | No | Sí | No | Sí |
| `consultar_notas(codigo)` | No | No | Sí | No |
| `consultar_solicitudes(codigo)` | No | No | Sí | No |
| `registrar_nota(codigo, evaluacion, valor)` | No | No | Sí | Sí |

Las tools estudiantiles no recibirán `codigo`.

El código se obtiene exclusivamente de la sesión autenticada.

---

## 8. Política central en `access.py`

Se añadirá una política única que permita consultar:

```text
tool_policy(tool_name)
allowed_tools(role)
authorize_tool(session, tool_name, arguments)
```

La política será utilizada por:

1. `mcp_client.py`, antes de mostrar tools al LLM.
2. `mcp_server.py`, antes de ejecutar.
3. `orchestrator.py`, al construir el contexto.
4. Pruebas unitarias de autorización.

El servidor no dependerá de que el cliente haya ocultado tools.

---

## 9. Archivos y cambios mínimos

### `access.py`

- Política central de tools.
- Roles permitidos.
- Validación de sesión activa.
- Reglas de identidad estudiante/docente.

### `sessions.py`

- Exposición controlada de sesión activa.
- Identificador interno de sesión.
- Sin serialización arbitraria.

### `audit.py`

Eventos MCP:

```text
mcp_tool_denegada
mcp_tool_ejecutada
mcp_contexto_invalido
mcp_confirmacion_rechazada
mcp_proceso_fallido
mcp_operacion_resultado_indeterminado
```

No se registrarán tokens, claves, embeddings, fotos ni argumentos completos sensibles.

### `mcp_client.py`

- Lanzamiento contextualizado.
- `MCPClientManager`.
- Reutilización por sesión.
- Filtrado de tools.
- Cierre y limpieza.
- Transporte de confirmación.

### `mcp_server.py`

- Validación del token.
- Contexto por proceso.
- Tools exactas por rol.
- Tools estudiantiles sin código.
- Autorización en cada llamada.
- Protección contra invocaciones directas.

### `orchestrator.py`

- Recibir sesión activa o contexto público.
- Solicitar/reutilizar cliente MCP.
- Liberar el cliente al cerrar la aplicación.
- Integrar `ConfirmFn`.

### `capabilities.py`

- Usar rol efectivo de sesión.
- Mantener filtrado RAG existente.

### `tools.py`

- Mantener tools públicas.
- Marcar correctamente las tools de escritura.
- No agregar identidad a schemas.

### Tests

Se añadirá una suite específica de R6 sin modificar el Lab 04.

La prueba diagnóstica E2E de metadata ya fue ejecutada y documentada en la sección 2.
Las siguientes pruebas funcionales todavía deben implementarse:

---

## 10. Pruebas previstas

### Política

- Público solo ve tools públicas.
- Estudiante ve únicamente tools estudiantiles y públicas.
- Docente ve únicamente tools docentes y públicas.
- Tools ocultas no pueden ejecutarse manualmente.

### Identidad

- `consultar_mis_notas()` usa el código de sesión.
- Un estudiante no puede enviar otro código.
- Un estudiante no accede a observaciones docentes.
- Sesión expirada rechaza llamadas.
- Token alterado o expirado es rechazado.
- Dos procesos concurrentes no comparten identidad.
- JWT válido es aceptado.
- JWT alterado es rechazado.
- JWT expirado es rechazado.
- JWT con emisor incorrecto es rechazado.
- JWT con audiencia incorrecta es rechazado.
- Usuario del JWT inconsistente con `usuarios.json` es rechazado.
- Sesión del JWT inconsistente con el contexto activo es rechazada.
- Metadata ausente o malformada es rechazada.

### Ciclo de vida

- Bootstrap firmado recibido mediante `StdioServerParameters.env`.
- Bootstrap inválido, vencido o con audiencia/propósito incorrectos es rechazado.
- Inicio de proceso tras login.
- Reutilización entre mensajes.
- Cierre en logout.
- Cierre en expiración.
- Cierre forzado por fallo.
- Limpieza de pipes y tareas.
- Usuario público aislado.
- Logout durante llamadas en curso bloquea nuevas operaciones.
- Las llamadas pendientes dejan registro de cancelación, finalización o fallo.
- Una escritura sin respuesta confirmable registra resultado indeterminado y no se
  repite automáticamente.

### Confirmaciones

- Escrituras requieren `ConfirmFn`.
- Confirmación rechazada no ejecuta.
- Token de confirmación alterado falla.
- Hash de argumentos diferente falla.
- Reutilización falla.
- Expiración falla.
- Comprobante válido es aceptado.
- Comprobante manipulado es rechazado.
- Comprobante vencido es rechazado.
- Comprobante reutilizado es rechazado.
- Dos llamadas concurrentes con el mismo comprobante solo permiten un consumo.
- La caída del proceso invalida la sesión y exige nueva autenticación.
- Aislamiento entre procesos impide usar el comprobante de otra sesión.

### Regresión

- Suite existente de R2, R3, R4 y R5.
- Lab 04 sin diferencias Git.

### Autorización server-side

- El cliente aplica la matriz oficial al filtrar `list_tools`.
- El servidor aplica el contexto de arranque al restringir `list_tools` cuando sea
  técnicamente posible; ese listado no sustituye la autorización de ejecución.
- La misma matriz se aplica durante cada `call_tool`.
- Un estudiante no puede invocar una tool docente aunque la envíe manualmente.
- Un docente no recibe tools exclusivas de estudiante.
- Las tools estudiantiles obtienen el código desde la identidad verificada.

---

## 11. Limitaciones técnicas conocidas

- La prueba E2E verificó `_meta` en `call_tool`, pero no en `list_tools`; por eso el
  listado inicial usa un bootstrap firmado en el entorno privado del subproceso.
- La metadata `_meta` no es confidencial ni confiable por sí misma. La seguridad
  depende de EdDSA y de comparar el JWT con el contexto esperado del proceso.
- El registro de comprobantes consumidos vive en memoria de un proceso. No se
  persistirá ni compartirá entre procesos; una caída invalida la sesión para impedir
  reutilizaciones después de un reinicio.
- Restringir `list_tools` en el servidor depende de las capacidades efectivas del SDK;
  incluso si no fuera posible, `call_tool` seguirá protegido server-side.

---

## 12. Orden de implementación

1. Añadir la dependencia criptográfica directa.
2. Implementar política central en `access.py`.
3. Añadir contexto firmado y validación.
4. Implementar lifecycle manager del cliente MCP.
5. Separar tools por rol.
6. Eliminar código de los schemas estudiantiles.
7. Añadir autorización server-side.
8. Integrar confirmaciones de un solo uso mediante `_meta`.
9. Integrar `orchestrator.py`.
10. Añadir auditoría específica.
11. Ejecutar pruebas R6 y regresión completa.
12. Comparar que `labs/04-agent-orchestration/` permanezca intacto.

El diseño queda listo para revisión. No se implementará código hasta recibir autorización explícita.