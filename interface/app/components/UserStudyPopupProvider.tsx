"use client";
import { ReactNode, useState, useEffect, useCallback, useRef } from "react";
import { useSearchParams, usePathname } from "next/navigation";
import UserStudyPopup, { PopupState, UserStudyPopupContext } from "./UserStudyPopup";
import { getCookie } from "../utils/cookies";
import { ENV } from "../config/env";
import { useAuth } from "../utils/auth";
import { getWebsiteRequirementTaskNames, isWebsiteRequirementTask, WEBSITE_REQUIREMENT_TASKS } from "../config/tasks";
import {
  hasWebsiteRequirementsChoiceFromSettings,
  isWebsiteRequirementsPhaseSkippedForStudy,
  saveWebsiteRequirementsChoiceInSettings,
  setWebsiteRequirementsChoiceLocal,
} from "../utils/userSettings";
import { PASSWORD_HASH, hashString } from "../utils/password";
import { isInternalReviewerUser } from "../config/internalReviewers";
import { isMultiSubmissionUser } from "../config/multiSubmissionAccounts";

type TutorialCookieState = 'unseen' | 'seen' | 'dismissed';
const TUTORIAL_COOKIE_NAME = `${ENV.COOKIE_PREFIX}tutorial_state`;
const WEBSITE_MODE_COMPLETE_COOKIE_NAME = `${ENV.COOKIE_PREFIX}website_requirements_complete_acknowledged`;
const ASSISTANT_TRANSITION_ACK_COOKIE_NAME = `${ENV.COOKIE_PREFIX}assistant_transition_acknowledged`;
const FOLLOW_UP_INTRO_TASK_NAME = 'website_tutorial_follow_up';

/** Session-only flag: when set, pre-test is treated as completed (do not use if you are in CMSC848Q). Unguessable key. */
export const PRE_TEST_SKIPPED_KEY = `${ENV.COOKIE_PREFIX}x9k2m7p4q1w8e5r3t6y0u1i2o3s5a7b9c`;

// Set to true to disable the user study popup
const DISABLE_USER_STUDY_POPUP = false;

interface UserStudyPopupProviderProps {
  children: ReactNode;
}

/**
 * Provider component that wraps the UserStudyPopup and provides context to children.
 * This allows the popup to be included in the server-side layout.
 * 
 * Dynamically calculates popup state based on:
 * - Tutorial cookie status
 * - User submissions (for pre-test check)
 * - Task completion status (for post-test check)
 * - Skill check completion status
 * 
 * Other components can use useUserStudyPopup() hook to access and set the popup state.
 */
