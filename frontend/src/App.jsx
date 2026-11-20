import { useEffect, useRef, useState } from "react";

const API_URL = (
  window.__CONFIG__?.API_URL || import.meta.env.VITE_API_URL || "http://localhost:8000"
).replace(/\/$/, "");

const newSessionId = () => "s-" + Math.random().toString(36).slice(2, 10);

const SIMULATIONS = [
  ["", "No simulated failure"],
  ["key_1=429", "key_1 returns 429"],
  ["key_1=500", "key_1 returns 500"],
  ["key_1=429,key_2=500", "Both keys fail"],
  ["*=429", "All keys return 429"],
];

function describeError(status, body) {
  const detail = body?.detail;
  if (status === 503) return "All AI providers are unavailable right now. Please try again in a moment.";
  if (status === 422) {
    if (Array.isArray(detail)) {
      const field = detail[0]?.loc?.slice(-1)[0];
      if (field === "message") return "Your message must be between 1 and 4000 characters.";
      return "The request was not valid: " + (detail[0]?.msg || "check your input") + ".";
    }
    return "The request was not valid.";
  }
  return typeof detail === "string" ? detail : `Something went wrong (HTTP ${status}).`;
}

function Receipt({ meta }) {
  const cached = meta.resolution_source === "LLM_CACHE";
  const via = cached ? "served from cache" : `answered by ${meta.key_id}` +
    (meta.fallback_attempts ? ` after ${meta.fallback_attempts} failed key${meta.fallback_attempts > 1 ? "s" : ""}` : "");
  return (
    <dl className="receipt">
      <div><dt>Source</dt><dd><span className={"chip " + (cached ? "cache" : "live")} data-testid="meta-source">{meta.resolution_source}</span></dd></div>
      <div><dt>Model</dt><dd data-testid="meta-model">{meta.model}</dd></div>
      <div><dt>Latency</dt><dd data-testid="meta-latency">{meta.latency_ms} ms</dd></div>
      <div><dt>Input tokens</dt><dd data-testid="meta-input-tokens">{meta.input_tokens}</dd></div>
      <div><dt>Output tokens</dt><dd data-testid="meta-output-tokens">{meta.output_tokens}</dd></div>
      <p className="via">{via}</p>
    </dl>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [items, setItems] = useState([]); // {kind: user|assistant|error, text, meta}
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [items, busy]);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setItems((p) => [...p, { kind: "user", text: message }]);
    setBusy(true);
    try {
      const headers = { "Content-Type": "application/json" };
      if (simulate) headers["X-Simulate-Failure"] = simulate;
      const body = { session_id: sessionId, message };
      if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();
      const res = await fetch(`${API_URL}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setItems((p) => [...p, { kind: "error", text: describeError(res.status, data) }]);
      } else {
        setItems((p) => [...p, { kind: "assistant", text: data.reply, meta: data.metadata }]);
      }
    } catch {
      setItems((p) => [...p, { kind: "error", text: "Can't reach the server. Check your connection and try again." }]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setItems([]);
    setInput("");
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>Failover Chat</h1>
          <p className="sub">Session <code>{sessionId}</code></p>
        </div>
        <button className="ghost" onClick={newChat}>New chat</button>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label>
          System prompt
          <textarea data-testid="system-prompt-input" rows={2} value={systemPrompt}
            placeholder="Optional. Applies to this session, e.g. “Answer like a pirate.”"
            onChange={(e) => setSystemPrompt(e.target.value)} />
        </label>
        <label>
          Test failover on the next messages
          <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
            {SIMULATIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </details>

      <main aria-live="polite">
        {items.length === 0 && (
          <div className="empty">
            <p>Ask anything. Every reply shows where it came from and what it cost.</p>
            <p>Send the same first message in a new chat to see it served from cache.</p>
          </div>
        )}
        {items.map((it, i) =>
          it.kind === "user" ? (
            <div key={i} className="msg user" data-testid="user-message">{it.text}</div>
          ) : it.kind === "error" ? (
            <div key={i} className="msg error" role="alert" data-testid="error-message">{it.text}</div>
          ) : (
            <article key={i}
              className={"msg assistant " + (it.meta.resolution_source === "LLM_CACHE" ? "cache" : "live")}
              data-testid="assistant-message">
              <div className="text">{it.text}</div>
              <Receipt meta={it.meta} />
            </article>
          )
        )}
        {busy && <div className="msg pending">Thinking…</div>}
        <div ref={endRef} />
      </main>

      <footer>
        <textarea data-testid="chat-input" ref={inputRef} rows={1} value={input} maxLength={4000}
          placeholder="Write a message" aria-label="Message"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <button data-testid="send-button" onClick={send} disabled={busy || !input.trim()}>Send</button>
      </footer>
    </div>
  );
}
