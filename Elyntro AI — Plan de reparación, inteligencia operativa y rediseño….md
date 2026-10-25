## Elyntro AI — Plan de reparación, inteligencia operativa y rediseño de interfaz  
**Proyecto:** Elyntro AI **Repositorio:** JejeStudio-web/elyntro-prototype **Servicio:** elyntro-prototype.onrender.com **Versión de referencia:** Prototype 1.1.0 · Engine 6.2.0 **Prioridad:** reparar el motor de ejecución antes de añadir nuevas funciones.  
   
⸻  
   
## 1. Objetivo general  
Transformar el prototipo de Elyntro en un sistema de trabajo con IA capaz de recibir objetivos, planificar procesos, ejecutar herramientas disponibles, gestionar errores, investigar con fuentes, producir resultados útiles y comprobar si las tareas se han completado correctamente.  
El proyecto debe avanzar en tres frentes coordinados:  
1. **Motor cognitivo:** planificación, selección de herramientas, ejecución, recuperación de errores y verificación.  
2. **Motor cognitivo:** planificación, selección de herramientas, ejecución, recuperación de errores y verificación.  
3. **Infraestructura de ejecución:** comunicación con los modelos, persistencia de resultados, diagnósticos y gestión de fallos.  
4. **Infraestructura de ejecución:** comunicación con los modelos, persistencia de resultados, diagnósticos y gestión de fallos.  
5. **Interfaz:** experiencia visual moderna, profesional y adaptable, con sesiones de trabajo, estados, resultados, fuentes y artefactos.  
6. **Interfaz:** experiencia visual moderna, profesional y adaptable, con sesiones de trabajo, estados, resultados, fuentes y artefactos.  
No se debe simular inteligencia mediante respuestas prefabricadas ni presentar como completada una tarea que no se ha ejecutado o verificado.  
## 2. Estado técnico conocido  
La aplicación ya dispone de varios componentes que deben inspeccionarse y reutilizarse antes de desarrollar sustitutos.  

| Componente | Estado conocido |
| ------------------------------ | -------------------------------------------------- |
| Aplicación web | Desplegada en Render |
| API de salud | /api/health responde correctamente |
| Endpoint de ejecución | /api/agent/execute |
| Endpoint alternativo | /api/execute |
| Motor | 6.2.0 |
| Proveedor de modelos | Groq configurado |
| Modelo configurado previamente | openai/gpt-oss-20b |
| Investigación externa | DuckDuckGo HTML, mejor esfuerzo |
| Planificador | RuleBasedPlanner |
| Ciclo cognitivo | CognitiveLoop |
| Ejecución de tareas | TaskRequest y ejecutores existentes |
| Herramientas conocidas | Investigación, análisis, creación y verificación |
| Persistencia de resultados | Artefactos asociados a las tareas |
| Error observado | HTTP 502 y mensaje de decisión inválida del modelo |
  
El archivo tool_runtime.py contiene una ruta que devuelve No se pudo obtener una decisión válida del modelo. cuando la respuesta del proveedor no incluye texto. Sin embargo, todavía no se ha confirmado que esa ruta sea la responsable del error de ejecución actual.  
**Regla fundamental:** no modificar un componente basándose únicamente en la similitud de un mensaje de error. Primero hay que identificar la ruta de código que realmente falla.  
   
⸻  
   
