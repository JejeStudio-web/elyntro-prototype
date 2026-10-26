# Fases 3–6: auditoría técnica y estado verificable

Este documento diferencia código presente de funciones conectadas y validadas en producción. La presencia de un módulo no demuestra que un proveedor tenga credenciales válidas o que una interfaz esté conectada a persistencia compartida.

## Fase 3 — Modelos y proveedores

**Implementado parcialmente en código:** `model_registry.py` registra perfiles/capacidades y límites; `model_router.py` selecciona candidatos con base en capacidad, coste y evaluaciones; `model_gateway.py` define llamadas normalizadas, adaptador OpenAI-compatible, tiempos límite y errores seguros. Las claves se leen de variables de entorno. Groq se usa en el camino de generación de la aplicación.

**Pendiente de validar/completar:** matriz real de proveedores con credenciales válidas, cuota y disponibilidad; adaptadores multimodales concretos para visión/audio; cuotas globales por usuario/periodo; circuit breaker/fallback probado con proveedores reales; métricas operativas y pruebas de smoke desde el despliegue. No afirmar que una clave o un proveedor funciona sin una llamada controlada.

## Fase 4 — Herramientas, investigación y archivos

**Implementado parcialmente:** `tool_runtime.py` implementa un ciclo acotado con herramientas permitidas, eventos y evidencia; `search_providers.py` normaliza búsqueda y extracción pública con validación de URL/DNS; `app.py` expone la API de tareas y creación de archivos con validación básica.

**Añadido en esta rama:** `artifact_store.py` guarda snapshots inmutables en SQLite, hash SHA-256, namespace, proyecto/tarea, metadatos acotados, listado y restauración como nueva versión. El gestor de artefactos es una capa de almacenamiento; su API y la UI deben conectarse antes de considerar la fase cerrada.

**Pendiente:** conectar artefactos a rutas autenticadas y UI; analizar MIME real y antivirus/escaneo donde proceda; límites por usuario y almacenamiento; guardar citas de forma consistente en todas las rutas de investigación; despliegue con volumen persistente o base de datos compartida (el SQLite local de un servicio efímero no garantiza persistencia multi-instancia).

## Fase 5 — Workspace, Control Room y Morph

**Implementado parcialmente:** interfaz de conversación en español y persistencia local de hasta 40 conversaciones en el navegador (PR #22 ya desplegada); Three.js Studio en la rama de PR #24; endpoints de salud/API y herramientas existentes.

**Pendiente:** identidad y sesiones autenticadas; proyectos/archivos compartidos entre dispositivos; adjuntos reales con progreso y procesamiento de imagen/vídeo/audio; Control Room conectado a estados de backend, colas, cuotas y despliegues; accesibilidad y pruebas manuales en iPad. El historial local no es una cuenta ni una base de datos sincronizada.

## Fase 6 — Memoria, historial y recuperación

**Implementado parcialmente:** `memory.py` ofrece memoria SQLite con namespace, tipo, fuente, confianza, fechas, expiración y eventos; incluye controles de privacidad. El historial de conversaciones de navegador no equivale a memoria persistente del servidor.

**Añadido en esta rama:** versionado de artefactos, comparación por versión/hash y restauración no destructiva mediante nueva versión.

**Pendiente:** API autenticada para consultar/editar/eliminar memoria; namespace vinculado a identidad real (no confiar en un namespace enviado libremente por cliente); búsqueda semántica sólo tras evaluar calidad/coste; restauración UI; backup/retención y persistencia compartida en producción.

## Criterios de salida y bloqueos de producción

- No marcar una fase completa hasta conectar interfaz, API y almacenamiento y ejecutar pruebas en el despliegue real.
- No habilitar el navegador Playwright en producción hasta crear el Private Service, configurar token y allowlist directamente en Render, probar conectividad y aplicar una política de salida de red contra destinos privados/metadata.
- No almacenar secretos en Git ni incluirlos en logs o mensajes.
- `ArtifactStore` usa SQLite local como backend inicial. Antes de múltiples instancias o datos que deban sobrevivir a reemplazos de instancia, migrar a Postgres/objeto compartido.
