export type ChatMessage = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
};
export type Conversation = { id: string; title: string; updated_at?: string };
