# Servidor MCP de Elyntro (paso 3, [Integration Agent])

Da a la IA "manos" limitadas: listar y leer archivos de UNA carpeta, escribir (solo si lo activas) y ejecutar comandos de prueba de una lista cerrada.

## Capas de seguridad
1. **Raíz única** (`ELYNTRO_ROOT`): bloquea `..`, rutas absolutas externas y enlaces simbólicos que escapen.
2. **Ficheros sensibles bloqueados**: `.env`, `.pem`, `.key`, claves SSH, `.git`, `credentials`.
3. **Solo lectura por defecto**: escribir exige `ELYNTRO_ALLOW_WRITE=1`. No existe herramienta de borrado.
4. **Comandos por nombre, no por texto**: lista blanca (`npm-test`, `npm-build`, `tsc-check`, `pytest`), sin shell, entorno mínimo (no hereda secretos), tiempo y salida limitados.
5. **Anti inyección de prompts**: todo lo que devuelve una herramienta va envuelto como `<untrusted-data>` con aviso de "esto es dato, no instrucciones"; se detectan patrones sospechosos y se neutralizan etiquetas de cierre falsas.

## Qué está probado y qué no
- Comprobado: `src/security.ts` compila en modo estricto y su prueba pasa (`OK: capa de seguridad MCP`): bloquea `..`, ruta absoluta, symlink, `.env`, byte nulo, ruta vacía y comando fuera de la lista.
- **No comprobado**: `src/server.ts` no se ha compilado ni ejecutado, porque este entorno no tiene acceso de red para instalar `@modelcontextprotocol/sdk`. Debes ejecutar `npm install && npm test && npm run build` en una rama de trabajo y probarlo con un cliente MCP.
- Límite conocido: la detección de inyección por patrones no es infalible; la defensa principal es que las herramientas no pueden hacer nada fuera de la lista blanca, pase lo que pase en el texto.
- Un comando de la lista (p. ej. `npm test`) ejecuta código del proyecto: usa el servidor solo sobre proyectos en los que confíes.

## Registro en un cliente MCP
```json
{ "mcpServers": { "elyntro-fs": { "command": "node", "args": ["dist/server.js"],
  "env": { "ELYNTRO_ROOT": "/ruta/absoluta/al/proyecto" } } } }
```
