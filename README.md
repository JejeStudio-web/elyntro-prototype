Elyntro v1.2 — Autonomous Intelligence Runtime

1. Objetivo

Elyntro es un sistema de inteligencia y ejecución de tareas que debe interpretar objetivos, planificar procesos, utilizar herramientas, trabajar con archivos, verificar resultados y recuperarse de errores.

No debe limitarse a generar respuestas de texto. Su objetivo es completar trabajos reales mediante un runtime modular, observable y verificable.

2. Arquitectura

El sistema se organiza en los siguientes módulos:

1. Cognitive Engine: interpretación de solicitudes, razonamiento, planificación y descomposición de objetivos.
2. Execution Runtime: ejecución de planes, gestión de estados, dependencias, límites y resultados.
3. Tool System: herramientas intercambiables para cálculos, archivos, investigación web y otras operaciones.
4. Model Provider Layer: interfaz independiente del proveedor de modelos, compatible con proveedores configurables.
5. Persistent Memory: memoria contextual y almacenamiento persistente con SQLite.
6. Verification Engine: evaluación de resultados mediante criterios explícitos y evidencias.
7. Recovery System: diagnóstico de errores, reparación de planes y reintentos controlados.
8. Security Layer: permisos, aislamiento de ejecución, límites de recursos y protección de secretos.
9. Artifact Manager: creación, almacenamiento y entrega de documentos, código y otros resultados.
10. Web Interface: conversaciones, historial, cuenta, archivos adjuntos, tareas y resultados.

3. Ciclo obligatorio de ejecución

Toda solicitud que requiera trabajo debe seguir un ciclo común:

1. Interpretar el objetivo y sus restricciones.
2. Determinar qué información y herramientas son necesarias.
3. Crear un plan estructurado con pasos y criterios de éxito.
4. Comprobar permisos y límites de ejecución.
5. Ejecutar los pasos y registrar sus resultados.
6. Validar los resultados con comprobaciones adecuadas.
7. Detectar errores, corregirlos y reintentar cuando sea seguro.
8. Guardar los resultados y los artefactos.
9. Informar qué se completó, qué se verificó y qué quedó pendiente.

Ningún endpoint debe saltarse este ciclo cuando la solicitud requiera ejecución verificable.

4. Correcciones prioritarias

* Establecer una única versión canónica del código.
* Integrar el motor cognitivo con el runtime web mediante interfaces explícitas.
* Eliminar rutas que respondan directamente y eviten las comprobaciones obligatorias cuando corresponda.
* Corregir la configuración de despliegue y eliminar dependencias de ZIP con nombres inconsistentes.
* Mejorar la persistencia SQLite, la gestión de concurrencia y los registros de auditoría.
* Unificar la configuración de proveedores y gestionar correctamente errores HTTP, cuotas, modelos y tiempos de espera.
* Separar la evaluación heurística de la verificación factual independiente.
* Evitar afirmar que una herramienta se ejecutó cuando solo se generó una respuesta textual.
* Añadir pruebas unitarias, de integración y de extremo a extremo.
* Documentar qué funciones están implementadas, cuáles están conectadas y cuáles siguen pendientes.

5. Multimodalidad y herramientas

La arquitectura debe permitir incorporar progresivamente:

* Lectura y análisis de documentos.
* Procesamiento de imágenes, audio y vídeo.
* Generación de imágenes y otros artefactos.
* Investigación web con fuentes y trazabilidad.
* Ejecución de código en un entorno aislado.
* Automatización de aplicaciones y navegador.
* Herramientas especializadas seleccionadas según la tarea.

Cada capacidad debe contar con una implementación real, permisos adecuados y pruebas propias. Aceptar un archivo no significa comprender su contenido; disponer de una interfaz no significa que la función esté operativa.

6. Verificación y seguridad

* No utilizar eval ni ejecutar código arbitrario sin aislamiento.
* No exponer claves API, credenciales ni información sensible en los registros.
* Aplicar límites de tiempo, memoria, tamaño de archivos y número de pasos.
* Distinguir entre éxito, fallo, resultado parcial y capacidad no configurada.
* Contrastar afirmaciones importantes con evidencias cuando la tarea lo requiera.
* No presentar una puntuación heurística como prueba objetiva de veracidad.
* Mantener registros auditables de herramientas, decisiones y resultados.

7. Despliegue

GitHub será la fuente canónica del código. Render alojará el servicio web y utilizará almacenamiento persistente cuando corresponda.

El despliegue deberá ser reproducible, sin extracciones manuales de paquetes durante la compilación, con dependencias declaradas, configuración mediante variables de entorno y un endpoint de salud.

8. Criterios de aceptación

La versión no se considerará terminada hasta comprobar:

* Instalación limpia desde el repositorio.
* Arranque y respuesta correcta del servicio.
* Persistencia de conversaciones y memoria después de reiniciar.
* Ejecución correcta de herramientas y registro de sus resultados.
* Gestión segura de errores y reintentos.
* Uso real del proveedor configurado.
* Ausencia de rutas que eviten las verificaciones obligatorias.
* Pruebas de integración del motor cognitivo con la aplicación web.
* Pruebas de permisos, límites y rechazo de operaciones inseguras.
* Funcionamiento de cada función anunciada en la interfaz.

9. Regla de desarrollo

No añadir funciones únicamente para aumentar el número de características declaradas. Priorizar integraciones funcionales, pruebas reproducibles y resultados verificables.

La v1.2 debe construirse sobre el código existente, conservar los módulos útiles y corregir los defectos identificados antes de añadir complejidad innecesaria.
