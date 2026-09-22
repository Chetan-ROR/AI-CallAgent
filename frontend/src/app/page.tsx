"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/AppShell";
import { API_URL } from "@/lib/api";

type BackendStatus = "checking" | "online" | "offline";

type CallResult = {
  status: string;
  call_sid: string;
  to: string;
};

export default function Home() {
  const [phone, setPhone] = useState("+91");
  const [backend, setBackend] = useState<BackendStatus>("checking");
  const [backendMessage, setBackendMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CallResult | null>(null);

  const [prompt, setPrompt] = useState("");
  const [promptLoading, setPromptLoading] = useState(true);
  const [promptSaving, setPromptSaving] = useState(false);
  const [promptMsg, setPromptMsg] = useState<string | null>(null);
  const [promptErr, setPromptErr] = useState<string | null>(null);

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
    let cancelled = false;

    async function loadPrompt() {
      setPromptLoading(true);
      try {
        const res = await fetch(`${API_URL}/prompt`);
        const data = await res.json();
        if (!cancelled && res.ok) {
          setPrompt(data.instructions || "");
        }
      } catch {
        if (!cancelled) {
          setPromptErr("Could not load prompt");
        }
      } finally {
        if (!cancelled) setPromptLoading(false);
      }
    }

    loadPrompt();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/make-call`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: phone.trim() }),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Call failed");
      }

      setResult(data as CallResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  async function onSavePrompt(e: FormEvent) {
    e.preventDefault();
    setPromptMsg(null);
    setPromptErr(null);
    setPromptSaving(true);

    try {
      const res = await fetch(`${API_URL}/prompt`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instructions: prompt }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Save failed");
      }
      setPrompt(data.instructions);
      setPromptMsg("Prompt saved — next call or practice session will use it");
    } catch (err) {
      setPromptErr(err instanceof Error ? err.message : "Save failed");
    } finally {
      setPromptSaving(false);
    }
  }

  return (
    <AppShell active="call" backend={backend} backendMessage={backendMessage}>
      <section className="mt-16 grid flex-1 items-start gap-12 lg:mt-20 lg:grid-cols-[1.15fr_0.85fr]">
        <div className="animate-rise-delay">
          <p className="mb-4 text-sm font-semibold tracking-[0.28em] text-volt uppercase">
            Gym floor · Voice AI
          </p>
          <h1 className="font-[family-name:var(--font-display)] text-5xl leading-[0.95] font-extrabold tracking-tight text-mist sm:text-7xl">
            Call
            <span className="text-volt">Agent</span>
          </h1>
          <p className="mt-6 max-w-md text-lg leading-relaxed text-fog">
            Dial a member and let the AI receptionist pick up the floor —
            trials, hours, and membership questions on the line. Train the
            same script first on{" "}
            <Link href="/practice" className="text-volt underline-offset-4 hover:underline">
              Practice chat
            </Link>
            , without placing a real call.
          </p>

          <form
            onSubmit={onSavePrompt}
            className="mt-10 rounded-2xl border border-line bg-ink-soft/80 p-6 backdrop-blur-md sm:p-7"
          >
            <label
              htmlFor="prompt"
              className="block text-sm font-semibold tracking-wide text-mist"
            >
              Agent prompt
            </label>
            <p className="mt-1 text-sm text-fog">
              Edit here — saved prompt is used on the next live or practice call.
            </p>
            <textarea
              id="prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={promptLoading || backend === "offline"}
              rows={8}
              required
              className="mt-4 w-full resize-y rounded-xl border border-line bg-ink px-4 py-3.5 font-medium leading-relaxed text-mist outline-none transition placeholder:text-fog/50 focus:border-volt focus:ring-2 focus:ring-volt/30 disabled:opacity-50"
              placeholder={
                promptLoading ? "Loading prompt…" : "Write agent instructions…"
              }
            />
            <button
              type="submit"
              disabled={
                promptSaving || promptLoading || backend === "offline" || !prompt.trim()
              }
              className="mt-4 rounded-xl border border-volt/40 bg-volt/15 px-5 py-3 font-[family-name:var(--font-display)] text-sm font-bold tracking-wide text-volt transition hover:bg-volt/25 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {promptSaving ? "Saving…" : "Save prompt"}
            </button>
            {promptMsg && (
              <p className="mt-3 text-sm text-volt">{promptMsg}</p>
            )}
            {promptErr && (
              <p className="mt-3 text-sm text-ember">{promptErr}</p>
            )}
          </form>
        </div>

        <form
          onSubmit={onSubmit}
          className="animate-rise-late rounded-2xl border border-line bg-ink-soft/80 p-6 shadow-[0_24px_80px_rgba(0,0,0,0.35)] backdrop-blur-md sm:p-8"
        >
          <label
            htmlFor="phone"
            className="block text-sm font-semibold tracking-wide text-mist"
          >
            Member number
          </label>
          <p className="mt-1 text-sm text-fog">E.164 format, e.g. +919876543210</p>
          <input
            id="phone"
            type="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="+919876543210"
            required
            className="mt-4 w-full rounded-xl border border-line bg-ink px-4 py-3.5 font-medium text-mist outline-none transition placeholder:text-fog/50 focus:border-volt focus:ring-2 focus:ring-volt/30"
          />

          <button
            type="submit"
            disabled={loading || backend === "offline"}
            className="group mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-volt px-5 py-3.5 font-[family-name:var(--font-display)] text-base font-bold tracking-wide text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Connecting…" : "Place AI call"}
            <span
              aria-hidden
              className="transition-transform group-hover:translate-x-0.5"
            >
              →
            </span>
          </button>

          {error && (
            <p className="mt-4 rounded-lg border border-ember/40 bg-ember/10 px-3 py-2 text-sm text-ember">
              {error}
            </p>
          )}

          {result && (
            <div className="mt-4 space-y-1 rounded-lg border border-volt/30 bg-volt/10 px-3 py-3 text-sm text-mist">
              <p className="font-semibold text-volt">{result.status}</p>
              <p className="text-fog">To: {result.to}</p>
              <p className="break-all font-mono text-xs text-fog">
                SID: {result.call_sid}
              </p>
            </div>
          )}
        </form>
      </section>
    </AppShell>
  );
}
