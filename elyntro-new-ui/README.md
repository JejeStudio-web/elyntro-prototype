# Elyntro — nueva interfaz

Frontend independiente construido con React, TypeScript y Vite. Vive en una carpeta separada para no interferir con el prototipo desplegado actualmente.

## Ejecutar en Replit
1. Importa el repositorio de GitHub y selecciona la rama `feature/new-elyntro-ui`.
2. Configura `elyntro-new-ui` como directorio del proyecto.
3. Ejecuta `npm install` y `npm run dev`. Para comprobar compilación: `npm run build`.
4. Copia las variables de `.env.example` a Secrets/Environment Variables de Replit con los valores reales.

## Variables de entorno
- `VITE_SUPABASE_URL`: URL del proyecto Supabase.
- `VITE_SUPABASE_ANON_KEY`: clave pública anon/publishable; nunca `service_role`.
- `VITE_N8N_CHAT_WEBHOOK`: URL del webhook de chat.

## Integración actual y límites
- Incluye registro/inicio de sesión Supabase Auth, listado y creación de conversaciones, lectura de mensajes y una interfaz responsive.
- El envío al webhook incluye `conversation_id` y `message` en JSON y el token de Supabase en `Authorization: Bearer`. Hay que confirmar que el workflow de n8n use exactamente ese contrato y devuelva HTTP/JSON compatibles.
- El frontend evita insertar por adelantado el mensaje del usuario para no duplicarlo si n8n ya lo persiste.
- La integración real y la compilación aún deben probarse en Replit con las credenciales del proyecto; no se considera verificada hasta entonces.
- No guardes claves privadas, tokens de Groq ni credenciales de servicio en variables `VITE_*`: se exponen al navegador.

## Archivos
`src/App.tsx` interfaz y flujo; `src/lib/supabase.ts` cliente; `src/styles.css` estilos; `src/types.ts` tipos compartidos.