## PARTE I — REPARACIÓN DEL MOTOR  
## 3. Diagnóstico del error de ejecución  
**3.1. Reproducir el problema**  
Ejecutar una tarea sencilla y otra de investigación desde la interfaz real.  
Prueba A:  
Analiza las ventajas y desventajas de la energía solar. Utiliza fuentes fiables, compara la evidencia y presenta una conclusión con las URL de las fuentes consultadas.  
Prueba B:  
Resume en cinco puntos las ventajas de la energía solar utilizando la información que puedas recuperar.  
Registrar en cada prueba:  
* Código HTTP.  
* Identificador de tarea, si existe.  
* Ruta del endpoint.  
* Fase en la que falla.  
* Respuesta del proveedor de modelos.  
* Herramienta que estaba ejecutándose.  
* Excepción completa del servidor.  
* Artefactos creados antes del fallo.  
* Fuentes recuperadas.  
* Resultado de la verificación.  
La respuesta de la API debe permitir distinguir entre un error de autenticación, una respuesta vacía del modelo, un fallo de investigación, un error de planificación y una excepción interna.  
**3.2. Inspeccionar el recorrido completo**  
Seguir esta secuencia en el código:  
1. Ruta Flask /api/agent/execute.  
2. Ruta Flask /api/agent/execute.  
3. Ruta Flask /api/agent/execute.  
4. Función execute_unified().  
5. Función execute_unified().  
6. Función execute_unified().  
7. Función classify_request().  
8. Función classify_request().  
9. Función classify_request().  
10. Construcción de TaskRequest.  
11. Construcción de TaskRequest.  
12. Construcción de TaskRequest.  
13. Inicialización de CognitiveLoop.  
14. Inicialización de CognitiveLoop.  
15. Inicialización de CognitiveLoop.  
16. Método loop.run().  
17. Método loop.run().  
18. Método loop.run().  
19. Selección del plan.  
20. Ejecución del manejador correspondiente.  
21. Comunicación con Groq mediante _groq_messages().  
22. Verificación del resultado.  
23. Serialización de la respuesta HTTP.  
Inspeccionar también el ejecutor de tareas y cualquier mecanismo que convierta excepciones en HTTP 502.  
No crear una segunda ruta de ejecución si ya existe una implementación funcional. El objetivo es reparar y reforzar el recorrido actual.  
**3.3. Corregir el tratamiento de errores**  
Implementar un sistema uniforme de errores que:  
* Capture las excepciones esperables en los límites adecuados.  
* Registre la excepción completa en los logs del servidor.  
* Genere un identificador de error para relacionar logs y respuestas.  
* Distinga errores recuperables de errores definitivos.  
* Preserve los resultados parciales y artefactos creados.  
* Devuelva JSON consistente en los errores de la API.  
* Evite incluir claves, tokens, secretos o datos privados en los logs y las respuestas.  
* No oculte los errores convirtiéndolos en respuestas de éxito.  
Ejemplo de estructura de respuesta:  
```
{
  "ok": false,
  "status": "failed",
  "error": {
    "code": "MODEL_EMPTY_RESPONSE",
    "message": "El proveedor no devolvió contenido utilizable.",
    "stage": "planning",
    "retryable": true,
    "error_id": "identificador-generado"
  },
  "task_id": "identificador-de-tarea",
  "artifacts": [],
  "sources": []
}

```
Este JSON es un contrato propuesto, no una respuesta que deba copiarse literalmente en todos los casos. Adaptar la estructura a las convenciones existentes y mantener la compatibilidad con los clientes actuales.  
Usar códigos HTTP coherentes: por ejemplo, 400 para solicitudes inválidas, 502 para errores de un proveedor externo y 500 para fallos internos inesperados. No utilizar un único código para todos los errores.  
## 4. Robustecer el acceso a modelos  
Revisar _groq_messages() y cualquier otra función que contacte con proveedores de modelos.  
**Requisitos**  
* Comprobar que la clave de Groq está configurada sin mostrar su valor.  
* Verificar que el modelo seleccionado es compatible con la cuenta y la API.  
* Manejar errores HTTP, tiempos de espera, respuestas vacías, JSON inválido y estructuras inesperadas.  
* Comprobar que el contenido devuelto existe y es del tipo esperado.  
* Distinguir entre errores de autenticación, permisos, límites de uso, disponibilidad y formato.  
* Respetar los límites de tokens y tiempos de espera.  
* No asumir que todos los modelos admiten el mismo modo JSON.  
* No repetir indefinidamente una petición fallida.  
* Registrar proveedor, modelo, duración y categoría del error, sin guardar credenciales.  
**Recuperación de respuestas**  
Si una respuesta no es válida:  
1. Comprobar si el proveedor ha devuelto un error explícito.  
2. Validar el contenido antes de interpretarlo.  
3. Si procede, solicitar una única reparación del formato.  
4. Reintentar solamente ante errores recuperables.  
5. Si la operación sigue fallando, devolver un diagnóstico preciso.  
6. Preservar los resultados parciales que ya existan.  
Un modelo alternativo solo debe utilizarse si está configurado y disponible. No inventar proveedores ni asumir que existe acceso gratuito a otro modelo.  
## 5. Mejorar el planificador y el ciclo cognitivo  
El planificador actual se basa en reglas. Puede ser útil para tareas sencillas, pero no debe ser el único mecanismo de decisión en un sistema que pretende resolver problemas diversos.  
Implementar una arquitectura híbrida:  
* **Clasificador inicial:** identifica el objetivo y el tipo de trabajo.  
* **Planificador:** selecciona y ordena los pasos.  
* **Modelo de decisión:** propone planes o acciones cuando sea necesario.  
* **Validador:** comprueba que las acciones propuestas son válidas.  
* **Ejecutor:** ejecuta las herramientas autorizadas.  
* **Observador:** recoge resultados, errores y artefactos.  
* **Verificador:** comprueba los resultados frente al objetivo.  
* **Controlador:** decide si continuar, corregir, reintentar o detenerse.  
El sistema debe seleccionar dinámicamente entre investigación, análisis, creación y verificación. No debe depender exclusivamente de palabras clave.  
**Ciclo de ejecución**  
```
OBJETIVO
   ↓
Comprensión y clasificación
   ↓
Plan de trabajo
   ↓
Validación del plan
   ↓
Selección de herramienta
   ↓
Ejecución de la herramienta
   ↓
Evaluación del resultado
   ↓
¿Es suficiente?
   ├── No → corregir, replanificar o detenerse
   └── Sí → verificación final
                ↓
          Entrega de resultados

```
Cada paso debe tener un estado explícito: pending, running, succeeded, failed o skipped.  
**Contrato de acciones**  
Cuando el modelo proponga una acción, validar como mínimo:  
* Que la acción pertenezca a una lista permitida.  
* Que los argumentos cumplan el esquema de la herramienta.  
* Que no falten parámetros obligatorios.  
* Que la acción sea compatible con el estado actual de la tarea.  
* Que respete los límites de tiempo, intentos y recursos.  
* Que no pueda ejecutar operaciones no autorizadas.  
El modelo propone; el ejecutor valida y decide si la operación está permitida.  
## 6. Investigación con fuentes reales  
Revisar el flujo de investigación existente:  
* _research_bundle()  
* _web_search()  
* _fetch_page()  
* handle_research()  
* handle_analyze()  
**Requisitos funcionales**  
Cada fuente debe conservar, cuando estén disponibles:  
* Título.  
* URL.  
* Dominio.  
* Extracto recuperado.  
* Fecha de publicación, si existe.  
* Fecha de consulta.  
* Estado de recuperación.  
* Motivo por el que se ha descartado, si procede.  
Diferenciar claramente entre resultados de búsqueda y páginas cuyo contenido se ha recuperado correctamente.  
No considerar una lista de títulos o enlaces como evidencia suficiente para una síntesis extensa.  
Si ninguna fuente contiene información utilizable, el sistema debe indicarlo y evitar fabricar hechos o referencias.  
**Síntesis fundamentada**  
El análisis debe:  
1. Extraer afirmaciones concretas de las fuentes.  
2. Relacionar cada afirmación con una fuente identificable.  
3. Diferenciar hechos, inferencias y recomendaciones.  
4. Detectar contradicciones entre fuentes.  
5. Reconocer incertidumbres y limitaciones.  
6. Verificar que las citas correspondan a las fuentes utilizadas.  
No exigir únicamente que aparezca una URL en cualquier parte del texto. Una URL mencionada no demuestra que la afirmación esté respaldada por ella.  
Cuando sea posible, usar referencias estructuradas que vinculen cada afirmación con una fuente y un extracto recuperado.  
## 7. Ejecución verificable y artefactos  
Cada tarea debe generar un registro consultable que incluya:  
* Objetivo original.  
* Plan utilizado.  
* Pasos ejecutados.  
* Herramientas invocadas.  
* Resultados de cada paso.  
* Errores y reintentos.  
* Fuentes recuperadas.  
* Artefactos producidos.  
* Resultado de la verificación.  
* Estado final.  
Estados finales recomendados:  
* succeeded: objetivo completado y verificaciones superadas.  
* partial: se obtuvo un resultado útil, pero faltan partes.  
* failed: no se pudo completar la tarea.  
* cancelled: ejecución cancelada.  
No marcar una tarea como completada solo porque el modelo haya generado una respuesta.  
Los artefactos deben conservarse incluso cuando una fase posterior falle. Por ejemplo, una investigación válida no debe desaparecer porque la síntesis haya producido un error.  
## 8. Memoria y continuidad  
Separar tres tipos de información:  
1. **Contexto de sesión:** información temporal de la conversación actual.  
2. **Contexto de sesión:** información temporal de la conversación actual.  
3. **Memoria de proyecto:** decisiones, preferencias y datos que conviene reutilizar entre tareas.  
4. **Memoria de proyecto:** decisiones, preferencias y datos que conviene reutilizar entre tareas.  
5. **Memoria de ejecución:** resultados, artefactos y pasos de una tarea específica.  
6. **Memoria de ejecución:** resultados, artefactos y pasos de una tarea específica.  
La memoria debe ser consultable, actualizable y controlable. No se debe tratar toda respuesta generada por el modelo como un hecho confirmado.  
Guardar información con procedencia cuando corresponda y evitar que datos obsoletos se impongan a resultados recientes y verificados.  
   
