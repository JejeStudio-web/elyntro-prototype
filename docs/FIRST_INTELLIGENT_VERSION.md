# Elyntro — primera versión inteligente

## Qué cambia en esta versión

- La API expone aliases versionados en `/api/v1/health` y `/api/v1/agent/execute`; las rutas anteriores se mantienen.
- La conversación envía el historial anterior en orden cronológico. El mensaje nuevo se añade al historial local sin duplicarlo en el contexto enviado a la API.
- Cuando `GROQ_API_KEY` está configurada, la creación de artefactos intenta generar contenido real mediante el modelo, en vez de usar siempre una plantilla fija.
- Los artefactos generados pasan por comprobaciones estáticas de formato. Estas comprobaciones no ejecutan el código y no garantizan seguridad ni corrección funcional.
- Si el proveedor está configurado pero falla, la creación falla de forma explícita; no se presenta una plantilla como si fuera generación del modelo.
- Si no hay clave del modelo, la app conserva un modo de demostración basado en plantillas y declara esa limitación.
- Se añade un workflow de GitHub Actions para ejecutar la suite en pushes y pull requests.

## Configuración necesaria en Render

En el servicio web, añade estas variables desde **Environment**:

- `GROQ_API_KEY`: clave privada del proveedor Groq. Añádela como variable secreta desde el panel de Render; no la escribas en el repositorio.
- `GROQ_MODEL`: opcional. Si se omite, se usa el valor por defecto definido por la aplicación.
- `ELYNTRO_SEARCH_PROVIDER`: `tavily` para investigación con Tavily o `duckduckgo` para la alternativa heredada.
- `TAVILY_API_KEY`: necesaria si se elige Tavily.

Sin `GROQ_API_KEY`, el estado de salud puede ser correcto porque el servidor responde, pero la conversación inteligente no está disponible. Sin `TAVILY_API_KEY`, la investigación con Tavily no tendrá credenciales.

## Despliegue y persistencia

El cambio se desarrolla en una rama separada y no se despliega por sí solo. Para que Render lo publique, configura el servicio para desplegar la rama revisada o fusiona la pull request después de comprobar las pruebas.

El plan gratuito de Render no debe darse por hecho que ofrece disco persistente. SQLite puede perder datos tras reemplazos o despliegues si el servicio no tiene almacenamiento persistente autorizado y configurado. La disponibilidad de `/api/health` solo comprueba que responde la aplicación; no verifica las credenciales ni que una llamada real al modelo funcione.

## Alcance y límites

Esta versión es un primer asistente con contexto de conversación, rutas de ejecución y generación de archivos respaldada por modelo. Todavía no incluye subida de imágenes/audio/vídeo, generación de imágenes, usuarios y sesiones con autenticación, cuotas por usuario ni un sandbox de ejecución aislado. No debe tratarse como un servicio multiusuario endurecido para producción hasta añadir esas protecciones.
