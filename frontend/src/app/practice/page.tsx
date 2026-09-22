"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { AppShell } from "@/components/AppShell";
import { usePracticeCall } from "@/hooks/usePracticeCall";
import { API_URL } from "@/lib/api";

type BackendStatus = "checking" | "online" | "offline";

export default function PracticePage() {
  const [backend, setBackend] = useState<BackendStatus>("checking");
  const [backendMessage, setBackendMessage] = useState("");
  const [draft, setDraft] = useState("");
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const {
    status,
    error,
    messages,
    muted,
    start,
    stop,
    sendText,
    toggleMute,
    audioRef,
  } = usePracticeCall();

  useEffect(() => {
    let cancelled = false;

    async function ping() {
      try {
        const res = await fetch(`${API_URL}/`);
        const data = await res.json();
        if (!cancelled) {
          setBackend(res.ok ? "online" : "offline");
          setBackendMessage(data.message || "Backend reachable");
        }
      } catch {
        if (!cancelled) {
          setBackend("offline");
          setBackendMessage(`Cannot reach FastAPI at ${API_URL}`);
        }
      }
    }

    ping();
    const id = setInterval(ping, 15000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const el = transcriptRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  function onSend(e: FormEvent) {
    e.preventDefault();
    if (!draft.trim()) return;
    sendText(draft);
    setDraft("");
  }

  const live = status === "live";
  const connecting = status === "connecting";

  return (
    <AppShell
      active="practice"
      backend={backend}
      backendMessage={backendMessage}
    >
      <audio ref={audioRef} className="hidden" autoPlay />
      <section className="mt-12 grid flex-1 items-start gap-8 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="animate-rise-delay">
          <p className="mb-4 text-sm font-semibold tracking-[0.28em] text-volt uppercase">
            Train without calling
          </p>
          <h1 className="font-[family-name:var(--font-display)] text-5xl leading-[0.95] font-extrabold tracking-tight text-mist sm:text-6xl">
            Practice
            <span className="text-volt">Chat</span>
          </h1>
          <p className="mt-6 max-w-md text-lg leading-relaxed text-fog">
            Same voice agent as the phone call. Talk with your mic or type —
            Matt starts speaking first, just like a live outbound call.
          </p>

          <div className="mt-10 flex flex-wrap gap-3">
            {!live && !connecting && (
              <button
                type="button"
                onClick={start}
                disabled={backend === "offline"}
                className="rounded-xl bg-volt px-5 py-3.5 font-[family-name:var(--font-display)] text-base font-bold tracking-wide text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
              >
                Start practice call
              </button>
            )}
            {connecting && (
              <button
                type="button"
                disabled
                className="rounded-xl bg-volt px-5 py-3.5 font-[family-name:var(--font-display)] text-base font-bold tracking-wide text-ink opacity-70"
              >
                Connecting mic…
              </button>
            )}
            {live && (
              <>
                <button
                  type="button"
                  onClick={toggleMute}
                  className="rounded-xl border border-line bg-ink-soft px-5 py-3.5 font-semibold text-mist transition hover:border-volt/40"
                >
                  {muted ? "Unmute mic" : "Mute mic"}
                </button>
                <button
                  type="button"
                  onClick={() => stop("You ended the practice call.")}
                  className="rounded-xl border border-ember/40 bg-ember/15 px-5 py-3.5 font-semibold text-ember transition hover:bg-ember/25"
                >
                  End call
                </button>
              </>
            )}
          </div>

          <p className="mt-4 text-sm text-fog">
            {status === "idle" && "Browser mic + OpenAI Realtime. No Twilio."}
            {status === "connecting" && "Allow microphone access to continue."}
            {status === "live" &&
              (muted
                ? "Mic muted — you can still type."
                : "Live — speak or type. Interrupt anytime.")}
            {status === "ended" && "Call ended. Start again to train another round."}
            {status === "error" && "Could not start. Check backend and OpenAI key."}
          </p>

          {error && (
            <p className="mt-4 rounded-lg border border-ember/40 bg-ember/10 px-3 py-2 text-sm text-ember">
              {error}
            </p>
          )}
        </div>

        <div className="animate-rise-late flex min-h-[28rem] flex-col rounded-2xl border border-line bg-ink-soft/80 shadow-[0_24px_80px_rgba(0,0,0,0.35)] backdrop-blur-md">
          <div className="flex items-center justify-between border-b border-line px-5 py-4">
            <p className="font-semibold text-mist">Call transcript</p>
            <span
              className={`text-xs font-semibold tracking-wide uppercase ${
                live ? "text-volt" : "text-fog"
              }`}
            >
              {live ? "On the line" : status}
            </span>
          </div>

          <div ref={transcriptRef} className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
            {messages.length === 0 && (
              <p className="text-sm leading-relaxed text-fog">
                Start a practice call. The agent will greet you the same way it
                does on a real outbound call. Reply by speaking or typing.
              </p>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`max-w-[92%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "ml-auto bg-volt/15 text-mist"
                    : msg.role === "agent"
                      ? "bg-ink text-mist"
                      : "mx-auto text-center text-fog"
                }`}
              >
                {msg.role !== "system" && (
                  <p className="mb-1 text-[11px] font-semibold tracking-wide text-fog uppercase">
                    {msg.role === "user" ? "You" : "Matt"}
                  </p>
                )}
                <p>
                  {msg.text || (msg.streaming ? "…" : "")}
                  {msg.streaming ? "▍" : ""}
                </p>
              </div>
            ))}
          </div>

          <form
            onSubmit={onSend}
            className="flex gap-2 border-t border-line p-4"
          >
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              disabled={!live}
              placeholder={
                live ? "Type a reply if you don’t want to speak…" : "Start a practice call first"
              }
              className="flex-1 rounded-xl border border-line bg-ink px-4 py-3 text-sm text-mist outline-none placeholder:text-fog/50 focus:border-volt focus:ring-2 focus:ring-volt/30 disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!live || !draft.trim()}
              className="rounded-xl bg-volt px-4 py-3 text-sm font-bold text-ink disabled:cursor-not-allowed disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      </section>
    </AppShell>
  );
}