⸻  
   
## PARTE II — REDISEÑO DE LA INTERFAZ  
## 9. Dirección visual  
La interfaz debe evolucionar desde un formulario técnico hacia un espacio de trabajo de IA.  
**Identidad visual**  
* Estilo premium, minimalista y tecnológico.  
* Fondo oscuro azul profundo, con alternativa clara futura.  
* Paneles diferenciados mediante superficies y bordes sutiles.  
* Acentos azul lavanda y verde turquesa.  
* Tipografía legible y jerarquía visual clara.  
* Iconos consistentes, sin depender de emojis decorativos.  
* Animaciones discretas para los cambios de estado.  
* Diseño adaptable a iPad, móvil y escritorio.  
* Espaciado generoso y controles accesibles.  
La interfaz debe ser visualmente atractiva sin sacrificar legibilidad, velocidad o funcionalidad.  
## 10. Estructura de la aplicación  
**Barra lateral**  
Debe incluir:  
* Identidad de Elyntro.  
* Botón de nueva tarea.  
* Asistente.  
* Investigación.  
* Análisis.  
* Creación.  
* Historial de tareas.  
* Artefactos.  
* Ajustes.  
No mostrar opciones que no estén implementadas. Las funciones futuras pueden aparecer como no disponibles, pero nunca deben fingir que ya funcionan.  
**Barra superior**  
Mostrar:  
* Nombre de la sección.  
* Estado de la conexión con el servidor.  
* Modelo y proveedor configurados, si el servidor los expone de forma segura.  
* Acceso a los ajustes de sesión.  
El indicador de conexión debe distinguir entre API accesible y modelo realmente disponible. Una respuesta correcta de /api/health no garantiza que Groq pueda completar una petición.  
**Área central**  
Incluir:  
* Presentación breve del sistema.  
* Ejemplos de tareas.  
* Campo de entrada amplio.  
* Botón principal de ejecución.  
* Mensajes de estado.  
* Resultados organizados.  
* Fuentes y artefactos.  
* Diagnósticos cuando falle una operación.  
**Entrada multimodal**  
Diseñar la zona de entrada para que pueda admitir, progresivamente:  
* Texto.  
* Imágenes.  
* Documentos.  
* Audio.  
* Vídeo.  
Cada tipo de archivo requiere validación, límites de tamaño, almacenamiento y una ruta de procesamiento compatible con el backend.  
**No mostrar una función como operativa hasta que el servidor pueda recibir el archivo, procesarlo y devolver un resultado real.**  
## 11. Experiencia de ejecución  
Al enviar una tarea, la interfaz debe mostrar etapas reales:  
1. Comprendiendo el objetivo.  
2. Preparando el plan.  
3. Ejecutando una herramienta.  
4. Analizando los resultados.  
5. Verificando la respuesta.  
6. Preparando la entrega.  
Los estados deben proceder del backend cuando exista soporte. No presentar una animación de progreso como si describiera operaciones reales.  
Si una etapa falla, mostrar:  
* Qué etapa falló.  
* Una explicación comprensible.  
* El código de error, si existe.  
* Qué resultados parciales se conservaron.  
* Si es posible reintentar.  
* Un identificador para consultar los logs.  
No exponer trazas internas completas, claves ni información sensible en la interfaz normal.  
## 12. Presentación de resultados  
Organizar las respuestas en secciones cuando el tipo de tarea lo permita:  
* Resumen.  
* Resultado principal.  
* Análisis.  
* Fuentes.  
* Artefactos.  
* Verificación.  
* Limitaciones.  
Permitir copiar resultados y abrir fuentes. Los archivos descargables deben proceder de artefactos reales generados por el backend.  
Para tareas largas, utilizar identificadores de tarea y, si la arquitectura lo permite, consulta de estado o actualizaciones periódicas. No mantener una petición HTTP abierta indefinidamente.  
## 13. Compatibilidad del frontend  
Antes de sustituir la interfaz:  
1. Revisar los endpoints existentes.  
2. Identificar el formato real de sus peticiones.  
3. Identificar el formato real de sus respuestas.  
4. Mantener compatibilidad con /api/agent/execute y /api/execute.  
5. Reutilizar los campos existentes cuando sea posible.  
6. Adaptar el frontend al contrato de API acordado.  
7. Gestionar respuestas no JSON y errores HTTP.  
8. Mostrar estados vacíos, errores y tiempos de espera.  
No asumir que el backend devuelve campos como message, answer, output o result sin comprobarlo.  
Si se cambia el contrato de la API, actualizar también los consumidores y las pruebas.  
   
