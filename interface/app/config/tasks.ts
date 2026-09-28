/**
 * Configuration for task-related constants
 */

import { ENV } from './env';

// Tasks with website requirements (must all be completed before showing all tasks)
// These should match the task.name field from the API
export const WEBSITE_REQUIREMENT_TASKS = [
  'website_tutorial_intro',
  'zic_zac_zoe',
  'website_tutorial_follow_up',
  'zic_zac_zoe_follow_up',
] as const;
export const GAME_REQUIRED_TASKS = ['platformer'] as const;
export const WEBSITE_TUTORIAL_TASKS = ['website_tutorial_intro', 'website_tutorial_follow_up'] as const;
export const TIMED_TASKS = [
  ...WEBSITE_REQUIREMENT_TASKS.filter((taskName) => !WEBSITE_TUTORIAL_TASKS.includes(taskName as any)),
  ...GAME_REQUIRED_TASKS,
] as const;
export const WEBSITE_REQUIREMENTS_LABEL = 'website_requirements' as const;

export type StudyTaskMode = 'website-requirements' | 'game';

type TaskLike = {
  id?: string;
  name?: string;
  label?: string;
  status?: string;
};

export const isWebsiteRequirementTask = (task: TaskLike): boolean => {
  const label = (task.label || '').toLowerCase();
  return label === WEBSITE_REQUIREMENTS_LABEL || WEBSITE_REQUIREMENT_TASKS.includes(task.name as any);
};

export const getWebsiteRequirementTaskNames = (tasks: TaskLike[]): string[] => {
  const namesFromTasks = tasks
    .filter((task) => task.id !== 'playground' && isWebsiteRequirementTask(task) && !!task.name)
    .map((task) => task.name as string);

  if (namesFromTasks.length > 0) {
    const namesSet = new Set(namesFromTasks);
    const orderedKnownTasks = WEBSITE_REQUIREMENT_TASKS.filter((taskName) => namesSet.has(taskName));
    const extraWebsiteTasks = Array.from(namesSet).filter(
      (taskName) => !WEBSITE_REQUIREMENT_TASKS.includes(taskName as any)
    );
    return [...orderedKnownTasks, ...extraWebsiteTasks];
  }

  return [...WEBSITE_REQUIREMENT_TASKS];
};

export const getStudyTaskMode = (
  tasks: TaskLike[],
  websiteRequirementsCompletedOverride: boolean = false
): StudyTaskMode => {
  if (ENV.OPEN_ENDED_GAME_STUDY_ONLY || websiteRequirementsCompletedOverride) {
    return 'game';
  }
  const websiteRequirementTaskNames = getWebsiteRequirementTaskNames(tasks);
  const completedTaskNames = new Set(
    tasks
      .filter((task) => task.id !== 'playground' && task.status === 'completed' && !!task.name)
      .map((task) => task.name as string)
  );
  const websiteRequirementsCompleted = websiteRequirementTaskNames.every((taskName) =>
    completedTaskNames.has(taskName)
  );
  return websiteRequirementsCompleted ? 'game' : 'website-requirements';
};

export const getRequiredTasksForMode = (mode: StudyTaskMode, tasks: TaskLike[] = []): readonly string[] => {
  if (mode === 'website-requirements') {
    return getWebsiteRequirementTaskNames(tasks);
  }
  return GAME_REQUIRED_TASKS;
};

// Usernames to filter out from the leaderboard (e.g., test accounts, internal users)
export const LEADERBOARD_FILTERED_USERNAMES: string[] = [
  'testtest',
  'test-example',
  'test',
  'eek',
  'swagswag',
  'afda@a.com',
  ...Array.from({ length: 100 }, (_, idx) => `test${idx + 1}`)
];

/**
 * Usernames, emails, and/or numeric user ids (as strings) for internal reviewers:
 * pre-test gate treated as passed; community submissions (including timed platformer) without starting the timer;
 * extra "View Submissions" on the task grid; full non-tutorial task catalog; **no sequential locking** on browse/vibe.
 * Matched case-insensitively against username, email, and `String(user.id)`.
 */
export const INTERNAL_REVIEWER_IDENTIFIERS: string[] = [
  // 'you@university.edu',
  // 'internal_account',
  'nbalepur',
  'baumler',
  'gpt-one-shot',
  'gpt-many-shot',
];

/**
 * Shared kiosk / conference accounts that may submit more than once per task.
 * Matched case-insensitively against username, email, and `String(user.id)`.
 * Each submission is kept (not collapsed to latest-per-user) and tagged with
 * the email collected on the submit form. These accounts skip the tutorial,
 * website-requirements phase, and required-task sequence.
 */
export const MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS: string[] = [
  'hcomp_2026_user',
];

