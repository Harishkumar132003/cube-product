import { useEffect, useRef, useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { ApiError, api } from "../api";
import { Empty } from "../components/Empty";
import { Icon } from "../components/Icon";
import { Topbar } from "../components/Topbar";
import type { AgentAnswer, ViewIndexStatus } from "../types";
import type { ProjectContext } from "./ProjectLayout";

interface Turn {
  id: number;
  question: string;
  /** Undefined while the answer is still being worked out. */
  result?: AgentAnswer;
  error?: string;
  seconds?: number;
}

const SUGGESTIONS = [
  "How many cases are there in total?",
  "How much was approved for each case status?",
  "Which case status has the highest approved amount?",
];

/** Nothing is stored: reloading the page clears the conversation. */
export function AgentPage() {
  const { project } = useOutletContext<ProjectContext>();
  const navigate = useNavigate();

  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [index, setIndex] = useState<ViewIndexStatus | null>(null);
  const [shown, setShown] = useState<number | null>(null);
  const next = useRef(1);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api
      .viewIndexStatus(project.id)
      .then(setIndex)
      .catch(() => setIndex(null));
  }, [project.id]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  const ask = async (text: string) => {
    const asked = text.trim();
    if (!asked || busy) return;
    const id = next.current++;
    setTurns((t) => [...t, { id, question: asked }]);
    setQuestion("");
    setBusy(true);
    const started = Date.now();
    try {
      const result = await api.ask(project.id, asked);
      setTurns((t) =>
        t.map((turn) =>
          turn.id === id
            ? {
                ...turn,
                result,
                seconds: Math.round((Date.now() - started) / 100) / 10,
              }
            : turn,
        ),
      );
      // Open the working for anything that did not come back clean.
      if (result.ok === false) setShown(id);
    } catch (err) {
      setTurns((t) =>
        t.map((turn) =>
          turn.id === id
            ? {
                ...turn,
                error:
                  err instanceof ApiError
                    ? err.message
                    : "That request failed.",
              }
            : turn,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  if (index && index.reachable && index.count === 0) {
    return (
      <>
        <Topbar title="Agent" />
        <div className="page">
          <section className="card">
            <Empty
              icon="search"
              title="Index the views first"
              body="The agent finds the measures and dimensions a question needs by searching the index. Nothing is indexed for this project yet."
              actions={
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}/views`)}
                >
                  Go to Views
                </button>
              }
            />
          </section>
        </div>
      </>
    );
  }

  return (
    <>
      <Topbar
        title="Agent"
        actions={
          <>
            {index?.stale && (
              <span className="pill pill--warn">index out of date</span>
            )}
            {turns.length > 0 && (
              <button
                className="btn btn--ghost"
                type="button"
                onClick={() => setTurns([])}
              >
                <Icon name="refresh" size={14} />
                Clear
              </button>
            )}
          </>
        }
      />

      <div className="page chat">
        {turns.length === 0 && (
          <div className="chat__intro">
            <p className="lede">
              Ask a question about this project's data. The agent picks a view,
              retrieves the members it needs, writes the SQL, runs it against
              Cube and reads the result back. Nothing is saved — reloading
              clears this.
            </p>
            <div className="chat__suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="chat__suggestion"
                  onClick={() => void ask(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((turn) => (
          <div className="turn" key={turn.id}>
            <div className="said said--you">
              <div className="bubble bubble--asked">{turn.question}</div>
            </div>

            {!turn.result && !turn.error && (
              <div className="said">
                <span className="said__mark">
                  <Icon name="sparkle" size={13} />
                </span>
                <div className="bubble bubble--agent bubble--working">
                  <span className="spinner" />
                  Reading the model and building a query…
                </div>
              </div>
            )}

            {turn.error && (
              <div className="said">
                <span className="said__mark said__mark--bad">
                  <Icon name="alert" size={13} />
                </span>
                <div className="bubble bubble--agent bubble--failed">
                  {turn.error}
                </div>
              </div>
            )}

            {turn.result && (
              <div className="said">
                <span
                  className={`said__mark${
                    turn.result.ok === false ? " said__mark--bad" : ""
                  }`}
                >
                  <Icon
                    name={turn.result.kind === "db_query" ? "sparkle" : "chat"}
                    size={13}
                  />
                </span>
                <div className="bubble bubble--agent">
                  <div className="bubble__text">{turn.result.answer}</div>

                  <div className="bubble__meta">
                    {turn.result.kind === "db_query" && turn.result.view && (
                      <span className="pill pill--violet mono">
                        {turn.result.view}
                      </span>
                    )}
                    {turn.result.kind === "not_permitted" && (
                      <span className="pill pill--warn">refused</span>
                    )}
                    {turn.result.ok === false && (
                      <span className="pill pill--down">could not answer</span>
                    )}
                    {turn.seconds !== undefined && (
                      <span className="tagx num">{turn.seconds}s</span>
                    )}
                    {turn.result.steps.length > 0 && (
                      <button
                        type="button"
                        className="btn btn--quiet btn--sm"
                        onClick={() =>
                          setShown(shown === turn.id ? null : turn.id)
                        }
                      >
                        {shown === turn.id ? "Hide working" : "Show working"}
                      </button>
                    )}
                  </div>

                  {shown === turn.id && (
                    <div className="working">
                      {turn.result.steps.map((s, i) => {
                        const { step, ...rest } = s;
                        return (
                          <div className="working__row" key={i}>
                            <span className="working__step">{step}</span>
                            <span className="working__detail">
                              {Object.entries(rest)
                                .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                                .join("  ")}
                            </span>
                          </div>
                        );
                      })}

                      {turn.result.sql && (
                        <>
                          <div className="working__label">SQL sent to Cube</div>
                          <pre className="preview">{turn.result.sql}</pre>
                        </>
                      )}

                      {turn.result.rows && turn.result.rows.length > 0 && (
                        <>
                          <div className="working__label">
                            Rows ({turn.result.rows.length})
                          </div>
                          <div className="table-wrap">
                            <table className="table">
                              <thead>
                                <tr>
                                  {Object.keys(turn.result.rows[0]).map((c) => (
                                    <th key={c}>{c}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {turn.result.rows.slice(0, 20).map((row, r) => (
                                  <tr key={r}>
                                    {Object.values(row).map((v, c) => (
                                      <td key={c}>
                                        {v === null ? "—" : String(v)}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <div className="composer">
        {/* Matched to the transcript width above it, so the input lines up with
            the bubbles rather than spanning the whole canvas. */}
        <div className="composer__inner">
          <input
            className="field__input"
            value={question}
            placeholder="Ask about this project's data…"
            disabled={busy}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void ask(question);
            }}
          />
          <button
            className="btn btn--solid"
            type="button"
            disabled={busy || !question.trim()}
            onClick={() => void ask(question)}
          >
            <Icon name="chat" size={14} />
            {busy ? "Asking…" : "Ask"}
          </button>
        </div>
      </div>
    </>
  );
}
