import { MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS } from './tasks';

type MultiSubmitUserLike = {
  id?: string | number | null;
  email?: string | null;
  username?: string | null;
};

const MULTI_SUBMISSION_SET: ReadonlySet<string> = new Set(
  MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS.map((s) => String(s).trim().toLowerCase()).filter(Boolean)
);

function normalizeToken(s: string | null | undefined): string | null {
  if (s == null) return null;
  const t = String(s).trim().toLowerCase();
  return t.length ? t : null;
}

/**
 * Whether this account may create multiple submissions per task
 * (`MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS` in tasks.ts).
 */
export function isMultiSubmissionUser(user: MultiSubmitUserLike | null | undefined): boolean {
  if (!user) return false;
  if (MULTI_SUBMISSION_SET.size === 0) return false;

  const email = normalizeToken(user.email ?? undefined);
  if (email && MULTI_SUBMISSION_SET.has(email)) return true;

  const username = normalizeToken(user.username ?? undefined);
  if (username && MULTI_SUBMISSION_SET.has(username)) return true;

  if (user.id != null && user.id !== '') {
    const idKey = String(user.id).trim().toLowerCase();
    if (idKey && MULTI_SUBMISSION_SET.has(idKey)) return true;
  }

  return false;
}