⸻  
   
## PARTE III — SEGURIDAD, PRUEBAS Y CALIDAD  
## 14. Seguridad del motor  
Implementar estas protecciones antes de permitir operaciones autónomas amplias:  
* Lista de herramientas autorizadas.  
* Validación de argumentos.  
* Límites de ejecución y reintentos.  
* Límites de tamaño de entrada y salida.  
* Protección frente a solicitudes externas no autorizadas.  
* Gestión segura de secretos mediante variables de entorno.  
* Tratamiento de las páginas web como datos no confiables.  
* Separación entre instrucciones del sistema y contenido recuperado.  
* Confirmación humana para acciones externas sensibles o irreversibles.  
* Registro de las acciones realizadas.  
La autonomía debe estar limitada por permisos, no por la confianza en que el modelo siempre actuará correctamente.  
## 15. Pruebas obligatorias  
**Pruebas del proveedor**  
* Clave ausente.  
* Clave inválida.  
* Modelo no disponible.  
* Error HTTP 403.  
* Error de límite de uso.  
* Tiempo de espera.  
* Respuesta vacía.  
* JSON inválido.  
* Respuesta válida.  
**Pruebas de investigación**  
* Resultados de búsqueda válidos.  
* Ningún resultado.  
* Página inaccesible.  
* Extractos vacíos.  
* Fuentes duplicadas.  
* Fuentes contradictorias.  
* Fallo de una fuente con otras disponibles.  
**Pruebas del ciclo cognitivo**  
* Tarea sencilla.  
* Tarea de investigación.  
* Tarea con varias herramientas.  
* Error recuperable.  
* Error definitivo.  
* Reintento limitado.  
* Verificación fallida.  
* Conservación de resultados parciales.  
**Pruebas de interfaz**  
* Envío con botón.  
* Envío con teclado.  
* Bloqueo de envíos duplicados.  
* Respuestas lentas.  
* Errores HTTP.  
* Respuestas no JSON.  
* Pantalla de historial vacío.  
* Visualización de fuentes.  
* Visualización de artefactos.  
* Adaptación a iPad y móvil.  
**Criterios mínimos de aceptación**  
* /api/health sigue funcionando.  
* Ambos endpoints de ejecución mantienen el comportamiento esperado.  
* Los errores de Groq se diagnostican sin revelar secretos.  
* Una respuesta vacía no provoca un fallo opaco.  
* Los errores del ejecutor quedan registrados.  
* Los resultados parciales se conservan.  
* Las tareas fallidas no se marcan como completadas.  
* Las fuentes mostradas corresponden a datos realmente recuperados.  
* La interfaz funciona con éxito y con errores.  
* Las pruebas existentes continúan pasando.  
   