export default function UserStudyPopupProvider({ children }: UserStudyPopupProviderProps) {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const { user, token, refreshUser, isLoading: isAuthLoading } = useAuth();
  const numericUserId = user?.id && !Number.isNaN(Number(user.id)) ? Number(user.id) : null;
  
  // Check for secret password bypass using hash comparison
  const [hasSecretPassword, setHasSecretPassword] = useState(false);
  const [passwordCheckComplete, setPasswordCheckComplete] = useState(false);
  
  useEffect(() => {
    const checkPassword = async () => {
      const password = searchParams?.get('password');
      if (password) {
        const passwordHash = await hashString(password);
        setHasSecretPassword(passwordHash === PASSWORD_HASH);
      } else {
        setHasSecretPassword(false);
      }
      setPasswordCheckComplete(true);
    };
    checkPassword();
  }, [searchParams]);
  
  // Calculate the appropriate popup state based on user progress
  const calculatePopupState = useCallback(async (): Promise<PopupState> => {
    // If disabled via flag, bypass all popup logic
    if (DISABLE_USER_STUDY_POPUP) {
      return 'none';
    }
    
    // If secret password is present, bypass all popup logic
    if (hasSecretPassword) {
      return 'none';
    }

    // Shared kiosk accounts should not be gated by pre/post-test after the first submit.
    if (isMultiSubmissionUser(user ?? undefined)) {
      setPreTestCompleted(true);
      setPostTestCompleted(true);
      setAllRequiredTasksCompleted(true);
      setPostTestBlockedByParticipantCap(false);
      return 'none';
    }
    
    // Collect debug information
    const debugInfo: any = {
      userId: numericUserId,
      tutorialState: null as TutorialCookieState | null,
      hasAnySubmissions: null as boolean | null,
      preTestCompleted: null as boolean | null,
      postTestCompleted: null as boolean | null,
      allRequiredTasksCompleted: null as boolean | null,
      completedTaskNames: [] as string[],
      requiredTaskNames: WEBSITE_REQUIREMENT_TASKS,
      numProjectsSubmitted: null as number | null,
      numGameProjectsSubmitted: null as number | null,
      hasSubmittedPlatformer: null as boolean | null,
      cookieDismissed: null as boolean | null,
      sessionDismissed: null as boolean | null,
      shouldShowSkillCheckPrompt: null as boolean | null,
      skillCheckPromptCondition: null as any,
      websiteRequirementsSkipped: null as boolean | null,
      websiteRequirementsChoiceMade: null as boolean | null,
      finalDecision: null as PopupState | null,
      error: null as any,
    };

    // If no user ID (not authenticated), don't show any popup
    if (!numericUserId) {
      setAllRequiredTasksCompleted(false);
      setPostTestBlockedByParticipantCap(false);
      debugInfo.finalDecision = 'none';
      return 'none';
    }
    
    try {
      // Check tutorial cookie first
      const tutorialState = (getCookie(TUTORIAL_COOKIE_NAME) as TutorialCookieState | null) || 'unseen';
      debugInfo.tutorialState = tutorialState;
      const websiteRequirementsSkipped = isWebsiteRequirementsPhaseSkippedForStudy(user?.settings);
      const websiteRequirementsChoiceMade = hasWebsiteRequirementsChoiceFromSettings(user?.settings);
      debugInfo.websiteRequirementsSkipped = websiteRequirementsSkipped;
      debugInfo.websiteRequirementsChoiceMade = websiteRequirementsChoiceMade;
      
      const selectedTaskParam = searchParams?.get('task');
      const experimentGroup = typeof user?.settings?.experiment_group === 'string'
        ? user.settings.experiment_group.trim().toLowerCase()
        : '';
      const isAgentGroupUser = experimentGroup === 'agent';

      // Fast path: fetch completion status first so tutorial/pre-test checks can resolve quickly.
      const testStatusResponse = await fetch(
        `/api/skill-check/completion-status-both?user_id=${numericUserId}`
      );

      // Parse test status (combines pre-test and post-test in one call)
      let preTestCompletedValue = false;
      let postTestCompletedValue = false;
      if (testStatusResponse.ok) {
        const testStatusData = await testStatusResponse.json();
        preTestCompletedValue = testStatusData.pre_test?.completed || false;
        postTestCompletedValue = testStatusData.post_test?.completed || false;
        // If user chose "Skip pre-test", treat as completed for this session only
        if (typeof window !== 'undefined' && sessionStorage.getItem(PRE_TEST_SKIPPED_KEY) === 'true') {
          preTestCompletedValue = true;
        }
        debugInfo.preTestCompleted = preTestCompletedValue;
        debugInfo.postTestCompleted = postTestCompletedValue;
      } else {
        debugInfo.preTestCompleted = false;
        debugInfo.postTestCompleted = false;
      }

      const effectivePreTestCompleted =
        preTestCompletedValue || isInternalReviewerUser(user ?? undefined);
      
      // Store the completion status in state for use by other components
      setPreTestCompleted(effectivePreTestCompleted);
      setPostTestCompleted(postTestCompletedValue);
      
      // Use the local variables for the rest of the function
      const preTestCompleted = effectivePreTestCompleted;
      debugInfo.preTestCompleted = effectivePreTestCompleted;

      // Fast decision flow:
      // 1. If user hasn't seen tutorial instructions, show tutorial immediately.
      // Open-ended-only study (game dev only): skip phase 1 instructions — same flag as browse/vibe study mode.
      if (tutorialState === 'unseen' && !ENV.OPEN_ENDED_GAME_STUDY_ONLY) {
        setPostTestBlockedByParticipantCap(false);
        debugInfo.finalDecision = 'tutorial';
        return 'tutorial';
      }
      
      // 2. If user has not done the pre-test, show pre-test immediately.
      if (!preTestCompleted) {
        setPostTestBlockedByParticipantCap(false);
        debugInfo.finalDecision = 'pre-test';
        return 'pre-test';
      }

      // Slow path: only after instructions/pre-test pass, fetch task/submission data.
      const [submissionsResponse, tasksResponse] = await Promise.all([
        fetch(`${ENV.BACKEND_URL}/api/users/${numericUserId}/submissions`),
        fetch(`/api/tasks`),
      ]);

      // Default to not blocked unless we explicitly detect cap blocking below.
      setPostTestBlockedByParticipantCap(false);

      // Parse submissions
      let submissionsData = { items: [] };
      if (submissionsResponse.ok) {
        submissionsData = await submissionsResponse.json();
        debugInfo.hasAnySubmissions = submissionsData.items && submissionsData.items.length > 0;
      }
      
      const submissions = submissionsData.items || [];
      const submittedProjectIds = new Set<number>(
        submissions.map((sub: any) => sub.projectId).filter((id: any): id is number => id != null)
      );
      
      // Calculate number of projects submitted
      const numProjectsSubmitted = submittedProjectIds.size;
      debugInfo.numProjectsSubmitted = numProjectsSubmitted;
      
      // Parse tasks and map projectId to task name
      let tasks: any[] = [];
      if (tasksResponse.ok) {
        const tasksData = await tasksResponse.json();
        tasks = tasksData.tasks || [];
        
        // Create mapping of projectId to task name
        const projectIdToTaskName = new Map<number, string>();
        tasks.forEach((task: any) => {
          if (task.projectId && task.name) {
            projectIdToTaskName.set(task.projectId, task.name);
          }
        });
        
        // Find completed task names from submissions
        const completedTaskNames = new Set<string>();
        submittedProjectIds.forEach((projectId: number) => {
          const taskName = projectIdToTaskName.get(projectId);
          if (taskName) {
            completedTaskNames.add(taskName);
          }
        });
        
        debugInfo.completedTaskNames = Array.from(completedTaskNames);
        const requiredTaskNames = getWebsiteRequirementTaskNames(tasks);
        debugInfo.requiredTaskNames = requiredTaskNames;

        const completedGameTaskNames = new Set<string>();
        submittedProjectIds.forEach((projectId: number) => {
          const matchingTask = tasks.find((task: any) => task.projectId === projectId);
          if (!matchingTask || !matchingTask.name || matchingTask.id === 'playground') {
            return;
          }
          if (!isWebsiteRequirementTask(matchingTask)) {
            completedGameTaskNames.add(String(matchingTask.name).toLowerCase());
          }
        });
        const numGameProjectsSubmitted = completedGameTaskNames.size;
        const hasSubmittedPlatformer = completedGameTaskNames.has('platformer');
        debugInfo.numGameProjectsSubmitted = numGameProjectsSubmitted;
        debugInfo.hasSubmittedPlatformer = hasSubmittedPlatformer;

        const requiredCompleted = requiredTaskNames.every(
          taskName => completedTaskNames.has(taskName)
        );
        const effectiveRequiredCompleted = requiredCompleted || websiteRequirementsSkipped;
        debugInfo.allRequiredTasksCompleted = effectiveRequiredCompleted;
        setAllRequiredTasksCompleted(effectiveRequiredCompleted);
      } else {
        setAllRequiredTasksCompleted(websiteRequirementsSkipped);
      }
      
      // 3. Show one-time assistant transition notice for agent-group users
      // when they enter the follow-up warm-up task.
      if (isAgentGroupUser && selectedTaskParam && tasks.length > 0) {
        const selectedTask = tasks.find(
          (task: any) => String(task.id) === selectedTaskParam || task.name === selectedTaskParam
        );
        const selectedTaskName = selectedTask?.name || null;
        const assistantTransitionAcknowledged = getCookie(ASSISTANT_TRANSITION_ACK_COOKIE_NAME);
        if (
          selectedTaskName === FOLLOW_UP_INTRO_TASK_NAME &&
          !assistantTransitionAcknowledged
        ) {
          debugInfo.finalDecision = 'assistant-transition';
          return 'assistant-transition';
        }
      }

      // 4. When user would have seen the skip-task modal, we return this state so recalculateState can auto-set "don't skip" (modal removed).
      if (!debugInfo.allRequiredTasksCompleted && !websiteRequirementsChoiceMade) {
        debugInfo.finalDecision = 'website-task-choice';
        return 'website-task-choice';
      }

      // 5. Force post-test after task reqs, only if user is in first-N eligible participants (by time)
      if (
        !postTestCompletedValue &&
        (debugInfo.numGameProjectsSubmitted ?? 0) >= ENV.NUM_TASKS_REQUIRED_UNTIL_POSTTEST &&
        debugInfo.hasSubmittedPlatformer
      ) {
        // Only now do the expensive post-test pool checks.
        let postTestPoolData: {
          meets_task_requirement?: boolean;
          in_post_test_pool?: boolean;
          post_test_completed?: boolean;
          post_test_open?: boolean;
        } | null = null;
        let studyWidePostTestCompletionsCount: number | null = null;

        try {
          const [postTestPoolResponse, postTestCompletionsResponse] = await Promise.all([
            fetch(`${ENV.BACKEND_URL}/api/users/${numericUserId}/post-test-pool-status`),
            fetch(`${ENV.BACKEND_URL}/api/study/post-test-completions-count`),
          ]);

          if (postTestPoolResponse.ok) {
            try {
              postTestPoolData = await postTestPoolResponse.json();
            } catch {
              postTestPoolData = null;
            }
          }

          if (postTestCompletionsResponse.ok) {
            try {
              const pc = await postTestCompletionsResponse.json();
              if (typeof pc?.post_test_completions_count === 'number') {
                studyWidePostTestCompletionsCount = pc.post_test_completions_count;
              }
            } catch {
              /* ignore */
            }
          }
        } catch {
          // Ignore fetch failures here and fall back to permissive behavior below.
        }

        const poolFilledByCount =
          studyWidePostTestCompletionsCount != null &&
          studyWidePostTestCompletionsCount >= ENV.POST_TEST_PARTICIPANT_CAP;
        const postTestCapBlockedByBackend =
          !!postTestPoolData &&
          postTestPoolData.meets_task_requirement === true &&
          postTestPoolData.post_test_completed !== true &&
          postTestPoolData.post_test_open === false;
        setPostTestBlockedByParticipantCap(postTestCapBlockedByBackend);

        const allowedByBackend =
          !postTestPoolData ||
          postTestPoolData.post_test_completed === true ||
          postTestPoolData.post_test_open !== false;
        const allowed = allowedByBackend && !poolFilledByCount;
        if (allowed) {
          debugInfo.finalDecision = 'post-test';
          return 'post-test';
        }
        debugInfo.finalDecision = 'none';
        debugInfo.postTestBlockedByParticipantCap = true;
        setPostTestBlockedByParticipantCap(true);
      }
      
      // 6. Otherwise, if user has not completed all required tasks, show nothing
      if (!debugInfo.allRequiredTasksCompleted) {
        debugInfo.finalDecision = 'none';
        return 'none';
      }
      
      // 7. Show one-time transition modal when website requirements are completed.
      if (debugInfo.allRequiredTasksCompleted) {
        const websiteModeCompleteAcknowledged = getCookie(WEBSITE_MODE_COMPLETE_COOKIE_NAME);
        if (!websiteModeCompleteAcknowledged) {
          debugInfo.finalDecision = 'website-requirements-complete';
          return 'website-requirements-complete';
        }
      }
      
      // 8. Otherwise, show nothing
      debugInfo.finalDecision = 'none';
      return 'none';
    } catch (error) {
      debugInfo.error = error;
      debugInfo.finalDecision = 'none';
      setPostTestBlockedByParticipantCap(false);
      console.error('[UserStudyPopupProvider] Error calculating popup state:', error);
      // On error, default to none to avoid blocking the user
      return 'none';
    }
  }, [numericUserId, hasSecretPassword, user, searchParams]);
  
  // Manage the popup state
  const [popupState, setPopupState] = useState<PopupState>('none');
  const [isCalculating, setIsCalculating] = useState(false);
  const [preTestCompleted, setPreTestCompleted] = useState<boolean | null>(null);
  const [postTestCompleted, setPostTestCompleted] = useState<boolean | null>(null);
  const [allRequiredTasksCompleted, setAllRequiredTasksCompleted] = useState<boolean | null>(null);
  const [postTestBlockedByParticipantCap, setPostTestBlockedByParticipantCap] = useState(false);
  const isCalculatingRef = useRef(false);
  const hasInitializedRef = useRef(false);
  
  // Expose a function to trigger recalculation (for when user completes task)
  // This is the ONLY place we make API calls to check test completion status
  const recalculateState = useCallback(async () => {
    if (isCalculatingRef.current) {
      return; // Prevent concurrent calculations
    }
    isCalculatingRef.current = true;
    setIsCalculating(true);
    try {
      let newState = await calculatePopupState();
      // When we would have shown the skip-task modal, auto-set choice to "don't skip" and recalc (modal removed).
      if (newState === 'website-task-choice' && numericUserId) {
        setWebsiteRequirementsChoiceLocal(false);
        try {
          await saveWebsiteRequirementsChoiceInSettings(
            numericUserId,
            false,
            user?.settings,
            token ?? undefined
          );
          await refreshUser();
        } catch (err) {
          console.error('[UserStudyPopupProvider] Failed to auto-save website task choice:', err);
        }
        newState = await calculatePopupState();
      }
      // Double-check hasSecretPassword before setting state (in case it changed during calculation)
      if (hasSecretPassword) {
        setPopupState('none');
      } else {
        setPopupState(newState);
      }
    } finally {
      isCalculatingRef.current = false;
      setIsCalculating(false);
    }
  }, [calculatePopupState, hasSecretPassword, numericUserId, user?.settings, token, refreshUser]);
  
  // Initialize popup state on first page load (after auth loads)
  // This only runs once when auth is ready
  useEffect(() => {
    if (isAuthLoading || !numericUserId || hasInitializedRef.current || !passwordCheckComplete) {
      return;
    }
    
    // Disable flag bypass
    if (DISABLE_USER_STUDY_POPUP) {
      setPopupState('none');
      hasInitializedRef.current = true;
      return;
    }
    
    // Secret password bypass
    if (hasSecretPassword) {
      setPopupState('none');
      hasInitializedRef.current = true;
      return;
    }
    
    // Compensation page loads its own summary API; skip duplicate popup fetches on cold load.
    const isCompensationRoute =
      pathname === "/compensation" || pathname === "/compensation/";
    hasInitializedRef.current = true;
    if (!isCompensationRoute) {
      recalculateState();
    }
  }, [isAuthLoading, numericUserId, hasSecretPassword, passwordCheckComplete, recalculateState, pathname]);
  
  // Handle password check completing after initialization
  // This ensures that if the password check completes after initialization,
  // we still bypass the popup immediately
  useEffect(() => {
    if (DISABLE_USER_STUDY_POPUP || hasSecretPassword) {
      setPopupState('none');
    }
  }, [hasSecretPassword]);
  
  // Handle tutorial close - first hide the tutorial, then recalculate to show next popup
  const handleTutorialClose = useCallback(() => {
    // First, hide the tutorial popup immediately
    setPopupState('none');
    // Then recalculate state after a brief delay to show pre-test (if needed)
    // This gives a smooth transition from tutorial to pre-test
    setTimeout(() => {
      recalculateState();
    }, 100);
  }, [recalculateState]);
  
  // Stats page is accessible when: secret password in URL OR user has completed the study (pre-test + required tasks + post-test)
  const statsAccessible =
    hasSecretPassword ||
    (preTestCompleted === true && allRequiredTasksCompleted === true && postTestCompleted === true);

  return (
    <UserStudyPopupContext.Provider value={{ 
      popupState, 
      setPopupState, 
      recalculateState, 
      isCalculating,
      onTutorialClose: handleTutorialClose,
      preTestCompleted,
      postTestCompleted,
      postTestBlockedByParticipantCap,
      allRequiredTasksCompleted,
      statsAccessible
    }}>
      <UserStudyPopup />
      {children}
    </UserStudyPopupContext.Provider>
  );
}

