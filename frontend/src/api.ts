import type {
  AgentAnswer,
  Bootstrap,
  Bundle,
  CubeCatalogue,
  GeneratedModel,
  JobSnapshot,
  JobState,
  ProjectPrompt,
  SavedView,
  Scan,
  ViewIndexReport,
  ViewIndexStatus,
  ViewSearchResult,
  ConnectionInput,
  Project,
  StoredConnection,
  TestConnectionResponse,
} from './types';

/** Where a file failed validation, and what kind of mistake it was. */
export interface FileProblem {
  detail: string;
  line: number | null;
  hint: string | null;
  kind: 'indentation' | 'syntax' | 'schema';
}

/** Error carrying the backend's own message, which is written for the user. */
export class ApiError extends Error {
  /** Present when the failure located itself in a file. */
  problem?: FileProblem;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let problem: FileProblem | undefined;
    try {
      const body = await response.json();
      if (typeof body.detail === 'string') detail = body.detail;
      // A file-validation failure carries a located message and a hint.
      else if (body.detail && typeof body.detail.detail === 'string') {
        problem = body.detail as FileProblem;
        detail = problem.detail;
      }
    } catch {
      /* non-JSON error body; keep the status message */
    }
    const error = new ApiError(detail);
    error.problem = problem;
    throw error;
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  bootstrap: () => request<Bootstrap>('/api/bootstrap'),

  testConnection: (input: ConnectionInput) =>
    request<TestConnectionResponse>('/api/connections/test', {
      method: 'POST',
      body: JSON.stringify(input),
    }),

  listProjects: () => request<Project[]>('/api/projects'),

  getProject: (id: string) => request<Project>(`/api/projects/${id}`),

  createProject: (name: string) =>
    request<Project>('/api/projects', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  renameProject: (id: string, name: string) =>
    request<Project>(`/api/projects/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }),

  runScan: (id: string) =>
    request<{ id: string }>(`/api/projects/${id}/scan`, { method: 'POST' }),

  buildBundle: (id: string, tables?: string[]) =>
    request<Bundle>(`/api/projects/${id}/bundle`, {
      method: 'POST',
      body: JSON.stringify(tables ? { tables } : {}),
    }),

  /** Starts generation and returns the job to watch; it does not wait. */
  startGeneration: (id: string, tables?: string[]) =>
    request<{ job_id: string; state: JobState }>(`/api/projects/${id}/generate`, {
      method: 'POST',
      body: JSON.stringify(tables ? { tables } : {}),
    }),

  generationStatus: (id: string, jobId: string) =>
    request<JobSnapshot>(`/api/projects/${id}/generate/${jobId}`),

  generationEventsUrl: (id: string, jobId: string) =>
    `/api/projects/${id}/generate/${jobId}/events`,

  cubeCatalogue: (id: string, root?: string) =>
    request<CubeCatalogue>(
      `/api/projects/${id}/cubes${root ? `?root=${encodeURIComponent(root)}` : ''}`,
    ),

  ask: (id: string, question: string) =>
    request<AgentAnswer>(`/api/projects/${id}/ask`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  listPrompts: (id: string) =>
    request<ProjectPrompt[]>(`/api/projects/${id}/prompts`),

  previewPrompt: (id: string, key: string) =>
    request<{ key: string; text: string; characters: number }>(
      `/api/projects/${id}/prompts/${key}/preview`,
    ),

  savePrompt: (id: string, key: string, body: string) =>
    request<{ key: string; customised: boolean }>(
      `/api/projects/${id}/prompts/${key}`,
      { method: 'PUT', body: JSON.stringify({ body }) },
    ),

  resetPrompt: (id: string, key: string) =>
    request<{ key: string; customised: boolean; body: string }>(
      `/api/projects/${id}/prompts/${key}`,
      { method: 'DELETE' },
    ),

  viewIndexStatus: (id: string) =>
    request<ViewIndexStatus>(`/api/projects/${id}/views/index`),

  rebuildViewIndex: (id: string) =>
    request<ViewIndexReport>(`/api/projects/${id}/views/index`, { method: 'POST' }),

  searchViews: (id: string, question: string, limit = 10) =>
    request<ViewSearchResult>(`/api/projects/${id}/views/search`, {
      method: 'POST',
      body: JSON.stringify({ question, limit }),
    }),

  listViews: (id: string) => request<SavedView[]>(`/api/projects/${id}/views`),

  previewView: (id: string, body: unknown) =>
    request<{ yaml: string }>(`/api/projects/${id}/views/preview`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  saveView: (id: string, body: unknown) =>
    request<SavedView & { yaml: string }>(`/api/projects/${id}/views`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  deleteView: (id: string, name: string) =>
    request<void>(`/api/projects/${id}/views/${name}`, { method: 'DELETE' }),

  checkMember: (
    id: string,
    body: { table: string; kind: string; type?: string; sql?: string },
  ) =>
    request<{ errors: string[]; warnings: string[] }>(
      `/api/projects/${id}/model/members/check`,
      { method: 'POST', body: JSON.stringify(body) },
    ),

  listEdits: (id: string) =>
    request<Record<string, string>>(`/api/projects/${id}/model/edits`),

  saveModelFile: (id: string, path: string, content: string) =>
    request<{ path: string; edited: boolean; has_original: boolean }>(
      `/api/projects/${id}/model/files`,
      { method: 'PUT', body: JSON.stringify({ path, content }) },
    ),

  revertModelFile: (id: string, path: string) =>
    request<{ path: string; content: string }>(
      `/api/projects/${id}/model/files?path=${encodeURIComponent(path)}`,
      { method: 'DELETE' },
    ),

  getModel: (id: string) => request<GeneratedModel>(`/api/projects/${id}/model`),

  getScan: (id: string) => request<Scan>(`/api/projects/${id}/scan`),

  selectTables: (id: string, tables: string[]) =>
    request<Project>(`/api/projects/${id}/tables`, {
      method: 'PUT',
      body: JSON.stringify({ tables }),
    }),

  setTableNotes: (id: string, notes: Record<string, { purpose: string }>) =>
    request<Project>(`/api/projects/${id}/table-notes`, {
      method: 'PUT',
      body: JSON.stringify({ notes }),
    }),

  setContext: (id: string, text: string) =>
    request<Project>(`/api/projects/${id}/context`, {
      method: 'PUT',
      body: JSON.stringify({ text }),
    }),

  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: 'DELETE' }),

  setConnection: (projectId: string, input: ConnectionInput) =>
    request<StoredConnection>(`/api/projects/${projectId}/connection`, {
      method: 'PUT',
      body: JSON.stringify(input),
    }),

  clearConnection: (projectId: string) =>
    request<void>(`/api/projects/${projectId}/connection`, { method: 'DELETE' }),

  reprobe: (projectId: string) =>
    request<TestConnectionResponse>(
      `/api/projects/${projectId}/connection/probe`,
      { method: 'POST' },
    ),

  selectSchemas: (projectId: string, schemas: string[]) =>
    request<StoredConnection>(
      `/api/projects/${projectId}/connection/schemas`,
      { method: 'PUT', body: JSON.stringify({ schemas }) },
    ),
};
