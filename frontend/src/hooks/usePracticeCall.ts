"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";

export type PracticeStatus =
  | "idle"
  | "connecting"
  | "live"
  | "ended"
  | "error";

export type ChatMessage = {
  id: string;
  role: "user" | "agent" | "system";
  text: string;
  streaming?: boolean;
};

type RealtimeEvent = {
  type: string;
  delta?: string;
  transcript?: string;
  name?: string;
  call_id?: string;
  error?: { message?: string } | string;
};

function newId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function usePracticeCall() {
  const [status, setStatus] = useState<PracticeStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [muted, setMuted] = useState(false);

  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dcRef = useRef<RTCDataChannel | null>(null);
  const micRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const agentMsgIdRef = useRef<string | null>(null);
  const userMsgIdRef = useRef<string | null>(null);

  const patchMessage = useCallback(
    (id: string, patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((msg) => (msg.id === id ? { ...msg, ...patch } : msg)),
      );
    },
    [],
  );

  const appendDelta = useCallback((id: string, delta: string) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === id ? { ...msg, text: `${msg.text}${delta}` } : msg,
      ),
    );
  }, []);

  const stop = useCallback((reason?: string) => {
    dcRef.current?.close();
    dcRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
    micRef.current?.getTracks().forEach((track) => track.stop());
    micRef.current = null;
    if (audioRef.current) {
      audioRef.current.srcObject = null;
    }
    agentMsgIdRef.current = null;
    userMsgIdRef.current = null;
    setMuted(false);
    setStatus((prev) => (prev === "connecting" ? "error" : "ended"));
    if (reason) {
      setMessages((prev) => [
        ...prev,
        { id: newId(), role: "system", text: reason },
      ]);
    }
  }, []);

  const handleEvent = useCallback(
    (event: RealtimeEvent) => {
      if (event.type === "response.output_audio_transcript.delta" && event.delta) {
        if (!agentMsgIdRef.current) {
          const id = newId();
          agentMsgIdRef.current = id;
          setMessages((prev) => [
            ...prev,
            { id, role: "agent", text: event.delta || "", streaming: true },
          ]);
        } else {
          appendDelta(agentMsgIdRef.current, event.delta);
        }
        return;
      }

      if (event.type === "response.output_audio_transcript.done") {
        if (agentMsgIdRef.current) {
          patchMessage(agentMsgIdRef.current, { streaming: false });
        }
        agentMsgIdRef.current = null;
        return;
      }

      if (
        event.type === "conversation.item.input_audio_transcription.delta" &&
        event.delta
      ) {
        if (!userMsgIdRef.current) {
          const id = newId();
          userMsgIdRef.current = id;
          setMessages((prev) => [
            ...prev,
            { id, role: "user", text: event.delta || "", streaming: true },
          ]);
        } else {
          appendDelta(userMsgIdRef.current, event.delta);
        }
        return;
      }

      if (
        event.type === "conversation.item.input_audio_transcription.completed"
      ) {
        const text = (event.transcript || "").trim();
        if (userMsgIdRef.current) {
          patchMessage(userMsgIdRef.current, {
            ...(text ? { text } : {}),
            streaming: false,
          });
          userMsgIdRef.current = null;
        } else if (text) {
          setMessages((prev) => [
            ...prev,
            { id: newId(), role: "user", text },
          ]);
        }
        return;
      }

      if (event.type === "input_audio_buffer.speech_started") {
        if (agentMsgIdRef.current) {
          patchMessage(agentMsgIdRef.current, { streaming: false });
          agentMsgIdRef.current = null;
        }
        return;
      }

      if (
        event.type === "response.function_call_arguments.done" &&
        event.name === "end_call"
      ) {
        const dc = dcRef.current;
        if (dc && dc.readyState === "open" && event.call_id) {
          dc.send(
            JSON.stringify({
              type: "conversation.item.create",
              item: {
                type: "function_call_output",
                call_id: event.call_id,
                output: JSON.stringify({
                  success: true,
                  reason: "practice_session_ended",
                }),
              },
            }),
          );
        }
        stop("Agent ended the call.");
        return;
      }

      if (event.type === "error") {
        const message =
          typeof event.error === "string"
            ? event.error
            : event.error?.message || "Realtime error";
        setError(message);
      }
    },
    [appendDelta, patchMessage, stop],
  );

  const start = useCallback(async () => {
    setError(null);
    setMessages([]);
    setStatus("connecting");

    try {
      const tokenRes = await fetch(`${API_URL}/practice/session`, {
        method: "POST",
      });
      const tokenData = await tokenRes.json();
      if (!tokenRes.ok) {
        throw new Error(tokenData.detail || "Could not start practice session");
      }

      const pc = new RTCPeerConnection();
      pcRef.current = pc;

      const audio = audioRef.current || document.createElement("audio");
      audio.autoplay = true;
      audioRef.current = audio;
      pc.ontrack = (evt) => {
        audio.srcObject = evt.streams[0];
      };

      const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
      micRef.current = mic;
      mic.getTracks().forEach((track) => pc.addTrack(track, mic));

      const dc = pc.createDataChannel("oai-events");
      dcRef.current = dc;
      dc.addEventListener("message", (evt) => {
        try {
          handleEvent(JSON.parse(evt.data) as RealtimeEvent);
        } catch {
          /* ignore malformed events */
        }
      });
      dc.addEventListener("open", () => {
        dc.send(
          JSON.stringify({
            type: "response.create",
            response: { output_modalities: ["audio"] },
          }),
        );
      });

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);

      const sdpRes = await fetch("https://api.openai.com/v1/realtime/calls", {
        method: "POST",
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${tokenData.value}`,
          "Content-Type": "application/sdp",
        },
      });
      if (!sdpRes.ok) {
        const body = await sdpRes.text();
        throw new Error(body || "OpenAI WebRTC handshake failed");
      }

      await pc.setRemoteDescription({
        type: "answer",
        sdp: await sdpRes.text(),
      });

      setStatus("live");
    } catch (err) {
      stop();
      setStatus("error");
      setError(err instanceof Error ? err.message : "Could not start practice");
    }
  }, [handleEvent, stop]);

  const sendText = useCallback((text: string) => {
    const trimmed = text.trim();
    const dc = dcRef.current;
    if (!trimmed || !dc || dc.readyState !== "open") return;

    setMessages((prev) => [
      ...prev,
      { id: newId(), role: "user", text: trimmed },
    ]);
    dc.send(
      JSON.stringify({
        type: "conversation.item.create",
        item: {
          type: "message",
          role: "user",
          content: [{ type: "input_text", text: trimmed }],
        },
      }),
    );
    dc.send(
      JSON.stringify({
        type: "response.create",
        response: { output_modalities: ["audio"] },
      }),
    );
  }, []);

  const toggleMute = useCallback(() => {
    const next = !muted;
    micRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMuted(next);
  }, [muted]);

  useEffect(() => {
    return () => {
      dcRef.current?.close();
      pcRef.current?.close();
      micRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return {
    status,
    error,
    messages,
    muted,
    start,
    stop,
    sendText,
    toggleMute,
    audioRef,
  };
}