⸻  
   
## PARTE IV — ORDEN DE IMPLEMENTACIÓN  
## Fase 1. Estabilización  
1. Reproducir el HTTP 502.  
2. Identificar la excepción original.  
3. Corregir la ruta de ejecución que falla.  
4. Mejorar los errores de comunicación con Groq.  
5. Añadir pruebas de regresión.  
6. Desplegar y comprobar el resultado.  
**Resultado esperado:** las tareas válidas se ejecutan y los fallos se explican correctamente.  
## Fase 2. Ciclo cognitivo  
1. Formalizar el contrato de herramientas.  
2. Validar las decisiones del modelo.  
3. Mejorar el planificador híbrido.  
4. Añadir límites y recuperación de errores.  
5. Conservar estados y resultados parciales.  
6. Reforzar la verificación.  
**Resultado esperado:** Elyntro puede ejecutar tareas de varios pasos con control y trazabilidad.  
## Fase 3. Investigación y análisis  
1. Mejorar la recuperación de páginas.  
2. Estructurar las fuentes.  
3. Separar búsqueda y lectura de contenido.  
4. Vincular afirmaciones con evidencia.  
5. Detectar limitaciones y contradicciones.  
6. Probar investigaciones completas.  
**Resultado esperado:** las síntesis están fundamentadas en las fuentes recuperadas y las limitaciones son visibles.  
## Fase 4. Interfaz  
1. Rediseñar el sistema visual.  
2. Crear la estructura de navegación.  
3. Mejorar el compositor de tareas.  
4. Añadir estados de ejecución.  
5. Organizar resultados, fuentes y artefactos.  
6. Adaptar la interfaz a iPad y escritorio.  
7. Conectar cada control a una operación real.  
**Resultado esperado:** una interfaz profesional que refleje fielmente el estado del motor.  
## Fase 5. Capacidades ampliadas  
1. Memoria de proyecto.  
2. Historial persistente.  
3. Generación de documentos y otros artefactos.  
4. Procesamiento multimodal.  
5. Herramientas adicionales.  
6. Ejecución de tareas largas.  
7. Futuro sistema de uso del ordenador con permisos, observación y verificación.  
Estas capacidades deben incorporarse de manera incremental, probando cada módulo antes de depender de él.  
   
