import { useState, useRef, useEffect } from "preact/hooks";
import type { ChatMessage } from "./index";

interface ChatBoxProps {
  messages: ChatMessage[];
  connected: boolean;
  onSend: (text: string) => void;
  onClose: () => void;
  primaryColor: string;
}

export function ChatBox({
  messages,
  connected,
  onSend,
  onClose,
  primaryColor,
}: ChatBoxProps) {
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Focus input when chat opens
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSubmit = (e: Event) => {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    onSend(text);
    setInput("");
  };

  return (
    <>
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "14px 16px",
          background: primaryColor,
          color: "#fff",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <div
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: connected ? "#4ade80" : "#fbbf24",
            }}
            role="status"
            aria-label={connected ? "Connected" : "Connecting"}
          />
          <span style={{ fontWeight: 600, fontSize: "14px" }}>
            AI Sales Agent
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close chat"
          style={{
            background: "none",
            border: "none",
            color: "#fff",
            cursor: "pointer",
            padding: "4px",
            lineHeight: 1,
            fontSize: "18px",
          }}
        >
          &times;
        </button>
      </div>

      {/* Messages */}
      <div
        role="log"
        aria-live="polite"
        aria-label="Chat messages"
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 16px",
          background: "#fff",
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          minHeight: "300px",
          maxHeight: "400px",
        }}
      >
        {messages.length === 0 && (
          <div
            style={{
              textAlign: "center",
              color: "#9ca3af",
              fontSize: "13px",
              marginTop: "40px",
            }}
          >
            Hi! Ask me anything about our product.
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              display: "flex",
              justifyContent:
                msg.role === "user" ? "flex-end" : "flex-start",
            }}
          >
            <div
              style={{
                maxWidth: "80%",
                padding: "8px 12px",
                borderRadius:
                  msg.role === "user"
                    ? "12px 12px 2px 12px"
                    : "12px 12px 12px 2px",
                background:
                  msg.role === "user" ? primaryColor : "#f3f4f6",
                color: msg.role === "user" ? "#fff" : "#1f2937",
                fontSize: "13px",
                lineHeight: "1.5",
                wordBreak: "break-word",
              }}
            >
              {msg.content}
              {msg.id === "streaming" && (
                <span
                  style={{ opacity: 0.6 }}
                  aria-label="AI is typing"
                >
                  &#9612;
                </span>
              )}
            </div>
          </div>
        ))}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        style={{
          display: "flex",
          borderTop: "1px solid #e5e7eb",
          background: "#fff",
        }}
      >
        <input
          ref={inputRef}
          type="text"
          value={input}
          onInput={(e) =>
            setInput((e.target as HTMLInputElement).value)
          }
          placeholder={
            connected ? "Type a message..." : "Connecting..."
          }
          disabled={!connected}
          aria-label="Chat message input"
          style={{
            flex: 1,
            border: "none",
            outline: "none",
            padding: "12px 16px",
            fontSize: "13px",
            background: "transparent",
          }}
        />
        <button
          type="submit"
          disabled={!connected || !input.trim()}
          aria-label="Send message"
          style={{
            padding: "12px 16px",
            background: "none",
            border: "none",
            color:
              connected && input.trim() ? primaryColor : "#d1d5db",
            cursor:
              connected && input.trim() ? "pointer" : "default",
            fontWeight: 600,
            fontSize: "13px",
          }}
        >
          Send
        </button>
      </form>
    </>
  );
}
