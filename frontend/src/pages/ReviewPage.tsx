import { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';
import { api } from '../api';
import { Empty } from '../components/Empty';
import { Icon } from '../components/Icon';
import { ModelFiles } from '../components/ModelFiles';
import { Topbar } from '../components/Topbar';
import type { GeneratedModel } from '../types';
import type { ProjectContext } from './ProjectLayout';

/* Hidden for now. The generator only reliably repeats questions already
 * written into the business context rather than finding its own, so the panel
 * is either empty or an echo. Flip to true once question discovery is real. */
const SHOW_QUESTIONS = false;

export function ReviewPage() {
  const { project } = useOutletContext<ProjectContext>();
  const navigate = useNavigate();
  const [model, setModel] = useState<GeneratedModel | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let live = true;
    api
      .getModel(project.id)
      .then((found) => live && setModel(found))
      .catch(() => live && setModel(null))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [project.id]);

  if (loading) return null;

  if (!model) {
    return (
      <>
        <Topbar title="Review" />
        <div className="page">
          <section className="card">
            <Empty
              icon="sparkle"
              title="Nothing to review yet"
              body="Review shows the generated model: the YAML Cube will read, and anything the evidence could not settle. Generate a model first."
              actions={
                <button
                  className="btn btn--primary"
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}/generate`)}
                >
                  Go to Generate
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
        title="Review"
        actions={
          <span className="pill pill--idle">
            Generated {new Date(model.created_at).toLocaleString()}
          </span>
        }
      />

      <div className="page stack">
        {/* What was built. Shown here rather than on Generate: this is where the
            model is read, and having it in both places meant neither was the
            place to look. */}
        <section className="card">
          <div className="card__head">
            <span className="card__label">
              <Icon name="sparkle" size={13} />
              What was generated
            </span>
            <span className="card__tools">
              <span className="pill pill--idle">
                {model.scope.selected.length} tables
              </span>
            </span>
          </div>
          <div className="card__body">
            <div className="facts">
              {(['cubes', 'joins', 'dimensions', 'measures', 'views'] as const).map((k) => (
                <div className="fact" key={k}>
                  <div className="fact__label">{k}</div>
                  <div className="fact__value">{model.counts[k] ?? 0}</div>
                </div>
              ))}
            </div>
          </div>
        </section>

        {SHOW_QUESTIONS && (model.questions.length > 0 ? (
          <section className="card">
            <div className="card__head">
              <span className="card__label">
                <Icon name="alert" size={13} />
                Open questions
              </span>
              <span className="card__tools">
                <span className="pill pill--warn">{model.questions.length}</span>
              </span>
            </div>
            <div className="feed">
              {model.questions.map((q, i) => (
                <div className="feed__row" key={i}>
                  <span className="feed__glyph feed__glyph--warn">
                    <Icon name="alert" size={14} />
                  </span>
                  <span className="feed__text">
                    <span className="feed__name">{q.question}</span>
                    <span className="feed__sub">{q.why_it_matters}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>
        ) : (
          /* Not a success state. The generator raised nothing, and we know it
           * only reliably repeats questions already written into the business
           * context rather than finding its own. Saying "no questions" here
           * would read as a clean bill of health it has not earned. */
          <div className="banner banner--info">
            The generator raised no questions on this run. That is not the same as
            the model being right — read the YAML below before trusting it.
          </div>
          ))}

        {model.files && <ModelFiles files={model.files} projectId={project.id} />}
      </div>
    </>
  );
}
