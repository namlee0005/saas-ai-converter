import { render } from "preact";
import { useState, useCallback, useRef, useEffect } from "preact/hooks";
import { ChatBox } from "./ChatBox";

// ---------------------------------------------------------------------------
// Types matching Go gateway protocol (InboundMessage / OutboundMessage)
// ---------------------------------------------------------------------------

interface InboundMessage {
  type: "chat" | "ping";
  tenant_id: string;
  session_id: string;
  last_msg_id?: string;
  payload: unknown;
}

interface OutboundMessage {
  type: "token" | "high_intent" | "tool_result" | "done";
  msg_id: string;
  payload: unknown;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  id: string;
}

// ---------------------------------------------------------------------------
// Config read from the <script> tag's data attributes
// ---------------------------------------------------------------------------

interface WidgetConfig {
  tenantId: string;
  gatewayUrl: string;
  sessionToken: string;
  sessionId: string;
  position?: "bottom-right" | "bottom-left";
  primaryColor?: string;
}

function readConfig(): WidgetConfig | null {
  const el = document.querySelector<HTMLScriptElement>(
    "script[data-salesagent-tenant]",
  );
  if (!el) return null;

  const tenantId = el.dataset.salesagentTenant ?? "";
  const gatewayUrl = el.dataset.salesagentGateway ?? "";
  const sessionToken = el.dataset.salesagentToken ?? "";
  const sessionId = el.dataset.salesagentSession ?? "";

  if (!tenantId || !gatewayUrl || !sessionToken || !sessionId) return null;

  return {
    tenantId,
    gatewayUrl,
    sessionToken,
    sessionId,
    position: (el.dataset.salesagentPosition as WidgetConfig["position"]) ?? "bottom-right",
    primaryColor: el.dataset.salesagentColor ?? "#2563eb",
  };
}

// ---------------------------------------------------------------------------
// WebSocket hook — reconnects automatically
// ---------------------------------------------------------------------------

const MAX_RECONNECT_MS = 30_000;

function useWebSocket(config: WidgetConfig) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectDelay = useRef(1000);
  const lastMsgId = useRef("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const streamBuffer = useRef("");

  const connect = useCallback(() => {
    const url = new URL(config.gatewayUrl);
    url.pathname = "/ws";
    url.searchParams.set("token", config.sessionToken);
    const ws = new WebSocket(url.toString());
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      reconnectDelay.current = 1000;

      // If reconnecting, request replay from last seen ID
      if (lastMsgId.current) {
        const replay: InboundMessage = {
          type: "ping",
          tenant_id: config.tenantId,
          session_id: config.sessionId,
          last_msg_id: lastMsgId.current,
          payload: null,
        };
        ws.send(JSON.stringify(replay));
      }
    };

    ws.onmessage = (event: MessageEvent) => {
      try {
        const msg: OutboundMessage = JSON.parse(event.data as string);
        lastMsgId.current = msg.msg_id;

        if (msg.type === "token") {
          // Streaming token — append to current assistant message
          streamBuffer.current += msg.payload as string;
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.role === "assistant" && last.id === "streaming") {
              return [
                ...prev.slice(0, -1),
                { ...last, content: streamBuffer.current },
              ];
            }
            return [
              ...prev,
              { role: "assistant", content: streamBuffer.current, id: "streaming" },
            ];
          });
        } else if (msg.type === "done") {
          // Finalize the streamed message
          setMessages((prev) => {
            const last = prev[prev.length - 1];
            if (last?.id === "streaming") {
              return [
                ...prev.slice(0, -1),
                { ...last, id: msg.msg_id },
              ];
            }
            return prev;
          });
          streamBuffer.current = "";
        }
      } catch {
        // Malformed message — ignore silently, never break host site
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      // Exponential backoff reconnect
      const delay = Math.min(reconnectDelay.current, MAX_RECONNECT_MS);
      reconnectDelay.current = delay * 2;
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [config]);

  const sendMessage = useCallback(
    (text: string) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      const userMsg: ChatMessage = {
        role: "user",
        content: text,
        id: crypto.randomUUID(),
      };
      setMessages((prev) => [...prev, userMsg]);

      const outbound: InboundMessage = {
        type: "chat",
        tenant_id: config.tenantId,
        session_id: config.sessionId,
        payload: { text },
      };
      ws.send(JSON.stringify(outbound));
    },
    [config],
  );

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  return { messages, connected, connect, disconnect, sendMessage };
}

// ---------------------------------------------------------------------------
// Root widget component
// ---------------------------------------------------------------------------

function Widget({ config }: { config: WidgetConfig }) {
  const [open, setOpen] = useState(false);
  const { messages, connected, connect, disconnect, sendMessage } =
    useWebSocket(config);

  const handleToggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      if (next) connect();
      else disconnect();
      return next;
    });
  }, [connect, disconnect]);

  const isRight = config.position === "bottom-right";
  const positionStyle = isRight ? { right: "20px" } : { left: "20px" };

  return (
    <>
      {open && (
        <div
          style={{
            position: "fixed",
            bottom: "80px",
            ...positionStyle,
            zIndex: 2147483646,
            width: "380px",
            maxHeight: "560px",
            display: "flex",
            flexDirection: "column",
            borderRadius: "12px",
            overflow: "hidden",
            boxShadow: "0 8px 30px rgba(0,0,0,0.12)",
            fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          }}
        >
          <ChatBox
            messages={messages}
            connected={connected}
            onSend={sendMessage}
            onClose={handleToggle}
            primaryColor={config.primaryColor ?? "#2563eb"}
          />
        </div>
      )}

      {/* Floating trigger button */}
      <button
        type="button"
        onClick={handleToggle}
        aria-label={open ? "Close chat" : "Open chat"}
        style={{
          position: "fixed",
          bottom: "20px",
          ...positionStyle,
          zIndex: 2147483647,
          width: "56px",
          height: "56px",
          borderRadius: "50%",
          border: "none",
          background: config.primaryColor ?? "#2563eb",
          color: "#fff",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "0 4px 14px rgba(0,0,0,0.15)",
          transition: "transform 0.15s ease",
        }}
      >
        <svg
          width="24"
          height="24"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
        >
          {open ? (
            <path d="M18 6L6 18M6 6l12 12" />
          ) : (
            <>
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </>
          )}
        </svg>
      </button>
    </>
  );
}

// ---------------------------------------------------------------------------
// Mount into Shadow DOM for style isolation
// ---------------------------------------------------------------------------

function mount() {
  const config = readConfig();
  if (!config) {
    console.warn("[SalesAgent] Missing required data attributes on script tag.");
    return;
  }

  const host = document.createElement("div");
  host.id = "salesagent-widget";
  document.body.appendChild(host);

  const shadow = host.attachShadow({ mode: "open" });
  const container = document.createElement("div");
  shadow.appendChild(container);

  render(<Widget config={config} />, container);
}

// CSP-safe: no eval, no inline styles injected via JS strings
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mount);
} else {
  mount();
}
