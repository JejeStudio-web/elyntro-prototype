# Elyntro — nueva interfaz web

Este directorio está reservado para la nueva interfaz React + TypeScript, independiente del prototipo desplegado en Render. Se trabaja en la rama `feature/new-elyntro-ui`; no modifica `main` ni activa el flujo de n8n.

## Estado

La estructura de integración está pendiente de subir y verificar. El ZIP inicial generado en ChatGPT contiene el starter completo, pero esta rama todavía no debe considerarse lista para importar en Replit.

## Integración prevista

- React + TypeScript + Vite
- Supabase Auth y consultas protegidas por RLS
- API de chat n8n: `https://jejestudio.app.n8n.cloud/webhook/elyntro/operational-chat`
- No almacenar claves `service_role`, Groq ni secretos de n8n en el navegador
- No activar producción hasta probar autenticación, propiedad de conversaciones y persistencia
