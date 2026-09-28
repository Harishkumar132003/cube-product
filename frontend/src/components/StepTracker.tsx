import { Icon } from './Icon';

export type StepState = 'done' | 'current' | 'locked';

/** Icons are shared with the buttons that act on each step. */
export type StepIcon = 'plug' | 'scan' | 'pencil' | 'sparkle' | 'check';

export interface Step {
  title: string;
  /** One line of real status, never a generic label. */
  status: string;
  state: StepState;
  icon: StepIcon;
  to: string | null;
}

interface Props {
  steps: Step[];
  onGo: (to: string) => void;
}

/**
 * Horizontal progress through the pipeline. Each step carries its own status
 * line, so the tracker says where you are and why the next one is blocked,
 * rather than only counting.
 */
export function StepTracker({ steps, onGo }: Props) {
  return (
    <ol className="tracker">
      {steps.map((step, i) => {
        const clickable = step.to !== null && step.state !== 'locked';
        return (
          <li
            key={step.title}
            className={`tracker__step is-${step.state}`}
            /* The connector to the left fills only once this step is reached. */
            data-connected={i > 0 ? (step.state === 'locked' ? 'off' : 'on') : undefined}
          >
            <button
              type="button"
              className="tracker__hit"
              disabled={!clickable}
              onClick={() => step.to && onGo(step.to)}
            >
              <span className="tracker__mark">
                {step.state === 'done' ? (
                  <Icon name="tick" size={13} />
                ) : (
                  <span className="tracker__n">{i + 1}</span>
                )}
              </span>
              <span className="tracker__title">{step.title}</span>
              <span className="tracker__status">{step.status}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
