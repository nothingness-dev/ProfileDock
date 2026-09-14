import type { AppearanceId } from './appearances';

/** Illustrative UI data only. Never read from or write to profile storage. */
export interface PreviewProfile {
  readonly id: string;
  readonly name: string;
  readonly appearanceId: AppearanceId;
  readonly engine: 'Direct' | 'Playwright';
  readonly browser: 'Chromium';
  readonly status: 'closed' | 'running';
  readonly lastOpened: string | null;
  readonly tabCount?: number;
}

export function createPreviewProfiles(): PreviewProfile[] {
  return [
    { id: 'preview-personal', name: 'Personal', appearanceId: 'ember', engine: 'Direct', browser: 'Chromium', status: 'closed', lastOpened: '2h ago' },
    { id: 'preview-development', name: 'Development', appearanceId: 'prism', engine: 'Playwright', browser: 'Chromium', status: 'running', lastOpened: null, tabCount: 6 },
    { id: 'preview-research', name: 'Research', appearanceId: 'orbit', engine: 'Direct', browser: 'Chromium', status: 'closed', lastOpened: 'yesterday' },
    { id: 'preview-sandbox', name: 'Sandbox', appearanceId: 'vertex', engine: 'Playwright', browser: 'Chromium', status: 'running', lastOpened: null, tabCount: 2 },
  ];
}

export function filterProfiles(profiles: readonly PreviewProfile[], query: string): PreviewProfile[] {
  const needle = query.trim().toLocaleLowerCase('en');
  return profiles.filter((profile) => profile.name.toLocaleLowerCase('en').includes(needle));
}

export function runningProfiles(profiles: readonly PreviewProfile[]): PreviewProfile[] {
  return profiles.filter((profile) => profile.status === 'running');
}

export function setPreviewStatus(profiles: readonly PreviewProfile[], id: string, status: PreviewProfile['status']): PreviewProfile[] {
  return profiles.map((profile) => profile.id === id && profile.status !== status
    ? { ...profile, status, lastOpened: 'just now in preview', tabCount: undefined }
    : profile);
}

export function closeAllPreviewProfiles(profiles: readonly PreviewProfile[]): PreviewProfile[] {
  return profiles.map((profile) => profile.status === 'running'
    ? { ...profile, status: 'closed', lastOpened: 'just now in preview', tabCount: undefined }
    : profile);
}
