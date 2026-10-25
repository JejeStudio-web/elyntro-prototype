# Elyntro v1.1 — Runtime de herramientas y ciclo de ejecución verificable

## Objetivo
Esta nota describe `ToolRuntime`, una utilidad opcional de ejecución iterativa disponible en `tool_runtime.py`. No está conectada a las rutas públicas de Flask; la aplicación usa su flujo de tareas registrado en `app.py`. El runtime recibe las herramientas permitidas como callbacks del llamador, limita el bucle a seis pasos por defecto y no tiene acceso arbitrario al sistema operativo.

## Flujo
1. Recibir objetivo e historial reciente.
2. Pedir al modelo una decisión estructurada JSON.
3. Validar la acción y el nombre de herramienta contra una lista permitida.
4. Ejecutar una herramienta con límites de entrada y registrar el resultado.
5. Incorporar el resultado al contexto como datos no confiables.
6. Repetir hasta respuesta final, error del proveedor o límite de pasos.
7. Devolver respuesta, eventos observables, metadatos del proveedor y estado de verificación.

## Herramientas permitidas
El runtime no incorpora herramientas concretas por sí mismo. El llamador proporciona un diccionario de callbacks permitidos; las pruebas actuales verifican el bucle con una herramienta de cálculo restringido. La aplicación principal implementa la búsqueda y recuperación web por separado.

El runtime no expone por sí mismo ejecución general de código, acceso al sistema operativo, navegador autenticado, ni escritura en servicios externos; las capacidades efectivas dependen de los callbacks que registre el llamador.

## Ciclo de verificación
El sistema conserva eventos con herramienta, estado, resumen y evidencia observada. Para artefactos devuelve resultados de comprobaciones estáticas. La respuesta nunca se etiqueta como verdad verificada solo porque el modelo lo afirme. `tool_evidence_recorded_not_truth_verified` significa exactamente eso: hay registros de ejecución, pero no demuestra por sí mismo la verdad factual ni el cumplimiento total del objetivo.

## Límites de ejecución
- Seis iteraciones del modelo por defecto, configurables entre una y ocho.
- Lista permitida de herramientas; nombres desconocidos se rechazan.
- Argumentos JSON deben ser objetos.
- Salidas de herramientas se tratan como datos no confiables y no como instrucciones.
- Calculadora con gramática aritmética restringida, límites de magnitud y sin ejecución dinámica.
- La lectura web es best-effort y depende de disponibilidad del buscador, DNS y sitio remoto.
- La recuperación web de la aplicación principal resuelve cada destino, rechaza direcciones no globales, conecta directamente a una IP validada y vuelve a validar cada redirección. HTTPS mantiene el hostname para SNI, validación de certificado y `Host`; esto reduce el riesgo de DNS rebinding/redirección, pero no es una garantía absoluta ni un sandbox de red.
- La validación de archivos es estática; no demuestra corrección funcional ni seguridad.

## Pruebas añadidas
- Ejecución de herramienta permitida y continuación hasta respuesta final.
- Rechazo de herramienta no autorizada.
- Límite de pasos produce estado incompleto, nunca éxito falso.
- Rechazo de código arbitrario en la calculadora.

## Despliegue
La aplicación actual se despliega desde el árbol canónico del repositorio, no subiendo o extrayendo ZIPs. La configuración vigente está en `render.yaml`: instala `requirements.lock`, inicia con `gunicorn --bind 0.0.0.0:$PORT app:app` y fija `PYTHON_VERSION` en `3.14.2`. Configura `GROQ_API_KEY` y `TAVILY_API_KEY` como secretos de Render si se habilitan esos proveedores. El blueprint no declara un disco persistente; SQLite puede perder datos en un reinicio, reemplazo o redespliegue.

## No incluido todavía
Memoria persistente de largo plazo con políticas de privacidad, sandbox de código, autenticación, permisos por usuario, navegador de computadora, fuentes con puntuación de autoridad, evaluación factual independiente ni benchmarks comparativos contra ChatGPT/Perplexity. Esos bloques requieren diseño, implementación y pruebas separadas.


## Resultado de validación en este entorno
- `python -m py_compile app.py tool_runtime.py`: correcto.
- `node --check` sobre el JavaScript integrado: correcto.
- `pytest -q tests/test_tool_runtime.py`: 4 pruebas aprobadas.
- No se ha ejecutado una prueba HTTP real contra Groq ni una prueba de despliegue en Render desde este entorno.
