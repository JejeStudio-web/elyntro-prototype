# Elyntro: contexto del proyecto

Fuente: lo que el creador ha contado en conversaciones anteriores. Lo no confirmado se marca como tal.

## Qué es
Elyntro es un proyecto de IA generalista, creado por el fundador (perfil no técnico, que delega la planificación técnica en Claude). Objetivo declarado: construir una IA de máxima calidad (inteligente e innovadora).

## Funciones y objetivos mencionados
- Chat con su propio agente de n8n (no Groq como sustituto). Agente: Elyntro_agent.
- Nueva interfaz React + TypeScript + Vite, con autenticación y persistencia en Supabase.
- "Founder Mode" (futuro): modificar Elyntro desde su propia interfaz, con permisos y reversión.
- Cerebro de la IA: máxima calidad al menor coste posible, sin gasto recurrente no medible; preferencia por modelos abiertos si es viable; sin infraestructura propia potente.
- No entrenar un modelo desde cero solo por ambición; sí analizar qué aportaría uno propio.

## Estado técnico conocido
- Repo: JejeStudio-web/elyntro-prototype (ramas: main, work/elyntro-audit-and-fix, feature/new-elyntro-ui; carpeta elyntro-new-ui).
- Interfaz: Vercel (elyntro-new-ui.vercel.app). Motor anterior: Flask + Gunicorn + SQLite en Render (elyntro-prototype.onrender.com).
- Variables: VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_N8N_CHAT_WEBHOOK. Webhook de chat: elyntro/operational-chat.
- Problema abierto: errores "conversation not found" y "error in workflow"; el flujo de prueba falló con Payment Required (sin créditos en n8n). Causa raíz sin confirmar del todo.
- Prioridad actual: conectar el agente de n8n.

## Reglas de trabajo del fundador
- Trabajar sobre el repositorio real en GitHub, sin Replit Agent.
- Cambios pequeños, revisables y reversibles en ramas; nada destructivo en producción sin autorización.
- No inventar resultados: distinguir lo comprobado de lo supuesto.
- Comparar alternativas y explicar motivos sin dar por hecha la terminología.
- Informe por iteración: diagnóstico, cambios, pruebas, seguridad, despliegue, pendientes.
- Nunca pedir contraseñas ni claves en el chat.