⸻  
   
## PARTE V — INSTRUCCIONES PARA EL AGENTE DE CÓDIGO  
## 16. Directiva de implementación  
Trabaja sobre el repositorio existente de Elyntro. No crees un prototipo independiente ni sustituyas el motor por una interfaz que simule resultados.  
Antes de modificar archivos:  
1. Inspecciona la estructura del repositorio.  
2. Lee app.py, el ciclo cognitivo, el planificador, los ejecutores, el runtime de herramientas y el sistema de persistencia.  
3. Identifica la ruta real que produce el error de ejecución.  
4. Comprueba el contrato actual de la API.  
5. Identifica los componentes que funcionan y deben conservarse.  
Después:  
1. Reproduce el error.  
2. Corrige la causa raíz.  
3. Implementa diagnósticos y pruebas.  
4. Refuerza el ciclo de planificación, ejecución y verificación.  
5. Rediseña la interfaz sin romper los endpoints.  
6. Ejecuta las pruebas y comprueba el despliegue.  
7. Documenta los cambios realizados.  
**Restricciones**  
* No borrar funcionalidades existentes sin justificarlo.  
* No reemplazar archivos completos cuando baste un cambio localizado.  
* No duplicar el sistema de ejecución.  
* No introducir dependencias innecesarias.  
* No cambiar el comando de arranque de Render sin una razón técnica comprobada.  
* No cambiar versiones de Python o dependencias sin comprobar compatibilidad.  
* No incluir claves de API en el código ni en GitHub.  
* No afirmar que una función está implementada si solo existe su interfaz.  
* No afirmar que una prueba ha pasado si no se ha ejecutado.  
* No resolver los errores únicamente ocultando excepciones.  
* No marcar una tarea como exitosa si la verificación ha fallado.  
**Entrega técnica**  
Al finalizar, proporciona:  
* Archivos modificados y motivo de cada cambio.  
* Causa raíz del error original.  
* Pruebas ejecutadas y resultados reales.  
* Cambios en el contrato de API, si los hay.  
* Variables de entorno nuevas, si son necesarias, sin valores secretos.  
* Instrucciones exactas de despliegue en Render.  
* Limitaciones que todavía existan.  
* Próxima mejora prioritaria.  
No fragmentes el proyecto en numerosos ZIP sin necesidad. Prioriza cambios coherentes, revisables y fáciles de mantener.  
   
⸻  
   
## 17. Definición de éxito  
Elyntro no será un sistema inteligente por tener una interfaz atractiva, utilizar un modelo potente o generar respuestas extensas.  
El progreso real se medirá por su capacidad para:  
* Comprender objetivos diversos.  
* Elegir procedimientos adecuados.  
* Utilizar herramientas reales.  
* Resolver errores sin entrar en bucles.  
* Aprovechar resultados parciales.  
* Fundamentar las conclusiones en evidencia.  
* Verificar el cumplimiento del objetivo.  
* Reconocer cuándo no puede completar una tarea.  
* Entregar artefactos útiles.  
* Mantener la seguridad y la trazabilidad.  
**Prioridad inmediata:** reparar y diagnosticar la ejecución actual antes de ampliar la autonomía. Después, mejorar el motor y la interfaz sobre una base estable.  
