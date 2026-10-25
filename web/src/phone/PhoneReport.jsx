import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { connectHub } from "../data/hubConnection.ts";
import { decodeToWav } from "./audio.js";
import "./phone.css";

const HUB = import.meta.env.VITE_STDB_URI || "http://127.0.0.1:3017";
const DB = import.meta.env.VITE_STDB_DB || "phase-e-demo";
const storageKey = "ember-report-session-v1";
async function api(path, body, session) {
  const multipart = body instanceof FormData;
  const response = await fetch(path, {
    method: "POST",
    headers: {
      ...(multipart ? {} : { "Content-Type": "application/json" }),
      ...(session ? { Authorization: `Bearer ${session.report_session}` } : {}),
    },
    body: multipart ? body : JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || "Request unavailable");
  return result;
}
function observedTime(clock, connected) {
  if (!clock || !connected) throw Error("Wait for the shared replay clock");
  return Math.round(
    Math.min(
      clock.max_sim_ms,
      Math.max(
        clock.min_sim_ms,
        clock.anchor_sim_ms +
          (clock.playing
            ? (Date.now() - clock.anchor_wall_ms) * clock.speed
            : 0),
      ),
    ),
  );
}
export default function PhoneReport() {
  const [session, setSession] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey));
      return saved?.expires_wall_ms > Date.now() ? saved : null;
    } catch {
      return null;
    }
  });
  const [rows, setRows] = useState({}),
    [connected, setConnected] = useState(false);
  const [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [reportId, setReportId] = useState(null);
  const [recording, setRecording] = useState(false),
    connection = useRef(null),
    recorder = useRef(null),
    tracks = useRef(null),
    timer = useRef(null),
    cancelled = useRef(false);
  useEffect(() => {
    if (!session) return;
    const hub = connectHub(
      HUB,
      DB,
      session.scenario_id,
      setRows,
      () => {
        setConnected(false);
        setMessage("Connection interrupted. Reload to reconnect.");
      },
      () => setConnected(true),
    );
    connection.current = hub;
    return () => {
      hub.close();
      connection.current = null;
    };
  }, [session]);
  useEffect(
    () => () => {
      cancelled.current = true;
      clearTimeout(timer.current);
      tracks.current?.getTracks().forEach((t) => t.stop());
    },
    [],
  );
  const person = rows.person?.find((p) => p.person_id === session?.person_id);
  const clock = rows.clock?.[0];
  async function join() {
    setBusy(true);
    try {
      const result = await api("/api/join", {
        join_code: new URLSearchParams(location.search).get("j") || "demo",
        label: "Participant",
      });
      sessionStorage.setItem(storageKey, JSON.stringify(result));
      setSession(result);
      setMessage("Joined at an assigned location.");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }
  async function tap(kind) {
    if (!person || !connected) return;
    setBusy(true);
    try {
      const result = await api(
        "/api/report-tap",
        {
          request_id: crypto.randomUUID(),
          kind,
          lon: person.lon,
          lat: person.lat,
          observed_sim_ms: observedTime(clock, connected),
        },
        session,
      );
      setReportId(result.report_id);
      setMessage(result.readback);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }
  async function voice() {
    if (recording) {
      recorder.current?.stop();
      return;
    }
    if (!person || !connected) return;
    setBusy(true);
    try {
      cancelled.current = false;
      const observed = observedTime(clock, connected),
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      tracks.current = stream;
      const chunks = [],
        capture = new MediaRecorder(stream);
      recorder.current = capture;
      capture.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      capture.onstop = async () => {
        clearTimeout(timer.current);
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        if (cancelled.current) {
          setBusy(false);
          return;
        }
        try {
          const data = new FormData();
          data.set(
            "audio",
            await decodeToWav(new Blob(chunks, { type: capture.mimeType })),
            "report.wav",
          );
          data.set("request_id", crypto.randomUUID());
          data.set("observed_sim_ms", String(observed));
          const result = await api("/api/report-voice", data, session);
          setReportId(result.report_id);
          setMessage(result.readback);
        } catch (error) {
          setMessage(error.message);
        } finally {
          setBusy(false);
        }
      };
      capture.start();
      setRecording(true);
      setMessage("Recording. Tap stop when finished; maximum 15 seconds.");
      timer.current = setTimeout(() => {
        if (capture.state === "recording") capture.stop();
      }, 14500);
    } catch (error) {
      tracks.current?.getTracks().forEach((t) => t.stop());
      setMessage(error.message);
      setBusy(false);
    }
  }
  const status = rows.report?.find((r) => r.report_id === reportId);
  const used = rows.observation?.find(
    (o) => o.obs_id === status?.observation_id && o.status === "assimilated",
  );
  return (
    <main className="phone-report">
      <header>
        <Link to="/">Ember</Link>
        <span>January 2025</span>
      </header>
      <h1>Report what you see</h1>
      {!session ? (
        <section>
          <p>
            You’ll join at an assigned location for this research prototype.
          </p>
          <p>
            Tap reports are stored for this session. If you use voice, audio
            goes to xAI for transcription and text to Anthropic for
            classification. A private transcript stays in the session hub until
            reset.
          </p>
          <button disabled={busy} onClick={join}>
            Join session
          </button>
        </section>
      ) : (
        <>
          <p role="status">
            {connected && person
              ? "Connected to the shared clock"
              : "Waiting for connection…"}
          </p>
          {connected && person && (
            <section>
              <p>
                Assigned location: {person.zone_id || "outside a named zone"}
              </p>
              <p>
                Reports below use this assigned location. Moving or spoken
                landmark locations are not supported yet.
              </p>
              {Number.isFinite(person.p_60) && (
                <p>
                  Published chance of fire reaching your zone within one hour:{" "}
                  {Math.round(person.p_60 * 100)}%
                </p>
              )}
              <div className="phone-report__choices">
                {["fire", "smoke", "embers", "all_clear"].map((kind) => (
                  <button
                    key={kind}
                    disabled={busy || !clock}
                    onClick={() => tap(kind)}
                  >
                    {kind === "all_clear"
                      ? "All clear"
                      : kind[0].toUpperCase() + kind.slice(1)}
                  </button>
                ))}
              </div>
              <button
                disabled={
                  (!recording && busy) || !session.voice_enabled || !clock
                }
                onClick={voice}
              >
                {recording
                  ? "Stop recording"
                  : session.voice_enabled
                    ? "Record a voice report"
                    : "Voice unavailable — use report buttons"}
              </button>
              {status && (
                <p>
                  {used
                    ? "Forecast includes your report."
                    : status.status === "rejected"
                      ? `Report rejected: ${status.reject_reason}`
                      : status.status === "accepted"
                        ? "Report accepted; waiting for its forecast."
                        : "Report awaiting engine review."}
                </p>
              )}
            </section>
          )}
          <button
            className="phone-report__leave"
            disabled={busy && !recording}
            onClick={() => {
              cancelled.current = true;
              clearTimeout(timer.current);
              if (recorder.current?.state === "recording")
                recorder.current.stop();
              tracks.current?.getTracks().forEach((t) => t.stop());
              sessionStorage.removeItem(storageKey);
              setSession(null);
              setRows({});
              setConnected(false);
            }}
          >
            Leave session
          </button>
        </>
      )}
      <p role="status" aria-live="polite">
        {message}
      </p>
    </main>
  );
}
