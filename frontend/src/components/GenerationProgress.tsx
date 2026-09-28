import { useEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { GeneratedModel, JobSnapshot, JobState, JobStep } from '../types';

interface Props {
  eventsUrl: string;
  /** Called once when the job finishes, with the model it produced. */
  onDone: (model: GeneratedModel) => void;
  onFailed: (message: string) => void;
  onClose: () => void;
}

/** Fold incoming steps into the list, updating a step in place by `key`.
 *
 * A step is reported twice, once running and once done, so appending blindly
 * would show every step as both. Position is held by first appearance, which
 * keeps the order stable as parallel cubes finish out of order.
 */
function merge(current: JobStep[], incoming: JobStep[]): JobStep[] {
  const next = [...current];
  for (const step of incoming) {
    const index = next.findIndex((s) => s.key === step.key);
    if (index === -1) next.push(step);
    else next[index] = step;
  }
  return next;
}

/** Live progress for one generation job, over server-sent events.
 *
 * Generation is minutes of silence otherwise, which reads as a hang. The steps
 * are streamed as they start and finish so it is always clear what is running
 * and which cube is slow.
 */
export function GenerationProgress({ eventsUrl, onDone, onFailed, onClose }: Props) {
  const [steps, setSteps] = useState<JobStep[]>([]);
  const [state, setState] = useState<JobState>('running');
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  /* The callbacks are re-created on every parent render; holding them in a ref
   * keeps the effect below tied to the URL alone, so the stream is opened once
   * per job instead of being torn down and reopened mid-generation. */
  const handlers = useRef({ onDone, onFailed });
  handlers.current = { onDone, onFailed };

  useEffect(() => {
    const source = new EventSource(eventsUrl);

    source.addEventListener('step', (event) => {
      const step = JSON.parse((event as MessageEvent).data) as JobStep;
      setSteps((current) => merge(current, [step]));
    });

    const finish = (event: Event) => {
      const snapshot = JSON.parse((event as MessageEvent).data) as JobSnapshot;
      setState(snapshot.state);
      setSteps((current) => merge(current, snapshot.events));
      if (snapshot.state === 'done' && snapshot.result) {
        handlers.current.onDone(snapshot.result);
      } else if (snapshot.state === 'failed') {
        setError(snapshot.error);
        handlers.current.onFailed(snapshot.error ?? 'Generation failed.');
      }
      source.close();
    };

    source.addEventListener('done', finish);
    source.addEventListener('failed', finish);

    source.onerror = () => {
      /* The stream also closes normally once the job ends; only treat a drop
       * as an error while work is still expected. */
      setState((current) => {
        if (current !== 'running') return current;
        setError('Lost the connection to the server. Generation may still be running.');
        return 'failed';
      });
      source.close();
    };

    return () => source.close();
  }, [eventsUrl]);

  useEffect(() => {
    if (state !== 'running') return;
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [state]);

  const done = steps.filter((s) => s.state === 'done').length;

  return (
    <div className="scrim" role="dialog" aria-modal="true" aria-label="Generating the model">
      <div className="modal">
        <div className="modal__head">
          <span className="card__label">
            <Icon name="sparkle" size={13} />
            {state === 'running' && 'Generating the model'}
            {state === 'done' && 'Model generated'}
            {state === 'failed' && 'Generation failed'}
          </span>
          <span className="card__tools">
            {state === 'running' ? (
              <span className="pill pill--violet num">{elapsed}s</span>
            ) : (
              <span className={`pill ${state === 'done' ? 'pill--up' : 'pill--down'}`}>
                {done} of {steps.length} steps
              </span>
            )}
          </span>
        </div>

        <div className="joblog">
          {steps.length === 0 && <p className="field__hint">Starting…</p>}
          {steps.map((step) => (
            <div className={`joblog__row is-${step.state}`} key={step.key}>
              <span className="joblog__mark">
                {step.state === 'running' && <span className="spinner" />}
                {step.state === 'done' && <Icon name="tick" size={13} />}
                {step.state === 'failed' && <Icon name="alert" size={13} />}
              </span>
              <span className="joblog__text">
                <span className="joblog__label">{step.label}</span>
                {step.detail && <span className="joblog__detail">{step.detail}</span>}
              </span>
            </div>
          ))}
        </div>

        {error && <p className="banner banner--error">{error}</p>}

        <div className="modal__foot">
          {state === 'running' ? (
            <span className="field__hint">
              This runs on the server. Closing this window will not stop it.
            </span>
          ) : (
            <span className="field__hint">
              {state === 'done'
                ? 'The model is ready. Open Review to read the generated YAML.'
                : 'Nothing was saved.'}
            </span>
          )}
          <button type="button" className="btn btn--solid btn--sm" onClick={onClose}>
            {state === 'running' ? 'Run in background' : 'Close'}
          </button>
        </div>
      </div>
    </div>
  );
}
