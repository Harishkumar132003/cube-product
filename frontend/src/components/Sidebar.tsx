import { useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import type { Project } from '../types';
import { Icon } from './Icon';

interface Props {
  projects: Project[];
  active: Project | null;
  openaiReady: boolean;
  openaiModel: string;
  backendUp: boolean;
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? 'nav__item is-active' : 'nav__item';

export function Sidebar({
  projects,
  active,
  openaiReady,
  openaiModel,
  backendUp,
}: Props) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const connected = Boolean(active?.connection);
  const hasContext = Boolean(active?.business_context?.trim());
  const scanned = Boolean(active?.last_scan_at);
  const others = projects.filter((p) => p.id !== active?.id);

  const go = (path: string) => {
    setOpen(false);
    navigate(path);
  };

  return (
    <aside className="rail">
      <button
        type="button"
        className="ws"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <span className="ws__glyph">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M8 1.5 14 5v6l-6 3.5L2 11V5l6-3.5Z"
              stroke="currentColor"
              strokeWidth="1.4"
              strokeLinejoin="round"
            />
            <path d="M8 8.2 14 5M8 8.2v6.3M8 8.2 2 5" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </span>
        <span className="ws__text">
          <span className="ws__name">{active ? active.name : 'Semantic layer'}</span>
          <span className="ws__sub">
            {active?.connection ? active.connection.database : 'Cube model generator'}
          </span>
        </span>
        <Icon name="chevron" size={14} className="ws__chev" />
      </button>

      {open && (
        <div className="ws__menu">
          {others.map((project) => (
            <button
              key={project.id}
              type="button"
              className="ws__item"
              onClick={() => go(`/projects/${project.id}`)}
            >
              {project.name}
            </button>
          ))}
          <button
            type="button"
            className="ws__item ws__item--all"
            onClick={() => go('/projects')}
          >
            All projects
          </button>
        </div>
      )}

      <nav className="nav">
        {active ? (
          <>
            <div className="nav__group">Main menu</div>
            <NavLink to={`/projects/${active.id}`} end className={navClass}>
              <Icon name="grid" className="nav__icon" />
              Dashboard
            </NavLink>
            <NavLink to={`/projects/${active.id}/connection`} className={navClass}>
              <Icon name="database" className="nav__icon" />
              Connection
              <span className="nav__spacer" />
              {!connected && <span className="nav__tag">setup</span>}
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/tables`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="scan" className="nav__icon" />
              Tables
              <span className="nav__spacer" />
              {connected && !scanned && <span className="nav__tag">scan</span>}
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/context`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="pencil" className="nav__icon" />
              Context
              <span className="nav__spacer" />
              {connected && !hasContext && <span className="nav__tag">optional</span>}
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/generate`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="sparkle" className="nav__icon" />
              Generate
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/agent`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="chat" className="nav__icon" />
              Agent
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/prompts`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="pencil" className="nav__icon" />
              Prompts
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/review`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="check" className="nav__icon" />
              Review
            </NavLink>
            <NavLink
              to={`/projects/${active.id}/views`}
              className={navClass}
              aria-disabled={!connected}
              tabIndex={connected ? undefined : -1}
            >
              <Icon name="grid" className="nav__icon" />
              Views
            </NavLink>
            

            <div className="nav__group">Workspace</div>
            <NavLink to="/projects" className={navClass}>
              <Icon name="folder" className="nav__icon" />
              Projects
              <span className="nav__spacer" />
              <span className="nav__tag">{projects.length}</span>
            </NavLink>

            <div className="nav__group">Project</div>
            <NavLink to={`/projects/${active.id}/settings`} className={navClass}>
              <Icon name="settings" className="nav__icon" />
              Settings
            </NavLink>
          </>
        ) : (
          <>
            <div className="nav__group">Workspace</div>
            <NavLink to="/projects" className={navClass}>
              <Icon name="folder" className="nav__icon" />
              Projects
              <span className="nav__spacer" />
              <span className="nav__tag">{projects.length}</span>
            </NavLink>
          </>
        )}
      </nav>

      <div className="who">
        <span className="who__avatar">{openaiReady ? 'AI' : '—'}</span>
        <span className="who__text">
          <span className="who__name">
            {openaiReady ? openaiModel : 'OpenAI key not set'}
          </span>
          <span className="who__sub">
            {backendUp ? 'backend connected' : 'backend unreachable'}
          </span>
        </span>
      </div>
    </aside>
  );
}
