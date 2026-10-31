# Elyntro — nueva interfaz

Frontend independiente construido con React, TypeScript y Vite. Vive en `elyntro-new-ui/` para no interferir con el prototipo desplegado actualmente en Render.

## Publicar una vista previa en Vercel

1. En Vercel, pulsa **Add New → Project** e importa `JejeStudio-web/elyntro-prototype`.
2. Selecciona la rama `feature/new-elyntro-ui`.
3. En **Root Directory**, pulsa **Edit** y elige `elyntro-new-ui`.
4. Usa estos ajustes:
   - **Framework Preset:** Vite
   - **Build Command:** `npm run build`
   - **Output Directory:** `dist`
   - **Install Command:** `npm install`
5. En **Environment Variables**, añade las variables de abajo para el entorno **Preview**. Puedes dejar las de Supabase y n8n vacías inicialmente si solo quieres ver la interfaz; las funciones que dependen de esos servicios no funcionarán hasta configurarlas.
6. Pulsa **Deploy**. Vercel generará una URL de vista previa.

El archivo `vercel.json` contiene una regla de fallback para que las rutas de la aplicación de una sola página funcionen al recargarlas.

## Variables de entorno

- `VITE_SUPABASE_URL`: URL del proyecto Supabase.
- `VITE_SUPABASE_ANON_KEY`: clave pública anon/publishable; nunca uses `service_role`.
- `VITE_N8N_CHAT_WEBHOOK`: URL del webhook de chat.

No guardes claves privadas, tokens de Groq ni credenciales de servicio en variables `VITE_*`: se exponen al navegador.

## Integración actual y límites

- Incluye registro/inicio de sesión Supabase Auth, listado y creación de conversaciones, lectura de mensajes y una interfaz responsive.
- El envío al webhook incluye `conversation_id` y `message` en JSON y el token de Supabase en `Authorization: Bearer`. Hay que confirmar que el workflow de n8n use exactamente ese contrato y devuelva HTTP/JSON compatibles.
- El frontend evita insertar por adelantado el mensaje del usuario para no duplicarlo si n8n ya lo persiste.
- La compilación y la integración real aún deben probarse; no se consideran verificadas hasta ejecutar un despliegue y probar los flujos con las credenciales reales.

## Archivos

`src/App.tsx` interfaz y flujo; `src/lib/supabase.ts` cliente; `src/styles.css` estilos; `src/types.ts` tipos compartidos.
