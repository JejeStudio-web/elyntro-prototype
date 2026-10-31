import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Activity, ArrowDown, ArrowUpRight, Check, ChevronDown, CircleHelp, Command, FileText, Globe, LogOut, Menu, MessageSquare, Plus, Search, Send, Settings2, ShieldCheck, Sparkles, SquarePen, X } from "lucide-react";
import { getAccessToken, supabase, supabaseConfigured } from "./lib/supabase";
import type { ChatMessage, Conversation } from "./types";

const webhook = import.meta.env.VITE_N8N_CHAT_WEBHOOK as string | undefined;
const starterPrompts = [
  { title: "Desarrolla una idea", body: "Ayúdame a convertir una idea en un plan concreto, detectando riesgos y pasos que falten.", icon: Sparkles },
  { title: "Investiga un tema", body: "Investiga este tema, contrasta las fuentes y separa los hechos de las incertidumbres.", icon: Globe },
  { title: "Resuelve un problema", body: "Analiza el problema, compara las opciones y recomienda la solución más sólida.", icon: Activity },
  { title: "Crea algo nuevo", body: "Trabaja conmigo para diseñar y construir algo útil desde cero.", icon: FileText }
];

export default function App() {
  const [session, setSession] = useState<any>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "signup">("login");
  const [authError, setAuthError] = useState("");
  const [authBusy, setAuthBusy] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [search, setSearch] = useState("");
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    if (!supabase) return;
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  useEffect(() => {
    if (session) void loadConversations();
    else { setConversations([]); setMessages([]); setActiveId(""); }
  }, [session]);

  useEffect(() => {
    if (session && activeId) void loadMessages(activeId);
    else setMessages([]);
  }, [session, activeId]);

  async function loadConversations() {
    if (!supabase) return;
    const { data, error } = await supabase.from("conversations").select("id,title,updated_at").order("updated_at", { ascending: false }).limit(60);
    if (error) { setNotice("No se pudieron cargar las conversaciones: " + error.message); return; }
    const rows = (data ?? []) as Conversation[];
    setConversations(rows);
    if (!activeId && rows[0]) setActiveId(rows[0].id);
  }

  async function loadMessages(id: string) {
    if (!supabase) return;
    const { data, error } = await supabase.from("messages").select("id,conversation_id,role,content,created_at").eq("conversation_id", id).order("created_at", { ascending: true }).limit(200);
    if (error) { setNotice("No se pudieron cargar los mensajes: " + error.message); return; }
    setMessages((data ?? []) as ChatMessage[]);
  }

  async function authenticate(event: FormEvent) {
    event.preventDefault();
    if (!supabase) return;
    setAuthBusy(true); setAuthError("");
    const result = authMode === "login"
      ? await supabase.auth.signInWithPassword({ email, password })
      : await supabase.auth.signUp({ email, password });
    if (result.error) setAuthError(result.error.message);
    else if (authMode === "signup" && !result.data.session) setAuthError("Revisa tu correo para confirmar la cuenta.");
    setAuthBusy(false);
  }

  async function newConversation() {
    if (!supabase || !session) return;
    const { data, error } = await supabase.from("conversations").insert({ user_id: session.user.id, title: "Nueva conversación" }).select("id,title,updated_at").single();
    if (error) { setNotice("No se pudo crear la conversación: " + error.message); return; }
    const conversation = data as Conversation;
    setConversations(current => [conversation, ...current]);
    setActiveId(conversation.id); setMessages([]); setNotice("");
  }

  async function sendMessage(text = draft) {
    const content = text.trim();
    if (!content || busy || !activeId) return;
    if (!webhook) { setNotice("Falta configurar VITE_N8N_CHAT_WEBHOOK en los secretos del proyecto."); return; }
    const token = await getAccessToken();
    if (!token) { setNotice("Tu sesión ha caducado. Vuelve a iniciar sesión."); return; }
    setDraft(""); setBusy(true); setNotice("");
    const optimistic: ChatMessage = { id: "pending-" + Date.now(), conversation_id: activeId, role: "user", content };
    setMessages(current => [...current, optimistic]);
    try {
      const response = await fetch(webhook, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
        body: JSON.stringify({ conversation_id: activeId, message: content })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || payload.message || "El servicio respondió con HTTP " + response.status);
      await loadMessages(activeId);
      await loadConversations();
      if (payload.reply && supabase) {
        const { data } = await supabase.from("messages").select("id,conversation_id,role,content,created_at").eq("conversation_id", activeId).order("created_at", { ascending: true }).limit(200);
        if (data) setMessages(data as ChatMessage[]);
      }
    } catch (error) {
      setMessages(current => current.filter(item => item.id !== optimistic.id));
      setNotice(error instanceof Error ? error.message : "No se pudo enviar el mensaje.");
    } finally { setBusy(false); }
  }

  const filtered = useMemo(() => conversations.filter(c => c.title.toLowerCase().includes(search.toLowerCase())), [conversations, search]);
  const active = conversations.find(c => c.id === activeId);

  if (!supabaseConfigured || !supabase) return <div className="setup-screen"><div className="brand-mark"><Sparkles size={21} /></div><p className="eyebrow">ELYNTRO / CONFIGURACIÓN</p><h1>Tu espacio empieza aquí.</h1><p className="muted">La interfaz está preparada. Añade las variables públicas de Supabase para habilitar las cuentas y las conversaciones.</p><div className="setup-card"><code>VITE_SUPABASE_URL</code><code>VITE_SUPABASE_ANON_KEY</code><code>VITE_N8N_CHAT_WEBHOOK</code><p>Usa únicamente la clave pública anon/publishable en el navegador. Nunca introduzcas claves service_role o secretos privados aquí.</p></div><p className="tiny">Configuración pendiente · No se ha conectado ningún servicio.</p></div>;

  if (!session) return <main className="auth-screen"><div className="auth-brand"><div className="brand-mark"><Sparkles size={21} /></div><span>Elyntro</span></div><section className="auth-card"><p className="eyebrow">TU ESPACIO DE TRABAJO</p><h1>{authMode === "login" ? "Piensa más lejos." : "Empieza con Elyntro."}</h1><p className="muted">Un espacio para explorar ideas y convertirlas en acciones.</p><form onSubmit={authenticate}><label>Correo electrónico<input type="email" autoComplete="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="tu@correo.com" /></label><label>Contraseña<input type="password" autoComplete={authMode === "login" ? "current-password" : "new-password"} value={password} onChange={e => setPassword(e.target.value)} minLength={6} required placeholder="Mínimo 6 caracteres" /></label>{authError && <p className="error-text">{authError}</p>}<button className="primary wide" disabled={authBusy}>{authBusy ? "Procesando…" : authMode === "login" ? "Iniciar sesión" : "Crear cuenta"} <ArrowUpRight size={17} /></button></form><p className="auth-switch">{authMode === "login" ? "¿Primera vez aquí?" : "¿Ya tienes cuenta?"} <button className="text-button" onClick={() => { setAuthMode(authMode === "login" ? "signup" : "login"); setAuthError(""); }}>{authMode === "login" ? "Crear cuenta" : "Iniciar sesión"}</button></p></section><p className="tiny auth-foot">Tus datos se gestionan con tu cuenta y las políticas de acceso de Supabase.</p></main>;

  return <div className={"app-shell " + (!sidebarOpen ? "sidebar-collapsed" : "")}>
    <aside className="sidebar"><div className="sidebar-top"><div className="brand-mark small"><Sparkles size={17} /></div><span className="brand-name">elyntro</span><button className="icon-button sidebar-toggle" aria-label="Ocultar panel" onClick={() => setSidebarOpen(false)}><Menu size={18} /></button></div>
      <button className="new-chat" onClick={newConversation}><SquarePen size={17} /><span>Nueva conversación</span><span className="shortcut">⌘ K</span></button>
      <div className="search-box"><Search size={15} /><input placeholder="Buscar conversaciones" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="section-label">RECIENTES <span>{filtered.length}</span></div>
      <nav className="conversation-list">{filtered.map(c => <button key={c.id} className={"conversation-item " + (c.id === activeId ? "active" : "")} onClick={() => setActiveId(c.id)}><MessageSquare size={15} /><span>{c.title || "Nueva conversación"}</span></button>)}
      {filtered.length === 0 && <p className="empty-sidebar">{search ? "No hay coincidencias." : "Tus conversaciones aparecerán aquí."}</p>}</nav>
      <div className="sidebar-bottom"><button className="side-link" onClick={() => setShowSettings(true)}><Settings2 size={17} />Configuración</button><button className="side-link" onClick={() => setNotice("La ayuda se incorporará en una siguiente iteración.")}><CircleHelp size={17} />Ayuda</button><div className="profile-row"><div className="avatar">{(session.user.email || "E").slice(0,1).toUpperCase()}</div><div className="profile-copy"><strong>{session.user.email?.split("@")[0]}</strong><span>Cuenta personal</span></div><button className="icon-button" aria-label="Cerrar sesión" onClick={() => supabase.auth.signOut()}><LogOut size={16} /></button></div></div>
    </aside>
    {!sidebarOpen && <button className="floating-menu icon-button" onClick={() => setSidebarOpen(true)} aria-label="Mostrar panel"><Menu size={19} /></button>}
    <main className="workspace"><header className="topbar"><div className="breadcrumbs"><span>Espacio de trabajo</span><ChevronDown size={14} /><strong>{active?.title || "Elyntro"}</strong></div><div className="topbar-right"><span className="secure-status"><ShieldCheck size={14} /> Sesión segura</span><button className="icon-button" onClick={() => setShowSettings(true)} aria-label="Configuración"><Settings2 size={17} /></button></div></header>
      <section className={"chat-area " + (messages.length ? "has-messages" : "")}>{messages.length === 0 ? <div className="welcome"><div className="welcome-symbol"><Sparkles size={25} /></div><p className="eyebrow">UN ESPACIO PARA PENSAR MEJOR</p><h1>¿Qué quieres <span>hacer posible?</span></h1><p className="welcome-sub">Piensa en voz alta. Elyntro te ayudará a ordenar las ideas y encontrar el siguiente paso.</p><div className="prompt-grid">{starterPrompts.map(({title,body,icon:Icon}) => <button key={title} className="prompt-card" onClick={() => { setDraft(body); }}><span className="prompt-icon"><Icon size={18} /></span><strong>{title}</strong><span>{body}</span><ArrowUpRight className="prompt-arrow" size={16} /></button>)}</div></div> : <div className="message-stack">{messages.map(m => <article key={m.id} className={"message " + (m.role === "user" ? "user-message" : "assistant-message")}><div className="message-avatar">{m.role === "user" ? (session.user.email || "T").slice(0,1).toUpperCase() : <Sparkles size={16} />}</div><div className="message-body"><div className="message-author">{m.role === "user" ? "Tú" : "Elyntro"}</div><div className="message-content">{m.content}</div></div></article>)}{busy && <article className="message assistant-message"><div className="message-avatar"><Sparkles size={16} /></div><div className="message-body"><div className="message-author">Elyntro</div><div className="typing"><i></i><i></i><i></i><span>Preparando respuesta</span></div></div></article>}</div>}</section>
      <div className="composer-wrap">{notice && <div className="notice"><span>{notice}</span><button className="icon-button" onClick={() => setNotice("")} aria-label="Cerrar aviso"><X size={15} /></button></div>}<form className="composer" onSubmit={e => { e.preventDefault(); void sendMessage(); }}><textarea value={draft} onChange={e => setDraft(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendMessage(); } }} placeholder="Escribe lo que tienes en mente…" rows={2} disabled={!activeId || busy} /><div className="composer-tools"><div className="composer-hint"><span className="hint-dot"></span>{webhook ? "Conector preparado" : "Conector pendiente"} <span className="hint-divider">·</span> <span>Mayús + Intro para nueva línea</span></div><button className="send-button" disabled={!draft.trim() || busy || !activeId} aria-label="Enviar mensaje">{busy ? <Activity size={17} className="spin" /> : <Send size={17} />}</button></div></form><p className="disclaimer">Elyntro puede equivocarse. Verifica la información importante.</p></div>
    </main>
    {showSettings && <div className="modal-backdrop" onClick={() => setShowSettings(false)}><section className="settings-modal" onClick={e => e.stopPropagation()}><div className="modal-heading"><div><p className="eyebrow">PREFERENCIAS</p><h2>Configuración</h2></div><button className="icon-button" onClick={() => setShowSettings(false)} aria-label="Cerrar"><X size={18} /></button></div><div className="setting-line"><div><strong>Cuenta</strong><p>{session.user.email}</p></div><Check size={17} /></div><div className="setting-line"><div><strong>Base de datos</strong><p>Supabase · {supabaseConfigured ? "Variables detectadas" : "Sin configurar"}</p></div><span className="status-pill">Cliente</span></div><div className="setting-line"><div><strong>Agente</strong><p>Webhook de n8n · {webhook ? "URL configurada" : "Pendiente de configurar"}</p></div><span className="status-pill">{webhook ? "Preparado" : "Pendiente"}</span></div><p className="tiny">Esta pantalla no cambia credenciales ni permisos. Configura las variables en el entorno de despliegue.</p></section></div>}
  </div>;
}
