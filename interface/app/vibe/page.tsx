"use client";

// Disable static prerender to avoid CSR bailout issues with useSearchParams
export const dynamic = 'force-dynamic';
import { useState, useEffect, useRef, useTransition, useCallback, useMemo, Suspense } from "react";
import { createPortal } from "react-dom";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { useRouteProtection, useAuth } from "../utils/auth";
import { getUserSettingsCookie, updateUserSetting } from "../utils/cookies";
import {
  isPlaygroundCompletedFromSettings,
  isWebsiteRequirementsPhaseSkippedForStudy,
  setPlaygroundCompletedInSettings,
} from "../utils/userSettings";
import {
  Grid3X3, 
  Plus, 
  Infinity, 
  Globe, 
  Bookmark, 
  Moon, 
  Calendar, 
  Building2,
  Star,
  Shuffle,
  Search,
  Filter,
  Play,
  CheckCircle,
  Circle,
  BookmarkCheck,
  X,
  ArrowLeft,
  ArrowLeftRight,
  Download
} from "lucide-react";
import { useSidebar } from "../components/AppLayout";
import MinimalTaskList from "../components/MinimalTaskList";
import TaskCardGrid from "../components/TaskCardGrid";
import TaskInstructionNew from "../components/TaskInstructionNew";
import CodingEditor from "../components/CodingEditor";
import PreviewTab, { PreviewTabRef } from "../components/PreviewTab";
import { MessageData } from "../components/Message";
import AssistantTerminalPane, { AssistantItem, AssistantTerminalPaneRef, type AssistantCopyPayload } from "../components/AssistantTerminalPane";
import IRBIframe from "../components/IRBIframe";
import SubmissionsGallery from "../components/SubmissionsPlaceholder";
import { load_next_task } from "../functions/task_logic";
import { ENV } from "../config/env";
import { DiffEditor } from "@monaco-editor/react";
import UserSubmissions from "../components/UserSubmissions";
import { useUserStudyPopup } from "../components/UserStudyPopup";
import { TaskTimer } from "../components/TaskTimer";
import {
  GAME_REQUIRED_TASKS,
  getRequiredTasksForMode,
  getStudyTaskMode,
  isWebsiteRequirementTask,
  TIMED_TASKS,
  WEBSITE_REQUIREMENT_TASKS,
} from "../config/tasks";
import { useSnackbar } from "../components/SnackbarProvider";
import LoadingSpinner from "../components/LoadingSpinner";
import { formatDateOnly } from "../utils/dateFormat";
import { PASSWORD_HASH, hashString } from "../utils/password";
import { ERROR_TRY_AGAIN } from "../utils/constants";
import { downloadProjectAsRepository } from "../utils/downloadProject";
import { isInternalReviewerUser } from "../config/internalReviewers";
import { isMultiSubmissionUser } from "../config/multiSubmissionAccounts";
import { useSubmissionGalleryCounts } from "../hooks/useSubmissionGalleryCounts";

type CodeLogEvent = "save-shortcut" | "before-unload" | "preview-refresh" | "AI-refresh" | "keep" | "reject" | "keep_all" | "reject_all" | "download" | "undo" | "redo" | "copy_from_assistant";
type TaskEventName =
  | "loaded_in"
  | "timer_started"
  | "left_page"
  | "started_edits"
  | "started_ai_query"
  | "questions_generation_started"
  | "questions_generation_completed"
  | "continued_to_questions"
  | "timer_paused"
  | "timer_resumed"
  | "submitted";
type TimerAlertTone = "warning" | "critical";
type ViewedSubmission = {
  id: number | null;
  title: string;
  description: string | null;
  projectId: number | null;
  userId: number | null;
};

const normalizeTaskNameKey = (value: unknown): string => {
  if (typeof value !== 'string') return '';
  return value.trim().toLowerCase().replace(/-/g, '_');
};
const OPEN_ENDED_AI_MODE_SESSION_KEY = 'open-ended-ai-mode-by-task';

function HomeInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  
  // Use route protection hook
  const { isAuthenticated, isLoading } = useRouteProtection();
  const { user, token, refreshUser } = useAuth();
  const numericUserId = user?.id && !Number.isNaN(Number(user.id)) ? Number(user.id) : null;
  const isInternalReviewer = useMemo(
    () => isInternalReviewerUser(user ?? undefined),
    [user]
  );
  const allowsMultipleSubmissions = useMemo(
    () => isMultiSubmissionUser(user ?? undefined),
    [user]
  );
  const studyEnded = false;
  
  // This page now exclusively renders tasks.
  const activeTab = 'tasks';
  
  // If on /vibe without a task parameter, redirect to browse
  // Only redirect if searchParams are available (avoid redirecting during hydration)
  useEffect(() => {
    // Only redirect if we're on /vibe AND searchParams are loaded AND there's no task param
    if (pathname === '/vibe' && searchParams && !searchParams.get('task')) {
      router.replace('/browse');
    }
  }, [pathname, searchParams, router]);
  
  // All hooks must be called before any conditional returns
  const [isPending, startTransition] = useTransition();
  const [searchQuery, setSearchQuery] = useState("");
  const [filteredTasks, setFilteredTasks] = useState<any[]>([]);
  const submissionGalleryCounts = useSubmissionGalleryCounts(isInternalReviewer, filteredTasks);
  const { isSidebarOpen: sidebarOpen, toggleSidebar, isAssistantVisible: showAIAssistant, setIsAssistantVisible: setShowAIAssistant } = useSidebar();
  // Filter state
  const [showFilterModal, setShowFilterModal] = useState(false);
  const [statusFilters, setStatusFilters] = useState({
    'completed': true,
    'in-progress': true,
    'not-started': true
  });
  const [categoryFilters, setCategoryFilters] = useState({
    'open-ended': true,
    'replication': true
  });
  // Skill check recalc hook for submission flow
  const { recalculateState } = useUserStudyPopup();
  
  // Check for secret password bypass using hash comparison
  const [hasSecretPassword, setHasSecretPassword] = useState(false);
  
  useEffect(() => {
    const checkPassword = async () => {
      const password = searchParams?.get('password');
      if (password) {
        const passwordHash = await hashString(password);
        setHasSecretPassword(passwordHash === PASSWORD_HASH);
      } else {
        setHasSecretPassword(false);
      }
    };
    checkPassword();
  }, [searchParams]);
  
  // Generate background circle data once on mount - prevents re-triggering on state changes
  const backgroundStars = useMemo(() => {
    const colors = ['#3b82f6', '#8b5cf6', '#ec4899'];
    
    // Large stars (20)
    const largeStars = Array.from({ length: 20 }, () => ({
      color: colors[Math.floor(Math.random() * colors.length)],
      size: Math.random() * 4 + 2,
      left: Math.random() * 100,
      top: Math.random() * 100,
      opacity: Math.random() * 0.6 + 0.4,
    }));
    
    // Medium stars (40)
    const mediumStars = Array.from({ length: 40 }, () => ({
      color: colors[Math.floor(Math.random() * colors.length)],
      size: Math.random() * 2 + 1,
      left: Math.random() * 100,
      top: Math.random() * 100,
      opacity: Math.random() * 0.5 + 0.3,
    }));
    
    // Small dots (100)
    const smallDots = Array.from({ length: 100 }, () => ({
      color: colors[Math.floor(Math.random() * colors.length)],
      size: Math.random() * 1.5 + 0.5,
      left: Math.random() * 100,
      top: Math.random() * 100,
      opacity: Math.random() * 0.4 + 0.2,
    }));
    
    // Animated dots (12)
    const animatedColors = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981'];
    const animatedDots = Array.from({ length: 12 }, () => ({
      color: animatedColors[Math.floor(Math.random() * animatedColors.length)],
      size: Math.random() * 8 + 4,
      top: Math.random() * 100,
      duration: Math.random() * 30 + 40, // 40-70 seconds
      delay: Math.random() * 5, // 0-5 seconds
      direction: Math.random() > 0.5 ? 'left-to-right' : 'right-to-left',
      opacity: Math.random() * 0.6 + 0.4,
    }));
    
    return { largeStars, mediumStars, smallDots, animatedDots };
  }, []); // Empty dependency array - only generate once on mount
  
  // Clear snackbars when leaving the Browse page
  const { clearAllSnackbars, showSnackbar } = useSnackbar();
  const NAVIGATION_WARNING_THRESHOLD_MS = 2000;
  const LEAVE_STUDY_WARNING_MESSAGE =
    "Are you sure you want to leave the study? You need to complete this task in one sitting to be eligible for compensation.";
  useEffect(() => {
    // Cleanup function to clear snackbars when component unmounts (navigating away)
    return () => {
      clearAllSnackbars();
    };
  }, [clearAllSnackbars]);

  // Load initial settings from cookies (only on client side after hydration)
  const [theme, setTheme] = useState<'native' | 'light' | 'dark'>('native');
  const [assistantPlacement, setAssistantPlacement] = useState<'bottom' | 'side'>('side');
  
  // Load settings from cookies after hydration
  useEffect(() => {
    const settings = getUserSettingsCookie();
    setTheme(settings.theme);
    setAssistantPlacement(settings.aiAssistantPlacement);
  }, []);
  const [selectedTask, setSelectedTask] = useState<string | null>(null);
  
  // Detect playground mode - check selectedTask
  const isPlaygroundMode = selectedTask === 'playground';
  
  const [showCodingTerminal, setShowCodingTerminal] = useState(false);
  const [expandedTask, setExpandedTask] = useState<string | null>(null);
  // Left pane tabs when in a task
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const [tooltipText, setTooltipText] = useState("");
  const [tooltipLeft, setTooltipLeft] = useState(0);
  const [tooltipTop, setTooltipTop] = useState(0);
  const [tooltipPlaceAbove, setTooltipPlaceAbove] = useState(true);
  const [leftTab, setLeftTab] = useState<'task' | 'preview' | 'leaderboard' | 'submissions' | 'project-details'>('task');
  const [rightTab, setRightTab] = useState<'code' | 'submissions'>('code');
  const DEFAULT_TASK_TIMER_DURATION_SECONDS = 120 * 60;
  /** Passed to TaskTimer so only the timer badge re-renders every second, not the whole page. */
  const [initialTimerRemainingSeconds, setInitialTimerRemainingSeconds] = useState<number | null>(null);
  const [isSubmissionQuestionsPaneOpen, setIsSubmissionQuestionsPaneOpen] = useState(false);
  const [timerAlert, setTimerAlert] = useState<{ message: string; tone: TimerAlertTone; dismissible: boolean } | null>(null);
  const [showTimerExpiredModal, setShowTimerExpiredModal] = useState(false);
  const [hasTimedTaskStarted, setHasTimedTaskStarted] = useState(false);
  const [isStartingTimedTask, setIsStartingTimedTask] = useState(false);
  const [isSubmitModalExitLocked, setIsSubmitModalExitLocked] = useState(false);
  const [viewedSubmission, setViewedSubmission] = useState<ViewedSubmission | null>(null);
  
  // Vibe page layout state
  const [code, setCode] = useState("");
  const [editorHeight, setEditorHeight] = useState(0);
  const [customProjectTitle, setCustomProjectTitle] = useState<string>('');
  const [customProjectDescription, setCustomProjectDescription] = useState<string>('');
  const [suggestionIdx, setSuggestionIdx] = useState(0);
  const [telemetry, setTelemetry] = useState<any[]>([]);
  const [logProbs, setLogProbs] = useState<any>(null);
  const [messageAIIndex, setMessageAIIndex] = useState(0);
  const [isSpinning, setIsSpinning] = useState(false);
  const editorRef = useRef<any>(null);
  const actualEditorRef = useRef<any>(null);
  const chatRef = useRef<any>(null);
  const leftPaneRef = useRef<HTMLDivElement | null>(null);
  const pendingLeftWidthRef = useRef<number>(0);
  const rafScheduledRef = useRef<boolean>(false);
  const lastConstrainedWidthRef = useRef<number>(0);
  const [isSwapped, setIsSwapped] = useState(() => {
    if (typeof window === 'undefined') return false;
    const settings = getUserSettingsCookie();
    return settings.taskPreviewSwap;
  });
  const containerRef = useRef<HTMLDivElement | null>(null);
  const taskAbortControllerRef = useRef<AbortController | null>(null);
  const assistantTerminalPaneRef = useRef<AssistantTerminalPaneRef | null>(null);
  const assistantAbortControllerRef = useRef<AbortController | null>(null);
  const latestSuggestionsRef = useRef<string[]>([]);
  const fileMetadataRef = useRef<Record<string, { name: string; language?: string }>>({});
  const unloadLoggedRef = useRef(false);
  const taskLeftPageLoggedRef = useRef(false);
  const isTaskNavigatedAwayRef = useRef(false);
  const taskNavigationAwayStartRef = useRef<number | null>(null);
  const startedEditsLoggedRef = useRef(false);
  const startedAIQueryLoggedRef = useRef(false);
  const lastClickedSubmissionRef = useRef<ViewedSubmission | null>(null);
  const lastLoadedTaskKeyRef = useRef<string | null>(null);
  const isInitialMountRef = useRef(true);
  const historyClearedOnLoadRef = useRef(false);
  const filterModalRef = useRef<HTMLDivElement | null>(null);
  const timerAlertTimeoutRef = useRef<number | null>(null);
  const timerWarningKeysShownRef = useRef<Set<string>>(new Set());
  const timerExpiredModalShownRef = useRef(false);
  const isSubmissionQuestionsPaneOpenRef = useRef(false);
  const timedTaskFilesPendingRef = useRef<any[] | null>(null);
  const suppressCodeLogsRef = useRef(false);
  
  // Resize state
  const [leftColumnWidth, setLeftColumnWidth] = useState(0);
  const [isResizing, setIsResizing] = useState(false);
  const [taskInstructionHeight, setTaskInstructionHeight] = useState(0);
  const [isVerticalResizing, setIsVerticalResizing] = useState(false);
  const [isEditorResizing, setIsEditorResizing] = useState(false);
  
  // Pane visibility
  const [showTaskInstructions, setShowTaskInstructions] = useState(true);
  const [showCodeEditor, setShowCodeEditor] = useState(true);
  const [showTerminal, setShowTerminal] = useState(true);
  /** Debug console (terminal) open state in Preview tab, for code-log metadata. */
  const [debugTerminalOpen, setDebugTerminalOpen] = useState(true);
  
  // Task data
  const [taskDescriptions, setTaskDescriptions] = useState<string[]>([]);
  const [initialFiles, setInitialFiles] = useState<any[]>([]);
  const [isLoadingFiles, setIsLoadingFiles] = useState(false);
  const [currentFiles, setCurrentFiles] = useState<any[]>([]);
  const [testCases, setTestCases] = useState<any[]>([]);
  const previewTabRef = useRef<PreviewTabRef>(null);
  const [allTasks, setAllTasks] = useState<any[]>([]);
  const [isLoadingTasks, setIsLoadingTasks] = useState(true);
  const websiteRequirementsSkipped =
    isWebsiteRequirementsPhaseSkippedForStudy(user?.settings) || allowsMultipleSubmissions;

  const buildTaskListForCurrentMode = useCallback((tasks: any[]) => {
    const mode = getStudyTaskMode(tasks, websiteRequirementsSkipped);
    if (mode === 'website-requirements' || allowsMultipleSubmissions) {
      return tasks;
    }

    const playgroundCompleted = isPlaygroundCompletedFromSettings(user?.settings);
    const playgroundTask = {
      id: 'playground',
      name: 'Playground',
      title: 'Playground Tutorial',
      description: '<p>We recommend that you begin here: a practice environment where you can experiment with coding and test the AI assistant. Your changes won\'t be saved, and you can try out different features to get familiar with the interface.</p><p>Use this space to:</p><ul><li>Practice coding with the AI assistant</li><li>Test different features and functionality</li><li>Get comfortable with the editor and preview</li><li>Experiment freely without worrying about submissions</li></ul><p><strong>Note:</strong> This is a sandbox environment - your work will not be saved or logged.</p>',
      difficulty: 'Beginner',
      appType: 'practice',
      estimatedTime: '5-10 min',
      tags: ['practice', 'tutorial', 'sandbox'],
      preview: 'A practice environment for testing and learning',
      status: playgroundCompleted ? 'completed' : 'not-started',
      saved: false,
      label: 'Practice',
      category: 'tutorial'
    };

    return [playgroundTask, ...tasks];
  }, [user?.settings, websiteRequirementsSkipped, allowsMultipleSubmissions]);
  
  // Chat state
  const [chatHistory, setChatHistory] = useState<any[]>([
    { role: "system", content: "help with python" },
  ]);
  const [messages, setMessages] = useState<MessageData[]>([
    { text: "How can I help you today?", sender: "bot" },
  ]);
  
  type AssistantMode = 'agent' | 'ask' | 'brainstorm';
  const isAssistantMode = (value: unknown): value is AssistantMode =>
    value === 'agent' || value === 'ask' || value === 'brainstorm';
  const defaultAssistantModeState = <T,>(initialValue: T): Record<AssistantMode, T> => ({
    agent: initialValue,
    ask: initialValue,
    brainstorm: initialValue,
  });
  const getStoredOpenEndedMode = (taskName: string): AssistantMode | null => {
    if (typeof window === 'undefined' || !taskName) return null;
    try {
      const raw = window.sessionStorage.getItem(OPEN_ENDED_AI_MODE_SESSION_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const savedMode = parsed?.[taskName];
      return isAssistantMode(savedMode) ? savedMode : null;
    } catch {
      return null;
    }
  };
  const setStoredOpenEndedMode = (taskName: string, mode: AssistantMode) => {
    if (typeof window === 'undefined' || !taskName) return;
    try {
      const raw = window.sessionStorage.getItem(OPEN_ENDED_AI_MODE_SESSION_KEY);
      const parsed = raw ? JSON.parse(raw) as Record<string, unknown> : {};
      window.sessionStorage.setItem(
        OPEN_ENDED_AI_MODE_SESSION_KEY,
        JSON.stringify({ ...parsed, [taskName]: mode })
      );
    } catch {
      // Ignore storage parsing/quota errors.
    }
  };

  // Assistant terminal state
  const [assistantMessagesByMode, setAssistantMessagesByMode] = useState<Record<AssistantMode, AssistantItem[]>>(
    defaultAssistantModeState<AssistantItem[]>([])
  );
  const [assistantInputByMode, setAssistantInputByMode] = useState<Record<AssistantMode, string>>(
    defaultAssistantModeState('')
  );
  const [summaryGeneratedByMode, setSummaryGeneratedByMode] = useState<Record<AssistantMode, boolean>>(
    defaultAssistantModeState(false)
  );
  // Assistant mode: each mode maps to its own backend endpoint.
  const [agentMode, setAgentMode] = useState<AssistantMode>('agent');
  const assistantMessages = assistantMessagesByMode[agentMode] ?? [];
  const assistantInputValue = assistantInputByMode[agentMode] ?? '';
  const summaryGenerated = summaryGeneratedByMode[agentMode] ?? false;
  const modeLabelMap: Record<AssistantMode, string> = {
    agent: 'Agent Mode',
    ask: 'Ask Mode',
    brainstorm: 'Brainstorm Mode',
  };
  const modeInitialMessageMap: Record<AssistantMode, string> = {
    agent: "Hello, I'm in Agent Mode! Tell me what you want to change, and I'll edit your code directly.",
    ask: "Hello, I'm in Ask Mode! I can answer questions about your current code and show examples, but I can't run or apply changes directly.",
    brainstorm: "Hello, I'm in Brainstorm Mode! I can help you come up with ideas for your project.",
  };
  const assistantModeRef = useRef<AssistantMode>('agent');
  useEffect(() => {
    assistantModeRef.current = agentMode;
  }, [agentMode]);
  // In-memory count of AI prompts this task session (not reset when user clears chat / trashcan)
  const assistantPromptCountRef = useRef(0);
  useEffect(() => {
    assistantPromptCountRef.current = 0;
  }, [selectedTask]);
  // Ref to track latest messages for snapshot saving (avoids stale closure issues)
  const assistantMessagesByModeRef = useRef<Record<AssistantMode, AssistantItem[]>>(
    defaultAssistantModeState<AssistantItem[]>([])
  );
  useEffect(() => {
    assistantMessagesByModeRef.current = assistantMessagesByMode;
  }, [assistantMessagesByMode]);

  const setAssistantMessagesForMode = useCallback((
    mode: AssistantMode,
    updater: AssistantItem[] | ((prev: AssistantItem[]) => AssistantItem[])
  ) => {
    setAssistantMessagesByMode((prev) => {
      const previousModeMessages = prev[mode] ?? [];
      const nextModeMessages = typeof updater === 'function'
        ? (updater as (prev: AssistantItem[]) => AssistantItem[])(previousModeMessages)
        : updater;
      return {
        ...prev,
        [mode]: nextModeMessages,
      };
    });
  }, []);
  const setAssistantInputForMode = useCallback((mode: AssistantMode, value: string) => {
    setAssistantInputByMode((prev) => ({ ...prev, [mode]: value }));
  }, []);
  const handleAssistantModeChange = useCallback((nextMode: AssistantMode) => {
    setAgentMode((prevMode) => {
      if (prevMode === nextMode) return prevMode;

      setAssistantInputByMode((prevInputs) => {
        const previousModeInput = prevInputs[prevMode] ?? '';
        const nextModeInput = prevInputs[nextMode] ?? '';
        // Keep draft text visible across mode switches when target mode has no draft yet.
        if (previousModeInput.length === 0 || nextModeInput.length > 0) {
          return prevInputs;
        }
        return {
          ...prevInputs,
          [nextMode]: previousModeInput,
        };
      });

      return nextMode;
    });
  }, []);
  const setSummaryGeneratedForMode = useCallback((mode: AssistantMode, value: boolean) => {
    setSummaryGeneratedByMode((prev) => ({ ...prev, [mode]: value }));
  }, []);
  const setAssistantInputValue = useCallback((value: string) => {
    setAssistantInputForMode(agentMode, value);
  }, [agentMode, setAssistantInputForMode]);
  const [inputValue, setInputValue] = useState("");
  const [awaitingResponse, setAwaitingResponse] = useState(false);
  const [awaitingManualSuggestions, setAwaitingManualSuggestions] = useState(false);
  const EXPERIMENT_GROUP_TASKS = new Set<string>([
    'website_tutorial_intro',
    'zic_zac_zoe',
  ]);
  const FORCE_CHAT_TASKS = new Set<string>([
    'website_tutorial_follow_up',
    'zic_zac_zoe_follow_up',
  ]);
  const AI_ASSISTANT_DETAILS_TASKS = new Set<string>([
    'website_tutorial_intro',
    'website_tutorial_follow_up',
    'zic_zac_zoe',
    'zic_zac_zoe_follow_up',
  ]);
  const normalizeExperimentGroup = (value: unknown): 'agent' | 'ask' | null => {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toLowerCase();
    if (normalized === 'agent') return 'agent';
    if (normalized === 'chat' || normalized === 'ask') return 'ask';
    return null;
  };
  // No per-session conversation ID; backend maintains a single global history list
  
  // Agent changes state
  const [pendingAgentChanges, setPendingAgentChanges] = useState<any>(null);
  // Track which files were modified in the most recent interaction
  const filesModifiedInCurrentInteractionRef = useRef<Set<string>>(new Set());

  // Undo/Redo state for AI assistant - linear history
  interface CodeSnapshot {
    codeState: Record<string, string>; // fileId -> content
    messages: AssistantItem[];
    timestamp: number;
  }
  const [history, setHistory] = useState<CodeSnapshot[]>([]);
  const [historyIndex, setHistoryIndex] = useState<number>(0); // Index of current snapshot (0-based)
  const historyIndexRef = useRef(0); // Ref to track current index for synchronous reads
  useEffect(() => {
    historyIndexRef.current = historyIndex;
  }, [historyIndex]);
  const MAX_HISTORY = 10;
  
  // Save snapshot (called on load and after agent completes)
  // Can accept codeState directly (for agent updates) or read from editor (for initial snapshot)
  const saveSnapshot = useCallback((messages?: AssistantItem[], codeState?: Record<string, string>) => {
    let finalCodeState: Record<string, string>;
    
    if (codeState) {
      // Use provided code state (from agent updates)
      finalCodeState = codeState;
    } else if (actualEditorRef?.current?.getAllFileContents) {
      // Read from editor (for initial snapshot)
      finalCodeState = actualEditorRef.current.getAllFileContents();
    } else {
      console.warn('⚠️ Cannot save snapshot: no codeState provided and editor ref not available', {
        hasRef: !!actualEditorRef?.current,
        hasMethod: !!actualEditorRef?.current?.getAllFileContents,
        isPlayground: isPlaygroundMode,
      });
      return;
    }
    
    // Use provided messages, or get latest from ref to avoid stale closure
    const modeForSnapshot = assistantModeRef.current;
    const messagesToSave = messages ?? assistantMessagesByModeRef.current[modeForSnapshot] ?? [];
    
      const snapshot: CodeSnapshot = {
      codeState: { ...finalCodeState },
      messages: [...messagesToSave],
        timestamp: Date.now(),
      };
    
      // Use functional updates to read current state
        setHistory(prev => {
      const currentIdx = historyIndexRef.current;
      // If we're at the latest snapshot, append new one
      // Otherwise, remove everything after current index and append
      const isAtLatest = currentIdx === prev.length - 1;
      const newHistory = isAtLatest
            ? [...prev, snapshot]
            : [...prev.slice(0, currentIdx + 1), snapshot];
      
          // Keep only last MAX_HISTORY entries
          const finalHistory = newHistory.slice(-MAX_HISTORY);
      
      // Update index to point to the new latest snapshot
      const newIndex = finalHistory.length - 1;
      setHistoryIndex(newIndex);
      
      return finalHistory;
        });
  }, [isPlaygroundMode]);
  
  // Task state
  const [responseId, setResponseId] = useState("");
  const [taskId, setTaskId] = useState<string>("");
  const [currentTaskMeta, setCurrentTaskMeta] = useState<{
    id: string;
    name?: string;
    projectId?: number;
    votingStartDate?: string | null;
    votingEndDate?: string | null;
    codeStartDate?: string | null;
  } | null>(null);
  // Set assistant mode by task policy:
  // - Playground + non-website-requirement tasks: default to agent (users can change mode)
  // - Website requirement tasks:
  //   - First two tasks: fixed by experiment_group (agent/chat)
  //   - Follow-up two tasks: forced chat mode
  useEffect(() => {
    if (selectedTask === 'playground') {
      handleAssistantModeChange('agent');
      return;
    }

    const taskName = normalizeTaskNameKey(currentTaskMeta?.name);
    if (!taskName) return;

    if (!WEBSITE_REQUIREMENT_TASKS.includes(taskName as any)) {
      const storedMode = getStoredOpenEndedMode(taskName);
      handleAssistantModeChange(storedMode ?? 'agent');
      return;
    }

    if (EXPERIMENT_GROUP_TASKS.has(taskName)) {
      const experimentGroup = normalizeExperimentGroup(user?.settings?.experiment_group);
      handleAssistantModeChange(experimentGroup ?? 'agent');
      return;
    }

    if (FORCE_CHAT_TASKS.has(taskName)) {
      handleAssistantModeChange('ask');
      return;
    }

    // Fallback for any other website-requirement task names.
    handleAssistantModeChange('ask');
  }, [selectedTask, currentTaskMeta?.name, user?.settings?.experiment_group, handleAssistantModeChange]);

  // Persist AI mode per open-ended task for the current browser session.
  useEffect(() => {
    if (selectedTask === 'playground') return;
    const taskName = normalizeTaskNameKey(currentTaskMeta?.name);
    if (!taskName) return;
    if (WEBSITE_REQUIREMENT_TASKS.includes(taskName as any)) return;
    setStoredOpenEndedMode(taskName, agentMode);
  }, [selectedTask, currentTaskMeta?.name, agentMode]);

  const [expCondition, setExpCondition] = useState("");
  const [workerId, setWorkerId] = useState("");
  const [model, setModel] = useState("gpt-3.5-turbo");
  const [taskNameDB, setTaskNameDB] = useState("");
  const [taskIndex, setTaskIndex] = useState(0);
  const [maxTokensTask, setMaxTokensTask] = useState(2000);
  const [unitTests, setUnitTests] = useState<string[]>([]);
  const [functionSignatures, setFunctionSignatures] = useState<string[]>([]);
  const [chatLogProbs, setChatLogProbs] = useState("");
  const [modelAutocomplete, setModelAutocomplete] = useState("Off");
  const [modelChat, setModelChat] = useState("gpt-4o");
  const [proactive, setProactive] = useState(false);
  const [suggestion_max_options, setSuggestionMaxOptions] = useState(3);
  const [insert_cursor, setInsertCursor] = useState(true);
  const [proactive_refresh_time_active, setProactiveRefreshTimeActive] = useState(15_000);
  const [proactive_refresh_time_inactive, setProactiveRefreshTimeInactive] = useState(30_000);
  const [proactive_delete_time, setProactiveDeleteTime] = useState(60_000);
  const [isMac, setIsMac] = useState(false); // Will be set after mount to detect platform

  const getCodeByLanguage = useCallback((): Record<string, string> | null => {
    const editorApi = actualEditorRef?.current;
    if (!editorApi || typeof editorApi.getAllFileContents !== 'function') {
      return null;
    }

    try {
      const contents: Record<string, string> = editorApi.getAllFileContents() || {};
      const metadataMap = fileMetadataRef.current || {};
      const result: Record<string, string> = {};

      Object.entries(contents).forEach(([fileId, content]) => {
        const meta = metadataMap[fileId] || { name: fileId };
        const key = determineLanguageKey(meta.language, meta.name || fileId);
        if (key) {
          result[key] = typeof content === 'string' ? content : String(content ?? '');
        }
      });

      return Object.keys(result).length > 0 ? result : null;
    } catch (error) {
      console.warn('Failed to collect code by language', error);
      return null;
    }
  }, [actualEditorRef]);

  const buildCodeByLanguageFromState = useCallback((codeState: Record<string, string>) => {
    const metadataMap = fileMetadataRef.current || {};
    const result: Record<string, string> = {};

    Object.entries(codeState || {}).forEach(([fileId, content]) => {
      const meta = metadataMap[fileId] || { name: fileId };
      const key = determineLanguageKey(meta.language, meta.name || fileId);
      if (key) {
        result[key] = typeof content === 'string' ? content : String(content ?? '');
      }
    });

    return Object.keys(result).length > 0 ? result : null;
  }, []);

  const resolveActiveTaskEventContext = useCallback(() => {
    if (!user?.id || !selectedTask || selectedTask === "playground" || !currentTaskMeta) {
      return null;
    }

    const numericUid = Number.parseInt(user.id, 10);
    if (!Number.isFinite(numericUid)) {
      return null;
    }

    const task = allTasks.find((item: any) => item?.id === currentTaskMeta.id);
    const projectId = currentTaskMeta.projectId ?? task?.projectId;
    if (!projectId) {
      return null;
    }

    return {
      userId: numericUid,
      projectId,
      taskId: currentTaskMeta.id,
      taskName: currentTaskMeta?.name ?? null,
    };
  }, [user?.id, selectedTask, currentTaskMeta, allTasks]);

  const sendTaskEvent = useCallback(async (eventName: TaskEventName, metadata?: Record<string, any>) => {
    const context = resolveActiveTaskEventContext();
    if (!context) return;
    const clickedSubmission = lastClickedSubmissionRef.current;

    try {
      await fetch(`${ENV.BACKEND_URL}/api/task-events`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          user_id: context.userId,
          project_id: context.projectId,
          event_name: eventName,
          event_metadata: {
            task_id: context.taskId,
            task_name: context.taskName,
            clicked_submission: Boolean(clickedSubmission),
            clicked_submission_id: clickedSubmission?.id ?? null,
            clicked_submission_title: clickedSubmission?.title ?? null,
            clicked_submission_project_id: clickedSubmission?.projectId ?? null,
            clicked_submission_user_id: clickedSubmission?.userId ?? null,
            ...(metadata || {}),
          },
        }),
      });
    } catch (error) {
      console.warn("Failed to log task event", error);
    }
  }, [resolveActiveTaskEventContext]);

  const sendTaskEventBeacon = useCallback((eventName: TaskEventName, metadata?: Record<string, any>) => {
    const context = resolveActiveTaskEventContext();
    if (!context) return;
    const clickedSubmission = lastClickedSubmissionRef.current;

    const payload = JSON.stringify({
      user_id: context.userId,
      project_id: context.projectId,
      event_name: eventName,
      event_metadata: {
        task_id: context.taskId,
        task_name: context.taskName,
        clicked_submission: Boolean(clickedSubmission),
        clicked_submission_id: clickedSubmission?.id ?? null,
        clicked_submission_title: clickedSubmission?.title ?? null,
        clicked_submission_project_id: clickedSubmission?.projectId ?? null,
        clicked_submission_user_id: clickedSubmission?.userId ?? null,
        ...(metadata || {}),
      },
    });
    const url = `${ENV.BACKEND_URL}/api/task-events`;

    try {
      let dispatched = false;
      if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
        dispatched = navigator.sendBeacon(url, new Blob([payload], { type: "application/json" }));
      }
      if (!dispatched) {
        fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: payload,
          keepalive: true,
        }).catch((error) => console.warn("Failed to beacon task event", error));
      }
    } catch (error) {
      console.warn("Failed to dispatch task event beacon", error);
    }
  }, [resolveActiveTaskEventContext]);

  const markStartedEdits = useCallback((source: "manual" | "ai_agent") => {
    if (startedEditsLoggedRef.current) return;
    startedEditsLoggedRef.current = true;
    void sendTaskEvent("started_edits", { source });
  }, [sendTaskEvent]);

  const markStartedAIQuery = useCallback((queryType: string) => {
    if (startedAIQueryLoggedRef.current) return;
    startedAIQueryLoggedRef.current = true;
    void sendTaskEvent("started_ai_query", { query_type: queryType });
  }, [sendTaskEvent]);

  const buildCodeLogPayload = useCallback((event: CodeLogEvent, context: Record<string, any> = {}) => {
    // Skip code logging in playground mode - no database saving or logging
    if (isPlaygroundMode || selectedTask === 'playground') {
      return null;
    }
    
    if (!user?.id) {
      return null;
    }
    
    const numericUserId = Number.parseInt(user.id, 10);
    if (!Number.isFinite(numericUserId)) {
      return null;
    }
    
    if (!selectedTask || !currentTaskMeta) {
      return null;
    }
    
    const projectId = currentTaskMeta.projectId ?? allTasks.find((task: any) => task?.id === currentTaskMeta.id)?.projectId;
    if (!projectId) {
      return null;
    }
    
    const codeOverride = context.codeByLanguage;
    const codeByLanguage = codeOverride || getCodeByLanguage();
    if (!codeByLanguage) {
      return null;
    }
    
    // Check if diffEditor is active by checking if pendingAgentChanges has any modified files
    const isDiffMode = !!(pendingAgentChanges?.modified && 
                         Object.keys(pendingAgentChanges.modified).length > 0);
    
    // Check if this is a save shortly after AI code was loaded
    const isAiGeneratedMode = event === 'save-shortcut' && 
                              aiCodeLoadedTimestampRef.current !== null &&
                              (Date.now() - aiCodeLoadedTimestampRef.current) <= AI_CODE_LOAD_WINDOW_MS;
    
    // If in diff mode, convert original code from fileId-based to language-based
    let originalCodeByLanguage: Record<string, string> | undefined = undefined;
    if (isDiffMode && pendingAgentChanges?.original) {
      originalCodeByLanguage = {};
      const metadataMap = fileMetadataRef.current || {};
      
      Object.entries(pendingAgentChanges.original).forEach(([fileId, originalContent]) => {
        const meta = metadataMap[fileId] || { name: fileId };
        const key = determineLanguageKey(meta.language, meta.name || fileId);
        if (key && originalContent && originalCodeByLanguage) {
          originalCodeByLanguage[key] = typeof originalContent === 'string' ? originalContent : String(originalContent ?? '');
        }
      });
    }
    
    const metadata = {
      event,
      taskId: currentTaskMeta.id,
      projectId,
      taskName: currentTaskMeta?.name ?? null,
      // Explicit UI context for downstream analytics (independent from stored code-log mode).
      editorMode: isDiffMode ? 'diff' : 'regular',
      triggeredAt: new Date().toISOString(),
      leftTab,
      showCodingTerminal,
      isPreviewVisible: showCodingTerminal && selectedTask && leftTab === 'preview',
      debug_terminal_open: leftTab === 'preview' ? debugTerminalOpen : false,
      codeLengths: Object.fromEntries(
        Object.entries(codeByLanguage).map(([key, value]) => [key, String(value ?? '').length])
      ),
      files: Object.fromEntries(
        Object.entries(fileMetadataRef.current || {}).map(([fileId, meta]) => [
          fileId,
          {
            name: meta?.name,
            language: meta?.language,
          },
        ])
      ),
      // Include original code in metadata when in diff mode
      ...(isDiffMode && originalCodeByLanguage && Object.keys(originalCodeByLanguage).length > 0 
          ? { originalCode: originalCodeByLanguage } 
          : {}),
      ...(event === 'AI-refresh'
          ? { aiPromptEditorMode: isDiffMode ? 'diff' : 'regular' }
          : {}),
      ...context,
    };
    
    // Determine mode: keep/reject actions take precedence, then download, then undo/redo, then copy_from_assistant, then AI (for automatic AI refreshes), then AI_generated (for saves after AI code), then diff, then regular
    let mode: string;
    if (event === 'keep' || event === 'keep_all') {
      mode = event === 'keep_all' ? 'keep_all' : 'keep';
    } else if (event === 'reject' || event === 'reject_all') {
      mode = event === 'reject_all' ? 'reject_all' : 'reject';
    } else if (event === 'download') {
      mode = 'download';
    } else if (event === 'undo' || event === 'redo') {
      mode = event;
    } else if (event === 'copy_from_assistant') {
      mode = 'copy';
    } else if (event === 'AI-refresh') {
      mode = 'AI';
    } else if (isAiGeneratedMode) {
      mode = 'AI_generated';
    } else if (isDiffMode) {
      mode = 'diff';
    } else {
      mode = 'regular';
    }
    
    return {
      userId: numericUserId,
      projectId,
      taskId: currentTaskMeta.id,
      mode,
      event,
      code: codeByLanguage,
      metadata,
    };
  }, [user, selectedTask, currentTaskMeta, getCodeByLanguage, leftTab, showCodingTerminal, debugTerminalOpen, allTasks, pendingAgentChanges]);

  const sendCodeLog = useCallback(async (event: CodeLogEvent, context: Record<string, any> = {}) => {
    if (suppressCodeLogsRef.current) return;
    const payload = buildCodeLogPayload(event, context);
    if (!payload) return;
    
    try {
      await fetch(`${ENV.BACKEND_URL}/api/code-logs`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      console.warn('Failed to log code snapshot', error);
    }
  }, [buildCodeLogPayload]);

  const sendCodeLogBeacon = useCallback((event: CodeLogEvent, context: Record<string, any> = {}) => {
    if (suppressCodeLogsRef.current) return;
    const payload = buildCodeLogPayload(event, context);
    if (!payload) return;
    
    if (event === 'before-unload') {
      if (unloadLoggedRef.current) return;
      unloadLoggedRef.current = true;
    }
    
    const url = `${ENV.BACKEND_URL}/api/code-logs`;
    const body = JSON.stringify(payload);
    
    try {
      let dispatched = false;
    
      if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
        const blob = new Blob([body], { type: 'application/json' });
        dispatched = navigator.sendBeacon(url, blob);
      }
    
      if (!dispatched) {
        fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body,
          keepalive: true,
        }).catch(error => console.warn('Failed to beacon code snapshot', error));
      }
    } catch (error) {
      console.warn('Failed to dispatch code snapshot beacon', error);
    }
  }, [buildCodeLogPayload]);

  // Undo function - go back in history
  const handleUndo = useCallback(() => {
    if (history.length === 0) return;
    
    // Can't go back further than first snapshot
    if (historyIndex <= 0) return;
    
    const targetIdx = historyIndex - 1;
    const snapshot = history[targetIdx];
    
    // Clear pending agent changes - this will hide diff editors and show normal editors
    setPendingAgentChanges(null);
    
    // Restore code state - update all files
    Object.entries(snapshot.codeState).forEach(([fileId, content]) => {
      if (actualEditorRef?.current?.updateFileContent) {
        actualEditorRef.current.updateFileContent(fileId, content);
      }
    });
    
    // Restore agent-mode messages (snapshots are generated from agent code-edit runs)
    setAssistantMessagesForMode('agent', snapshot.messages);
    
    // Update history index
    setHistoryIndex(targetIdx);
    const codeByLanguage = buildCodeByLanguageFromState(snapshot.codeState || {});
    if (codeByLanguage) {
      void sendCodeLog('undo', { codeByLanguage });
    }
  }, [history, historyIndex, buildCodeByLanguageFromState, sendCodeLog, setAssistantMessagesForMode]);
    
  // Refresh preview when history index changes (after undo/redo)
  useEffect(() => {
    // Small delay to ensure editor state has updated
    const timeoutId = setTimeout(() => {
      try {
        previewTabRef.current?.refreshPreview();
      } catch (error) {
        console.warn('Failed to refresh preview after history change:', error);
      }
    }, 50);
    
    return () => clearTimeout(timeoutId);
  }, [historyIndex, history.length]);
  
  // Redo function - go forward in history
  const handleRedo = useCallback(() => {
    if (history.length === 0) return;
    
    // Already at latest
    if (historyIndex >= history.length - 1) return;
    
    const targetIdx = historyIndex + 1;
    const snapshot = history[targetIdx];
    
    // Clear pending agent changes - this will hide diff editors and show normal editors
    setPendingAgentChanges(null);
    
    // Restore code state - update all files
    Object.entries(snapshot.codeState).forEach(([fileId, content]) => {
      if (actualEditorRef?.current?.updateFileContent) {
        actualEditorRef.current.updateFileContent(fileId, content);
      }
    });
    
    // Restore agent-mode messages (snapshots are generated from agent code-edit runs)
    setAssistantMessagesForMode('agent', snapshot.messages);
    
    // Update history index
    setHistoryIndex(targetIdx);
    const codeByLanguage = buildCodeByLanguageFromState(snapshot.codeState || {});
    if (codeByLanguage) {
      void sendCodeLog('redo', { codeByLanguage });
    }
  }, [history, historyIndex, buildCodeByLanguageFromState, sendCodeLog, setAssistantMessagesForMode]);
  
  // Check if undo/redo is available
  const canUndo = useMemo(() => {
    return history.length > 0 && historyIndex > 0;
  }, [history.length, historyIndex]);
  
  const canRedo = useMemo(() => {
    return history.length > 0 && historyIndex < history.length - 1;
  }, [history.length, historyIndex]);
  
  // Save initial snapshot when files are loaded with actual content
  useEffect(() => {
    // Only proceed if files are loaded, not loading, and editor is available
    if (initialFiles.length > 0 && !isLoadingFiles && actualEditorRef?.current?.getAllFileContents) {
      // Check if initialFiles actually have content (not just empty files)
      const hasContent = initialFiles.some(f => f?.content && String(f.content).trim().length > 0);
      
      if (!hasContent) {
        return;
      }
      
      // Wait a bit for editor to be ready and have loaded the content
      const timer = setTimeout(() => {
        try {
          const initialCodeState = actualEditorRef.current.getAllFileContents();
          
          // Check if the code state has actual content (not just empty strings)
          const codeStateEntries = Object.entries(initialCodeState);
          const hasActualContent = codeStateEntries.some(([_, content]) => 
            content && String(content).trim().length > 0
          );
          
          // Only save if we have files with actual content
          if (hasActualContent && codeStateEntries.length > 0) {
            const initialSnapshot = {
              codeState: { ...initialCodeState },
              messages: [] as AssistantItem[],
              timestamp: Date.now(),
            };
            setHistory([initialSnapshot]);
            setHistoryIndex(0);
          }
        } catch (error) {
          console.error('Failed to save initial snapshot:', error);
        }
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [initialFiles, isLoadingFiles]);
  
  // Detect platform after mount
  useEffect(() => {
    setIsMac(typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform));
  }, []);

  // Mark initial mount as complete after first render
  useEffect(() => {
    isInitialMountRef.current = false;
  }, []);

  // Clear agent history when user first navigates to the page (once per page load)
  useEffect(() => {
    if (!isLoading && isAuthenticated && numericUserId !== null && !historyClearedOnLoadRef.current) {
      historyClearedOnLoadRef.current = true;
      // Clear history on first page load
      fetch(`${ENV.BACKEND_URL}/api/agent-history/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userId: numericUserId,
        }),
      }).catch((e) => {
        // Silently fail - clearing history is best-effort
      });
    }
  }, [isLoading, isAuthenticated, numericUserId]); // Run when auth state is ready

  // Save settings to cookies when they change (but not on initial mount)
  useEffect(() => {
    if (!isInitialMountRef.current) {
      updateUserSetting('theme', theme);
    }
  }, [theme]);

  useEffect(() => {
    if (!isInitialMountRef.current) {
      updateUserSetting('aiAssistantPlacement', assistantPlacement);
    }
  }, [assistantPlacement]);

  useEffect(() => {
    if (!isInitialMountRef.current) {
      updateUserSetting('taskPreviewSwap', isSwapped);
    }
  }, [isSwapped]);

  // Initialize layout with 1/3 left, 2/3 right split
  useEffect(() => {
    const init = () => {
      const container = containerRef.current;
      const padding = 16; // matches ml-12/px and overall layout padding used elsewhere
      const handleWidth = 4;
      const viewportHeight = window.innerHeight - 32;
      const halfHeight = viewportHeight * 0.5;
      if (container) {
        const rect = container.getBoundingClientRect();
        const containerWidth = rect.width;
        // Set left column to 1/3 of container width
        const leftWidth = (containerWidth - handleWidth) * (1/3);
        setLeftColumnWidth(leftWidth);
      }
      setTaskInstructionHeight(halfHeight);
      setEditorHeight(halfHeight);
    };
    // Use rAF to ensure layout is measured after paint
    const raf = requestAnimationFrame(init);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Ensure width is set when showCodingTerminal becomes true (safety net)
  useEffect(() => {
    if (showCodingTerminal && selectedTask && leftColumnWidth === 0) {
      const container = containerRef.current;
      if (container) {
        const rect = container.getBoundingClientRect();
        const handleWidth = 4;
        const leftWidth = (rect.width - handleWidth) * (1/3);
        setLeftColumnWidth(leftWidth);
      } else {
        // Fallback: estimate based on viewport width
        const sidebarWidth = sidebarOpen ? 256 : 48;
        const estimatedContainerWidth = window.innerWidth - sidebarWidth - 48;
        const handleWidth = 4;
        const leftWidth = (estimatedContainerWidth - handleWidth) * (1/3);
        setLeftColumnWidth(leftWidth);
      }
    }
  }, [showCodingTerminal, selectedTask, leftColumnWidth, sidebarOpen]);

  // Adjust heights on window resize to prevent overflow while preserving padding
  useEffect(() => {
    const handleResize = () => {
      const containerHeight = window.innerHeight - 32;

      // Constrain Task Instructions height between 25% and 75% of viewport-like height
      const tiMin = (containerHeight * 25) / 100;
      const tiMax = (containerHeight * 75) / 100;
      setTaskInstructionHeight(prev => Math.max(tiMin, Math.min(tiMax, prev || 0)));

      // Constrain Editor height between 20% and 70% to mirror drag constraints
      const edMin = (containerHeight * 20) / 100;
      const edMax = (containerHeight * 70) / 100;
      setEditorHeight(prev => Math.max(edMin, Math.min(edMax, prev || 0)));

      try {
        (actualEditorRef.current as any)?.layout?.();
      } catch (e) {
        // no-op
      }
    };

    // Use rAF to batch during continuous resizing
    let rafId: number | null = null;
    const onResize = () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(handleResize);
    };

    window.addEventListener('resize', onResize);
    return () => {
      if (rafId) cancelAnimationFrame(rafId);
      window.removeEventListener('resize', onResize);
    };
  }, []);

  // Toggle AI assistant when the editor tab-bar robot button is clicked
  useEffect(() => {
    const openAIHandler = () => setShowAIAssistant(!showAIAssistant);
    window.addEventListener('open-ai-assistant', openAIHandler as EventListener);
    return () => {
      window.removeEventListener('open-ai-assistant', openAIHandler as EventListener);
    };
  }, [showAIAssistant, setShowAIAssistant]);

  const isViewSubmissionsUnlocked = useMemo(() => {
    // Disable submissions viewing in playground mode.
    if (isPlaygroundMode) return false;

    // If we don't have task metadata, keep submissions locked.
    if (!currentTaskMeta?.name) return false;

    // Lock voting/submissions only for required game tasks (currently: platformer) for normal participants.
    if (GAME_REQUIRED_TASKS.includes(currentTaskMeta.name as any)) {
      return !!isInternalReviewer;
    }

    // All other tasks should always allow viewing submissions.
    return true;
  }, [currentTaskMeta?.name, isPlaygroundMode, isInternalReviewer]);

  const isWebsiteRequirementsTaskSelected = useMemo(() => {
    if (selectedTask === 'playground' || isPlaygroundMode) return false;

    const selectedTaskFromList = allTasks.find((task: any) => task.id === selectedTask);
    if (selectedTaskFromList) {
      return isWebsiteRequirementTask(selectedTaskFromList);
    }

    // Fallback while task list metadata is still loading.
    if (currentTaskMeta) {
      return isWebsiteRequirementTask(currentTaskMeta);
    }

    return false;
  }, [allTasks, selectedTask, currentTaskMeta, isPlaygroundMode]);

  // For website_requirements tasks, only show the task UI once task type and experiment_group have loaded
  const isWebsiteRequirementsDataReady = useMemo(() => {
    if (!isWebsiteRequirementsTaskSelected) return true;
    const hasTaskType = !!currentTaskMeta?.name;
    const hasExperimentGroup = user != null; // user.settings (incl. experiment_group) comes with auth
    return hasTaskType && hasExperimentGroup;
  }, [isWebsiteRequirementsTaskSelected, currentTaskMeta?.name, user]);

  const selectedTaskName = useMemo(() => {
    if (selectedTask === 'playground' || isPlaygroundMode) return null;

    const selectedTaskFromList = allTasks.find((task: any) => task.id === selectedTask);
    if (selectedTaskFromList?.name) {
      return normalizeTaskNameKey(selectedTaskFromList.name);
    }

    if (currentTaskMeta?.name) {
      return normalizeTaskNameKey(currentTaskMeta.name);
    }

    return null;
  }, [allTasks, selectedTask, currentTaskMeta?.name, isPlaygroundMode]);

  const isTimedTaskSelected = useMemo(() => {
    // Shared kiosk accounts should not inherit a single persisted timer across people.
    if (allowsMultipleSubmissions) return false;
    if (!selectedTaskName) return false;
    return TIMED_TASKS.includes(selectedTaskName as any);
  }, [selectedTaskName, allowsMultipleSubmissions]);

  const shouldWarnOnPageLeaveForTask = useMemo(() => {
    if (allowsMultipleSubmissions) return false;
    if (!selectedTaskName) return false;
    return (
      isWebsiteRequirementsTaskSelected ||
      GAME_REQUIRED_TASKS.includes(selectedTaskName as any)
    );
  }, [isWebsiteRequirementsTaskSelected, selectedTaskName, allowsMultipleSubmissions]);

  const taskTimerDurationSeconds = useMemo(() => {
    switch (selectedTaskName) {
      case 'zic_zac_zoe':
        return Math.max(1, ENV.RECREATION_TASK_ONE_MINUTES) * 60;
      case 'zic_zac_zoe_follow_up':
        return Math.max(1, ENV.RECREATION_TASK_TWO_MINUTES) * 60;
      case 'platformer':
        return Math.max(1, ENV.GAME_TASK_ONE_MINUTES) * 60;
      default:
        return DEFAULT_TASK_TIMER_DURATION_SECONDS;
    }
  }, [selectedTaskName]);
  const timedTaskLimitMinutes = Math.max(1, Math.floor(taskTimerDurationSeconds / 60));
  const isTimedTaskPreStartGateActive = isTimedTaskSelected && !hasTimedTaskStarted;

  const handleStartTimedTask = useCallback(async () => {
    if (!isTimedTaskPreStartGateActive || isStartingTimedTask) return;
    setIsStartingTimedTask(true);
    await sendTaskEvent("timer_started", { source: "manual_start_click" });
    setHasTimedTaskStarted(true);
    setInitialTimerRemainingSeconds(taskTimerDurationSeconds);
    if (timedTaskFilesPendingRef.current) {
      setInitialFiles(timedTaskFilesPendingRef.current);
      timedTaskFilesPendingRef.current = null;
    }
    setIsStartingTimedTask(false);
  }, [isStartingTimedTask, isTimedTaskPreStartGateActive, sendTaskEvent, taskTimerDurationSeconds]);

  useEffect(() => {
    if (isTimedTaskPreStartGateActive && leftTab === 'preview') {
      setLeftTab('task');
    }
  }, [isTimedTaskPreStartGateActive, leftTab]);

  const showProminentTimerAlert = useCallback((
    message: string,
    tone: TimerAlertTone,
    options?: { dismissible?: boolean; autoDismissMs?: number },
  ) => {
    const dismissible = options?.dismissible ?? true;
    const autoDismissMs = options?.autoDismissMs ?? 9000;
    setTimerAlert({ message, tone, dismissible });
    if (timerAlertTimeoutRef.current !== null) {
      window.clearTimeout(timerAlertTimeoutRef.current);
      timerAlertTimeoutRef.current = null;
    }

    if (dismissible) {
      timerAlertTimeoutRef.current = window.setTimeout(() => {
        setTimerAlert(null);
        timerAlertTimeoutRef.current = null;
      }, autoDismissMs);
    }
  }, []);

  const handleSubmissionQuestionsPaneVisibilityChange = useCallback((isOpen: boolean) => {
    if (isOpen === isSubmissionQuestionsPaneOpenRef.current) return;

    // Submission questions should pause the active task timer globally.
    isSubmissionQuestionsPaneOpenRef.current = isOpen;
    setIsSubmissionQuestionsPaneOpen(isOpen);

    if (isOpen) {
      setTimerAlert(null);
      setShowTimerExpiredModal(false);
      void sendTaskEvent("timer_paused", { reason: "submission_questions_open" });
      return;
    }

    void sendTaskEvent("timer_resumed", { reason: "submission_questions_closed" });
  }, [sendTaskEvent]);

  useEffect(() => {
    return () => {
      if (timerAlertTimeoutRef.current !== null) {
        window.clearTimeout(timerAlertTimeoutRef.current);
      }
    };
  }, []);

  useEffect(() => {
    timerWarningKeysShownRef.current.clear();
    timerExpiredModalShownRef.current = false;
    setTimerAlert(null);
    setShowTimerExpiredModal(false);
    setInitialTimerRemainingSeconds(null);
    setHasTimedTaskStarted(!isTimedTaskSelected);
    setIsSubmissionQuestionsPaneOpen(false);
    setIsStartingTimedTask(false);
    setIsSubmitModalExitLocked(false);
    isSubmissionQuestionsPaneOpenRef.current = false;
  }, [isTimedTaskSelected, selectedTask, taskTimerDurationSeconds]);

  useEffect(() => {
    const handleSubmissionPaneVisibility = (event: Event) => {
      const customEvent = event as CustomEvent<{ open?: boolean }> | undefined;
      const isOpen = Boolean(customEvent?.detail?.open);
      handleSubmissionQuestionsPaneVisibilityChange(isOpen);
    };

    window.addEventListener(
      "submission-questions-pane-visibility",
      handleSubmissionPaneVisibility as EventListener
    );
    return () => {
      isSubmissionQuestionsPaneOpenRef.current = false;
      window.removeEventListener(
        "submission-questions-pane-visibility",
        handleSubmissionPaneVisibility as EventListener
      );
    };
  }, [handleSubmissionQuestionsPaneVisibilityChange]);

  useEffect(() => {
    if (!isTimedTaskSelected) return;

    let isCancelled = false;

    const initializeTimer = async () => {
      const context = resolveActiveTaskEventContext();
      if (!context) {
        if (!isCancelled) {
          // Keep timed tasks gated until explicit user start.
          // Missing context should not auto-start countdown.
          setHasTimedTaskStarted(false);
          setInitialTimerRemainingSeconds(taskTimerDurationSeconds);
        }
        return;
      }

      try {
        const params = new URLSearchParams({
          user_id: String(context.userId),
          project_id: String(context.projectId),
          duration_seconds: String(taskTimerDurationSeconds),
        });
        const response = await fetch(`${ENV.BACKEND_URL}/api/task-events/timer-state?${params.toString()}`);
        if (!response.ok) {
          throw new Error(`Failed to initialize timer: ${response.status}`);
        }

        const data = await response.json();
        const remainingSeconds = Number.isFinite(data?.remaining_seconds)
          ? Math.max(0, Math.min(taskTimerDurationSeconds, Number(data.remaining_seconds)))
          : taskTimerDurationSeconds;
        const hasStarted = Boolean(data?.has_started);
        const isPaused = Boolean(data?.is_paused);

        if (!isCancelled) {
          // Keep local pause source-of-truth tied to actual pane visibility once timer has started.
          if (hasStarted && isPaused && !isSubmissionQuestionsPaneOpenRef.current) {
            void sendTaskEvent("timer_resumed", { reason: "sync_resume_no_submission_pane" });
          }
          setHasTimedTaskStarted(hasStarted);
          setInitialTimerRemainingSeconds(remainingSeconds);
          if (hasStarted && timedTaskFilesPendingRef.current) {
            setInitialFiles(timedTaskFilesPendingRef.current);
            timedTaskFilesPendingRef.current = null;
          }
        }
      } catch (error) {
        console.warn("Failed to initialize persisted timer state", error);
        if (!isCancelled) {
          // Fail closed: keep pre-start gate active instead of auto-starting.
          setHasTimedTaskStarted(false);
          setInitialTimerRemainingSeconds(taskTimerDurationSeconds);
        }
      }
    };

    void initializeTimer();

    return () => {
      isCancelled = true;
    };
  }, [
    isTimedTaskSelected,
    taskTimerDurationSeconds,
    resolveActiveTaskEventContext,
    sendTaskEvent,
  ]);

  const taskTimerWarningCheckpoints = useMemo(
    () => [
      {
        key: "halfway",
        thresholdSeconds: Math.floor(taskTimerDurationSeconds * 0.5),
        message: "50% time remaining. Keep an eye on the timer.",
        tone: "warning" as TimerAlertTone,
        dismissible: true,
      },
      {
        key: "quarter",
        thresholdSeconds: Math.floor(taskTimerDurationSeconds * 0.25),
        message: "25% time remaining. Start planning your final pass.",
        tone: "warning" as TimerAlertTone,
        dismissible: true,
      },
      {
        key: "three-minutes",
        thresholdSeconds: 3 * 60,
        message: "3 minutes left. Prepare your code for submission now.",
        tone: "critical" as TimerAlertTone,
        dismissible: true,
      },
    ],
    [taskTimerDurationSeconds]
  );

  const handleTaskTimerWarning = useCallback(
    (_key: string, message: string, tone: TimerAlertTone, options: { dismissible: boolean; autoDismissMs: number }) => {
      showProminentTimerAlert(message, tone, options);
    },
    [showProminentTimerAlert]
  );

  const handleTaskTimerExpired = useCallback(() => {
    setTimerAlert(null);
    setShowTimerExpiredModal(true);
  }, []);

  const shouldWarnBeforeLeavingStudyTask = useCallback(() => {
    if (isTimedTaskPreStartGateActive) {
      return false;
    }
    return (
      shouldWarnOnPageLeaveForTask &&
      !studyEnded &&
      selectedTask !== 'playground' &&
      showCodingTerminal
    );
  }, [isTimedTaskPreStartGateActive, shouldWarnOnPageLeaveForTask, selectedTask, showCodingTerminal, studyEnded]);

  const confirmLeaveStudyIfNeeded = useCallback((): boolean => {
    if (!shouldWarnBeforeLeavingStudyTask()) {
      return true;
    }
    return window.confirm(LEAVE_STUDY_WARNING_MESSAGE);
  }, [shouldWarnBeforeLeavingStudyTask, LEAVE_STUDY_WARNING_MESSAGE]);

  useEffect(() => {
    const handleBeforeSidebarNavigation = (event: Event) => {
      if (!(event instanceof CustomEvent)) return;

      if (!confirmLeaveStudyIfNeeded()) {
        event.preventDefault();
      }
    };

    window.addEventListener('app:before-sidebar-navigation', handleBeforeSidebarNavigation);
    return () => {
      window.removeEventListener('app:before-sidebar-navigation', handleBeforeSidebarNavigation);
    };
  }, [confirmLeaveStudyIfNeeded]);

  const canShowViewSubmissionsTab = useMemo(() => {
    if (isPlaygroundMode || selectedTask === 'playground' || isWebsiteRequirementsTaskSelected) {
      return false;
    }
    if (isTimedTaskSelected && !isInternalReviewer) {
      return false;
    }
    return true;
  }, [
    isPlaygroundMode,
    selectedTask,
    isWebsiteRequirementsTaskSelected,
    isTimedTaskSelected,
    isInternalReviewer,
  ]);

  const assistantPaneTitle = 'AI Assistant Mode:';

  // Keyboard shortcuts: Cmd/Ctrl + [ and ] to switch Task/Preview; Cmd/Ctrl + Shift to next file; Cmd/Ctrl + (/) for Code/Submissions
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      try {
        const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform);
        const metaOrCtrl = isMac ? e.metaKey : e.ctrlKey;
        const isArrowLeft = e.key === 'ArrowLeft' || (e as any).code === 'ArrowLeft';
        if (!metaOrCtrl || e.altKey) return;

        const key = e.key || '';
        const code = (e as any).code || '';
        const isBracketLeft = key === '[' || code === 'BracketLeft';
        const isBracketRight = key === ']' || code === 'BracketRight';
        const isOpenParen = key === '(' || code === 'Digit9' || code === 'Numpad9';
        const isCloseParen = key === ')' || code === 'Digit0' || code === 'Numpad0';

        // Cmd/Ctrl + [ / ] to switch Task/Preview
        if (!e.shiftKey && (isBracketLeft || isBracketRight)) {
          e.preventDefault();
          e.stopPropagation();
          if (showCodingTerminal && selectedTask) {
            if (isBracketLeft) setLeftTab('task');
            // Switch to preview or project details based on tab
            else if (isBracketRight) {
              if (viewedSubmission) {
                setLeftTab('project-details');
              } else if (rightTab !== 'submissions') {
                void sendCodeLog('preview-refresh', { refreshSource: 'tab-switch' });
                setLeftTab('preview');
              }
            }
          }
          return;
        }

        // Cmd/Ctrl + (/) to switch Code/View Submissions
        const targetingOpenParen = isOpenParen || (!!e.shiftKey && (code === 'Digit9' || code === 'Numpad9'));
        const targetingCloseParen = isCloseParen || (!!e.shiftKey && (code === 'Digit0' || code === 'Numpad0'));
        if (targetingOpenParen || targetingCloseParen) {
          e.preventDefault();
          e.stopPropagation();
          if (showCodingTerminal && selectedTask) {
            if (targetingOpenParen) {
              setRightTab('code');
              // If on project-details, switch to task tab
              if (leftTab === 'project-details') {
                setLeftTab('task');
              }
            } else if (targetingCloseParen && canShowViewSubmissionsTab && isViewSubmissionsUnlocked) {
              setRightTab('submissions');
            }
          }
          return;
        }

        // Cmd/Ctrl + Shift → Next file (modifier-only chord)
        if (e.shiftKey && !e.altKey && (
          e.key === 'Shift' || e.key === 'Meta' || e.key === 'Control' ||
          (e as any).code === 'ShiftLeft' || (e as any).code === 'ShiftRight' ||
          (e as any).code === 'MetaLeft' || (e as any).code === 'MetaRight' ||
          (e as any).code === 'ControlLeft' || (e as any).code === 'ControlRight'
        )) {
          e.preventDefault();
          e.stopPropagation();
          try { window.dispatchEvent(new Event('navigate-next-file')); } catch {}
          return;
        }
      } catch {}
    };

    // Attach on both window and document in capture phase to maximize interception
    window.addEventListener('keydown', handleKeyDown, { capture: true });
    document.addEventListener('keydown', handleKeyDown, { capture: true } as any);
    return () => {
      window.removeEventListener('keydown', handleKeyDown, { capture: true } as any);
      document.removeEventListener('keydown', handleKeyDown, { capture: true } as any);
    };
  }, [showCodingTerminal, selectedTask, canShowViewSubmissionsTab, isViewSubmissionsUnlocked, isInternalReviewer]);

  useEffect(() => {
    const view = searchParams?.get('view');
    if (view === 'submissions' && isInternalReviewer) {
      setRightTab('submissions');
    } else {
      setRightTab('code');
    }
  }, [selectedTask, searchParams, isInternalReviewer]);

  // Update currentFiles when initialFiles change
  useEffect(() => {
    const cloned = cloneFileNodes(initialFiles);
    setCurrentFiles(cloned);

    const metadataMap: Record<string, { name: string; language?: string }> = {};
    flattenFileNodes(cloned).forEach(node => {
      if (node && node.type === 'file') {
        const key = node.id || node.name;
        if (key) {
          metadataMap[key] = { name: node.name || key, language: node.language };
        }
      }
    });
    fileMetadataRef.current = metadataMap;
  }, [initialFiles]);

  useEffect(() => {
    unloadLoggedRef.current = false;
  }, [selectedTask, currentTaskMeta]);

  useEffect(() => {
    const context = resolveActiveTaskEventContext();
    if (!context) return;

    const pageLoadKey = `${context.userId}:${context.projectId}:${context.taskId}`;
    if (lastLoadedTaskKeyRef.current !== pageLoadKey) {
      lastLoadedTaskKeyRef.current = pageLoadKey;
      taskLeftPageLoggedRef.current = false;
      isTaskNavigatedAwayRef.current = false;
      taskNavigationAwayStartRef.current = null;
      startedEditsLoggedRef.current = false;
      startedAIQueryLoggedRef.current = false;
      lastClickedSubmissionRef.current = null;
      void sendTaskEvent("loaded_in");
    }

    return () => {
      if (taskLeftPageLoggedRef.current) return;
      if (lastLoadedTaskKeyRef.current !== pageLoadKey) return;
      taskLeftPageLoggedRef.current = true;
      isTaskNavigatedAwayRef.current = false;
      taskNavigationAwayStartRef.current = null;
      void sendTaskEvent("left_page", { source: "task_cleanup" });
    };
  }, [resolveActiveTaskEventContext, sendTaskEvent]);

  useEffect(() => {
    if (!currentTaskMeta) return;
    const task = allTasks.find(t => t?.id === currentTaskMeta.id);
    if (!task) return;
    setCurrentTaskMeta(prev => {
      if (!prev || prev.id !== currentTaskMeta.id) return prev;
      const nextProjectId = task.projectId ?? prev.projectId;
      const nextName = prev.name ?? task.name;
      const normalizedPrevVoting = prev.votingStartDate ?? null;
      const nextVotingStartDate = task.votingStartDate ?? normalizedPrevVoting;
      const normalizedPrevVotingEnd = prev.votingEndDate ?? null;
      const nextVotingEndDate = task.votingEndDate ?? normalizedPrevVotingEnd;
      const normalizedPrevCodeStart = prev.codeStartDate ?? null;
      const nextCodeStartDate = task.codeStartDate ?? normalizedPrevCodeStart;

      const projectIdUnchanged = nextProjectId === prev.projectId;
      const nameUnchanged = nextName === prev.name;
      const votingDateUnchanged = nextVotingStartDate === normalizedPrevVoting;
      const votingEndUnchanged = nextVotingEndDate === normalizedPrevVotingEnd;
      const codeStartUnchanged = nextCodeStartDate === normalizedPrevCodeStart;

      if (projectIdUnchanged && nameUnchanged && votingDateUnchanged && votingEndUnchanged && codeStartUnchanged) {
        return prev;
      }

      return {
        ...prev,
        projectId: nextProjectId,
        name: nextName,
        votingStartDate: nextVotingStartDate,
        votingEndDate: nextVotingEndDate,
        codeStartDate: nextCodeStartDate,
      };
    });
  }, [allTasks, currentTaskMeta]);

  const formatVotingStartDate = (dateString: string | null | undefined): string => {
    return formatDateOnly(dateString);
  };

  const viewSubmissionsTooltip = isViewSubmissionsUnlocked
    ? 'View community submissions.'
    : 'Voting is not available for this task!';

  useEffect(() => {
    try {
      const nowIso = new Date().toISOString();
    } catch (error) {
      // no-op: logging should never break app flow
    }
  }, [isViewSubmissionsUnlocked, currentTaskMeta?.votingStartDate]);

  // Track AI code load timestamp for marking saves as AI_generated
  const aiCodeLoadedTimestampRef = useRef<number | null>(null);
  const AI_CODE_LOAD_WINDOW_MS = 3000; // 3 seconds window to mark saves as AI_generated (generous to humans: only immediate saves count as AI)
  
  // Reset timestamp when pendingAgentChanges is cleared/rejected
  const prevPendingAgentChangesRef = useRef<any>(null);
  useEffect(() => {
    const hasContent = !!(pendingAgentChanges?.modified && Object.keys(pendingAgentChanges.modified).length > 0);
    if (!hasContent) {
      // AI code was cleared/rejected, reset timestamp
      aiCodeLoadedTimestampRef.current = null;
    }
    prevPendingAgentChangesRef.current = pendingAgentChanges;
  }, [pendingAgentChanges]);

  // Helper function to find the first line difference between two strings
  const findFirstDifferenceLine = useCallback((original: string, modified: string): number | null => {
    if (!original || !modified) return null;
    
    const originalLines = original.split('\n');
    const modifiedLines = modified.split('\n');
    
    const maxLength = Math.max(originalLines.length, modifiedLines.length);
    
    for (let i = 0; i < maxLength; i++) {
      const origLine = originalLines[i] || '';
      const modLine = modifiedLines[i] || '';
      
      if (origLine !== modLine) {
        // Return 1-indexed line number
        return i + 1;
      }
    }
    
    return null;
  }, []);

  // Auto-switch to file with changes by AI agent when summary is generated
  useEffect(() => {
    if (!summaryGeneratedByMode.agent || !pendingAgentChanges || !actualEditorRef.current) return;
    
    const editorApi = actualEditorRef.current;
    if (!editorApi.selectFileByName || !editorApi.revealLocation || !editorApi.getActiveFileId) return;
    
    // Only consider files that were modified in the most recent interaction
    const filesModifiedInCurrentInteraction = Array.from(filesModifiedInCurrentInteractionRef.current);
    
    // Filter to only files with actual changes
    const filesWithChanges = filesModifiedInCurrentInteraction.filter(fileId => {
      const original = pendingAgentChanges.original?.[fileId] || '';
      const modified = pendingAgentChanges.modified?.[fileId] || '';
      return original !== modified;
    });
    
    if (filesWithChanges.length === 0) return;
    
    // Get current active file ID
    const activeFileId = editorApi.getActiveFileId();
    
    // Determine which file to view: if current file is not in changed files, switch to a changed file
    let targetFileId: string | null = null;
    let targetFile: typeof currentFiles[0] | null = null;
    let didSwitch = false;
    
    if (!activeFileId || !filesWithChanges.includes(activeFileId)) {
      // Current file is not in the changed files from the most recent interaction, switch to a changed file (use most recent)
      targetFileId = filesWithChanges[filesWithChanges.length - 1];
      targetFile = currentFiles.find(f => f.id === targetFileId);
      if (targetFile) {
        editorApi.selectFileByName(targetFile.name);
        didSwitch = true;
      }
    } else {
      // Current file is in the changed files from the most recent interaction, stay on it
      targetFileId = activeFileId;
      targetFile = currentFiles.find(f => f.id === targetFileId);
    }
    
    if (!targetFile || !targetFileId) return;
    
    // Scroll to first difference in the target file
    const original = pendingAgentChanges.original?.[targetFileId] || '';
    const modified = pendingAgentChanges.modified[targetFileId] || '';
    const firstDiffLine = findFirstDifferenceLine(original, modified);
    
    if (firstDiffLine !== null) {
      setTimeout(() => {
        editorApi.revealLocation(targetFile.name, firstDiffLine, 1, { scrollOnly: true });
      }, didSwitch ? 300 : 100); // Longer delay if we switched files
    }
  }, [summaryGeneratedByMode.agent, pendingAgentChanges, currentFiles, findFirstDifferenceLine]);

  // Switch from preview to task when switching to submissions tab
  useEffect(() => {
    if (rightTab === 'submissions' && leftTab === 'preview') {
      setLeftTab('task');
    }
  }, [rightTab, leftTab]);

  // Switch from project-details to task when switching to code tab
  useEffect(() => {
    if (rightTab === 'code' && leftTab === 'project-details') {
      setLeftTab('task');
    }
  }, [rightTab, leftTab]);

  // Listen for view-submission events from SubmissionsPlaceholder
  useEffect(() => {
    const handleViewSubmission = (event: Event) => {
      const customEvent = event as CustomEvent;
      const submission = customEvent.detail;
      if (submission && submission.title) {
        const clickedSubmission: ViewedSubmission = {
          id: Number.isFinite(Number(submission.id)) ? Number(submission.id) : null,
          title: submission.title,
          description: submission.description || null,
          projectId: Number.isFinite(Number(submission.projectId)) ? Number(submission.projectId) : null,
          userId: Number.isFinite(Number(submission.userId)) ? Number(submission.userId) : null,
        };
        setViewedSubmission(clickedSubmission);
        lastClickedSubmissionRef.current = clickedSubmission;
        // When a specific project is viewed while on the submissions tab,
        // automatically switch to the Project Details tab
        setLeftTab(prev => (rightTab === 'submissions' ? 'project-details' : prev));
      }
    };

    window.addEventListener('view-submission', handleViewSubmission);
    return () => {
      window.removeEventListener('view-submission', handleViewSubmission);
    };
  }, [rightTab]);

  // Listen for exit-submission-view events to clear project details state
  useEffect(() => {
    const handleExitView = () => {
      setViewedSubmission(null);
      if (leftTab === 'project-details') {
        setLeftTab('task');
      }
    };

    window.addEventListener('exit-submission-view', handleExitView);
    return () => {
      window.removeEventListener('exit-submission-view', handleExitView);
    };
  }, [leftTab]);

  // Load tasks from API (backed by data/dummy_tasks.json)
  // Extract to a reusable function so we can refresh tasks when returning to tasks view
  // Caches tasks in localStorage and only fetches from API if cache is empty
  const loadTasks = useCallback(async (signal?: AbortSignal, forceRefresh: boolean = false) => {
    const cacheKey = numericUserId ? `cached_tasks_${numericUserId}` : 'cached_tasks_anonymous';
    
    // Try to load from cache first (unless forcing refresh)
    if (!forceRefresh && typeof window !== 'undefined') {
      try {
        const cachedData = localStorage.getItem(cacheKey);
        if (cachedData) {
          const parsed = JSON.parse(cachedData);
          const tasks = Array.isArray(parsed.tasks) ? parsed.tasks : [];
          
          setAllTasks(buildTaskListForCurrentMode(tasks));
          setIsLoadingTasks(false);
          return; // Use cached data, skip API call
        }
      } catch (error) {
        // If cache is corrupted, fall through to fetch from API
      }
    }
    
    // Fetch from API if cache is empty or force refresh is requested
    try {
      // Include userId in the API call so backend can determine task statuses
      const queryParams = numericUserId ? `?userId=${numericUserId}` : '';
      const fetchOptions: RequestInit = { 
        cache: 'no-store',
        headers: {
          'Cache-Control': 'no-cache, no-store, must-revalidate',
          'Pragma': 'no-cache',
        }
      };
      if (signal) {
        fetchOptions.signal = signal;
      }
      const res = await fetch(`/api/tasks${queryParams}`, fetchOptions);
      if (res.ok) {
        const data = await res.json();
        const tasks = Array.isArray(data.tasks) ? data.tasks : [];
        
        // Save to cache
        if (typeof window !== 'undefined') {
          try {
            localStorage.setItem(cacheKey, JSON.stringify({ tasks }));
          } catch (error) {
            // If localStorage is full or unavailable, silently fail
          }
        }
        
        setAllTasks(buildTaskListForCurrentMode(tasks));
        // Filtering will be handled by the useEffect that depends on allTasks
      } else {
        console.error('Failed to load tasks:', res.status, res.statusText);
        setAllTasks([]);
        setFilteredTasks([]);
      }
    } catch (error: any) {
      // Ignore abort errors
      if (error.name === 'AbortError') {
        return;
      }
      console.error('Error loading tasks:', error);
      setAllTasks([]);
      setFilteredTasks([]);
    } finally {
      setIsLoadingTasks(false);
    }
  }, [buildTaskListForCurrentMode, numericUserId]);

  // Callback to refresh tasks cache after project submission
  const handleProjectSubmitted = useCallback(async () => {
    if (allowsMultipleSubmissions) {
      suppressCodeLogsRef.current = true;
    }
    // Log successful submissions across all non-playground tasks.
    void sendTaskEvent("submitted", { source: "project_submitted" });
    // Refresh tasks cache to update statuses after submission
    await loadTasks(undefined, true); // forceRefresh = true
    // Also recalculate user study popup state
    if (recalculateState) {
      await recalculateState();
    }

    // Required game tasks only allow one submission; return participants to browse.
    // Multi-submission kiosk accounts go back to browse with a cleared workspace
    // so the next person starts from a blank starter project.
    if (allowsMultipleSubmissions) {
      try {
        localStorage.removeItem('multiFileEditor_files');
        localStorage.setItem('code', '');
      } catch {}
      setHistory([]);
      setHistoryIndex(0);
      setInitialFiles([]);
      setCurrentFiles([]);
      setCode('');
      setExpandedTask(null);
      setShowCodingTerminal(false);
      setSelectedTask(null);
      setTaskId('');
      setCurrentTaskMeta(null);
      router.push('/browse');
      return;
    }
    if (
      currentTaskMeta?.name &&
      GAME_REQUIRED_TASKS.includes(currentTaskMeta.name as any)
    ) {
      setExpandedTask(null);
      setShowCodingTerminal(false);
      setSelectedTask(null);
      setTaskId("");
      setCurrentTaskMeta(null);
      router.push('/browse');
    }
  }, [loadTasks, recalculateState, sendTaskEvent, currentTaskMeta?.name, router, allowsMultipleSubmissions]);

  const handleQuestionsGenerationStarted = useCallback((metadata?: Record<string, any>) => {
    void sendTaskEvent("questions_generation_started", {
      source: "comprehension_questions",
      ...(metadata || {}),
    });
  }, [sendTaskEvent]);

  const handleQuestionsGenerationCompleted = useCallback((metadata?: Record<string, any>) => {
    void sendTaskEvent("questions_generation_completed", {
      source: "comprehension_questions",
      ...(metadata || {}),
    });
  }, [sendTaskEvent]);

  const handleContinuedToQuestions = useCallback((metadata?: Record<string, any>) => {
    void sendTaskEvent("continued_to_questions", {
      source: "continue_submission_flow",
      ...(metadata || {}),
    });
  }, [sendTaskEvent]);

  // Initial load of tasks
  useEffect(() => {
    const abortController = new AbortController();
    loadTasks(abortController.signal);
    
    return () => {
      abortController.abort();
    };
  }, [loadTasks]);

  // Cleanup function to clear assistant messages, follow-up ideas, diff editor, and related state
  const cleanupTaskState = useCallback(() => {
    // Clear diff editor state first (editedModifiedContent and diff editor refs)
    // This should be done before clearing pendingAgentChanges to ensure proper cleanup
    try {
      actualEditorRef.current?.clearDiffEditor?.();
    } catch (error) {
      // Silently fail if editor ref is not available
    }
    // Clear assistant messages and follow-up ideas
    setAssistantMessagesByMode({ agent: [], ask: [], brainstorm: [] });
    latestSuggestionsRef.current = [];
    // Clear pending agent changes (this will also trigger cleanup in MultiFileEditor)
    setPendingAgentChanges(null);
    setAwaitingResponse(false);
    setAwaitingManualSuggestions(false);
    setAssistantInputByMode({ agent: '', ask: '', brainstorm: '' });
    setSummaryGeneratedByMode({ agent: false, ask: false, brainstorm: false });
    timedTaskFilesPendingRef.current = null;
  }, []);

  useEffect(() => {
    const suppressCodeLogs = () => {
      suppressCodeLogsRef.current = true;
    };
    window.addEventListener('kiosk-suppress-code-logs', suppressCodeLogs);
    return () => {
      window.removeEventListener('kiosk-suppress-code-logs', suppressCodeLogs);
    };
  }, []);
  // Note: We no longer refresh automatically since tasks are cached and status updates aren't critical
  // If you need to refresh, you can add `loadTasks(undefined, true)` with forceRefresh=true
  // useEffect(() => {
  //   // Only refresh if we're returning to tasks view (not on initial mount)
  //   if (!showCodingTerminal && numericUserId) {
  //     // Use a small delay to avoid race conditions and ensure we're actually on the tasks page
  //     const timeoutId = setTimeout(() => {
  //       loadTasks(undefined, true);
  //     }, 100);
  //     
  //     return () => clearTimeout(timeoutId);
  //   }
  // }, [showCodingTerminal, numericUserId, loadTasks]);

  // Cleanup assistant messages and follow-up ideas when navigating back to task selection
  useEffect(() => {
    if (!showCodingTerminal) {
      cleanupTaskState();
    }
  }, [showCodingTerminal, cleanupTaskState]);

  // Ignore live content changes for preview; only refresh on save
  const handleFileContentChange = useCallback(() => {
    const editorApi = actualEditorRef?.current;
    if (!editorApi || typeof editorApi.getAllFileContents !== 'function') {
      return;
    }

    markStartedEdits("manual");

    try {
      const contents: Record<string, string> = editorApi.getAllFileContents() || {};

      setCurrentFiles(prev => {
        const source = (prev && prev.length > 0) ? prev : cloneFileNodes(initialFiles);

        const applyContents = (nodes: any[]): any[] =>
          nodes.map(node => {
            if (!node) return node;
            if (node.type === 'file') {
              const key = node.id || node.name;
              const nextContent = key != null ? contents[key] ?? node.content ?? '' : node.content ?? '';
              return {
                ...node,
                content: nextContent,
              };
            }
            if (Array.isArray(node.children)) {
              return {
                ...node,
                children: applyContents(node.children),
              };
            }
            return { ...node };
          });

        return applyContents(source);
      });

      const metadataMap = { ...fileMetadataRef.current };
      Object.entries(contents).forEach(([fileId]) => {
        if (!metadataMap[fileId]) {
          metadataMap[fileId] = {
            name: fileId,
            language: determineLanguageKey(undefined, fileId) || undefined,
          };
        }
      });
      fileMetadataRef.current = metadataMap;
    } catch (error) {
      console.warn('Failed to synchronize file contents from editor', error);
    }
  }, [actualEditorRef, initialFiles, markStartedEdits]);

  const handlePreviewRefresh = useCallback((source: string) => {
    // Skip logging for external refreshes (they're triggered programmatically and log separately)
    if (source === 'external') {
      return;
    }
    void sendCodeLog('preview-refresh', { refreshSource: source });
  }, [sendCodeLog]);

  // Handle Cmd/Ctrl+S from the editor - refresh preview if it's visible
  const handleSaveShortcut = (_fileId?: string) => {
    void sendCodeLog('save-shortcut');

    const isPreviewVisible = showCodingTerminal && selectedTask && leftTab === 'preview';
    if (!isPreviewVisible) {
      return;
    }

    // Best-effort refresh of the active preview iframe
    try {
      previewTabRef.current?.refreshPreview();
    } catch (error) {
      console.warn('Failed to refresh preview on save shortcut:', error);
    }
  };

  useEffect(() => {
    const shouldTrackTaskLeave =
      shouldWarnOnPageLeaveForTask &&
      !studyEnded &&
      selectedTask !== 'playground' &&
      showCodingTerminal;

    if (!shouldTrackTaskLeave) {
      isTaskNavigatedAwayRef.current = false;
      taskNavigationAwayStartRef.current = null;
      return;
    }

    const handleVisibilityChange = () => {
      if (document.hidden) {
        if (!isTaskNavigatedAwayRef.current) {
          isTaskNavigatedAwayRef.current = true;
          taskNavigationAwayStartRef.current = Date.now();
          void sendTaskEvent("left_page", { source: "visibilitychange_hidden" });
        }
        return;
      }

      if (isTaskNavigatedAwayRef.current && taskNavigationAwayStartRef.current) {
        const timeAwayMs = Date.now() - taskNavigationAwayStartRef.current;
        isTaskNavigatedAwayRef.current = false;
        taskNavigationAwayStartRef.current = null;
        void sendTaskEvent("left_page", {
          source: "visibilitychange_visible",
          time_away_ms: timeAwayMs,
        });

        if (timeAwayMs >= NAVIGATION_WARNING_THRESHOLD_MS) {
          showSnackbar(
            'We have logged that you navigated away from the page. You should not leave the page for any reason. Repeatedly doing this may impact your compensation.',
            5000
          );
        }
      }
    };

    const beaconNavigationAwayEvent = (source: "beforeunload" | "pagehide") => {
      if (isTaskNavigatedAwayRef.current && taskNavigationAwayStartRef.current) {
        const timeAwayMs = Date.now() - taskNavigationAwayStartRef.current;
        isTaskNavigatedAwayRef.current = false;
        taskNavigationAwayStartRef.current = null;
        taskLeftPageLoggedRef.current = true;
        sendTaskEventBeacon("left_page", { source, time_away_ms: timeAwayMs });
        return;
      }

      if (!taskLeftPageLoggedRef.current) {
        taskLeftPageLoggedRef.current = true;
        sendTaskEventBeacon("left_page", { source });
      }
    };

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      sendCodeLogBeacon('before-unload');
      beaconNavigationAwayEvent("beforeunload");

      // Trigger browser-native confirmation dialog before leaving.
      event.preventDefault();
      event.returnValue = "";
      return "";
    };

    const handlePageHide = (event: any) => {
      if (event?.persisted) {
        return;
      }
      sendCodeLogBeacon('before-unload');
      beaconNavigationAwayEvent("pagehide");
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('beforeunload', handleBeforeUnload);
    window.addEventListener('pagehide', handlePageHide);

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('beforeunload', handleBeforeUnload);
      window.removeEventListener('pagehide', handlePageHide);
    };
  }, [
    NAVIGATION_WARNING_THRESHOLD_MS,
    shouldWarnOnPageLeaveForTask,
    selectedTask,
    sendCodeLogBeacon,
    sendTaskEvent,
    sendTaskEventBeacon,
    showCodingTerminal,
    showSnackbar,
    studyEnded,
  ]);

  const defaultFileName = (type: string): string => {
    switch (type) {
      case 'html':
        return 'index.html';
      case 'css':
        return 'styles.css';
      case 'js':
        return 'script.js';
      default:
        return `${type}.txt`;
    }
  };

  function cloneFileNodes(nodes: any[] | undefined): any[] {
    if (!Array.isArray(nodes)) {
      return [];
    }
    return nodes.map(node => ({
      ...node,
      children: Array.isArray(node?.children) ? cloneFileNodes(node.children) : node?.children,
    }));
  }

  function flattenFileNodes(nodes: any[] | undefined): any[] {
    if (!Array.isArray(nodes)) {
      return [];
    }
    const result: any[] = [];
    const stack = [...nodes];
    while (stack.length) {
      const current = stack.shift();
      if (!current) continue;
      result.push(current);
      if (Array.isArray(current.children) && current.children.length > 0) {
        stack.unshift(...current.children);
      }
    }
    return result;
  }

  function determineLanguageKey(language?: string, name?: string): string | null {
    const lang = (language || '').toLowerCase();
    if (lang.includes('html')) return 'html';
    if (lang.includes('css')) return 'css';
    if (lang.includes('javascript') || lang === 'js') return 'js';
    if (lang.includes('typescript') || lang === 'ts') return 'js';

    const lowerName = (name || '').toLowerCase();
    if (lowerName.endsWith('.html')) return 'html';
    if (lowerName.endsWith('.css')) return 'css';
    if (
      lowerName.endsWith('.js') ||
      lowerName.endsWith('.mjs') ||
      lowerName.endsWith('.cjs') ||
      lowerName.endsWith('.ts') ||
      lowerName.endsWith('.tsx') ||
      lowerName.endsWith('.jsx')
    ) {
      return 'js';
    }

    return null;
  }

  // Handler for AI Assistant submit
  const handleAssistantSubmit = async (message: string) => {
    const trimmedMessage = message.trim();
    if (!trimmedMessage) {
      return;
    }
    const submitMode = assistantModeRef.current;
    const endpointByMode: Record<AssistantMode, string> = {
      agent: '/api/agent/stream',
      ask: isWebsiteRequirementsTaskSelected ? '/api/debug/stream' : '/api/ask/stream',
      brainstorm: '/api/brainstorm/stream',
    };
    const endpointPath = endpointByMode[submitMode];
    const isChatStyleMode = submitMode !== 'agent';

    markStartedAIQuery(submitMode);

    const createMessageId = () => `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const appendMessage = (item: AssistantItem) => {
      setAssistantMessagesForMode(
        submitMode,
        (prev) => [...prev, { ...item, id: item.id ?? createMessageId() }]
      );
    };
    const updateMessage = (id: string, updates: Partial<AssistantItem>) => {
      setAssistantMessagesForMode(
        submitMode,
        (prev) => prev.map(msg => (msg.id === id ? { ...msg, ...updates } : msg))
      );
    };

    // Clear all suggestions when user submits a new query
    setAssistantMessagesForMode(submitMode, (prev) => prev.filter(msg => msg.type !== 'suggestions'));
    
    setSummaryGeneratedForMode(submitMode, false);
    latestSuggestionsRef.current = [];
    assistantPromptCountRef.current = (assistantPromptCountRef.current || 0) + 1;
    
    appendMessage({ type: 'user', message: trimmedMessage });
    setAwaitingResponse(true);
    // Clear input field immediately after submission (input stays disabled via awaitingResponse)
    setAssistantInputValue('');

    // Get current files from editor
    const files = { html: '', css: '', js: '' };
    const fileIdsByType: Record<string, string> = {};
    const fileNamesByType: Record<string, string> = {};
    let allContents: Record<string, string> = {};

    if (actualEditorRef?.current?.getAllFileContents) {
      allContents = actualEditorRef.current.getAllFileContents();

      // First pass: prioritize exact filename matches (index.html, styles.css, frontend.js)
      const exactMatches: Record<string, { fileId: string; fileName: string; content: string }> = {};
      Object.entries(allContents).forEach(([fileId, content]) => {
        const file = currentFiles.find(f => f.id === fileId || f.name === fileId);
        const fileNameRaw = file?.name || fileId || '';
        const fileName = fileNameRaw.toLowerCase();
        const contentStr = String(content || '');

        // Check for exact filename matches first
        if (fileName === 'index.html') {
          exactMatches['html'] = { fileId, fileName: fileNameRaw, content: contentStr };
        } else if (fileName === 'styles.css') {
          exactMatches['css'] = { fileId, fileName: fileNameRaw, content: contentStr };
        } else if (fileName === 'frontend.js') {
          exactMatches['js'] = { fileId, fileName: fileNameRaw, content: contentStr };
        }
      });

      // Assign exact matches first
      Object.entries(exactMatches).forEach(([type, match]) => {
        files[type as 'html' | 'css' | 'js'] = match.content;
        fileIdsByType[type] = match.fileId;
        fileNamesByType[type] = match.fileName;
      });

      // Second pass: fill in any missing types with generic matches
      Object.entries(allContents).forEach(([fileId, content]) => {
        const file = currentFiles.find(f => f.id === fileId || f.name === fileId);
        const fileNameRaw = file?.name || fileId || '';
        const fileName = fileNameRaw.toLowerCase();
        const contentStr = String(content || '');

        const assignIfEmpty = (type: 'html' | 'css' | 'js') => {
          if (!files[type]) {
            files[type] = contentStr;
            fileIdsByType[type] = fileId;
            fileNamesByType[type] = file?.name || fileId || defaultFileName(type);
          }
        };

        if (fileName.endsWith('.html') || file?.language === 'html') {
          assignIfEmpty('html');
        } else if (fileName.endsWith('.css') || file?.language === 'css') {
          assignIfEmpty('css');
        } else if (
          ((fileName.endsWith('.js') || fileName.endsWith('.javascript')) && !fileName.endsWith('.json')) ||
          file?.language === 'javascript'
        ) {
          assignIfEmpty('js');
        }
      });
    }

    const fallbackNames: Record<string, string> = {
      html: fileNamesByType['html'] || 'index.html',
      css: fileNamesByType['css'] || 'styles.css',
      js: fileNamesByType['js'] || 'script.js',
    };

    // Reset tracking for files modified in this interaction
    filesModifiedInCurrentInteractionRef.current = new Set();
    
    const toolMessageIds = new Map<string, string>();
    const completedToolMessages = new Set<string>(); // Track which tool messages have been completed
    let finalPayload: any = null;
    let filesWereEdited = false; // Track if any files were successfully edited
    // Track modified files during the stream for snapshot saving
    const modifiedFilesDuringStream: Record<string, string> = {};
    let snapshotSaved = false; // Flag to ensure we only save snapshot once per agent completion
    let lastAssistantLogId: number | null = null; // Link codes row -> assistant_logs (stored in codes.metadata)

    let wasAborted = false;
    let askStreamMessageId: string | null = null;
    let askStreamAccum = '';
    let askClearQuery = false;
    try {

      const numericUserId = user?.id && !Number.isNaN(Number(user.id)) ? Number(user.id) : null;
      const controller = new AbortController();
      assistantAbortControllerRef.current = controller;
      const response = await fetch(`${ENV.BACKEND_URL}${endpointPath}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          assistantMode: submitMode,
          prompt: trimmedMessage,
          files,
          taskId: taskId || currentTaskMeta?.id || null,
          taskName: currentTaskMeta?.name || null,
          userId: numericUserId,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error('Failed to start agent stream');
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error('Streaming response not supported');
      }

      const decoder = new TextDecoder();
      let buffer = '';

      const maybeHandleChunk = (rawLine: string) => {
        const line = rawLine.trim();
        if (!line) return;
        let payload: any;
        try {
          payload = JSON.parse(line);
        } catch (err) {
          console.warn('Failed to parse agent stream chunk:', line, err);
          return;
        }

        const state = payload?.state;
        const data = payload?.data ?? {};

        // Non-agent modes send { delta } and { done, clearQuery }
        if (isChatStyleMode) {
          if (payload.delta !== undefined) {
            if (!askStreamMessageId) {
              askStreamMessageId = createMessageId();
              appendMessage({ id: askStreamMessageId, type: 'assistant', message: '', renderMarkdown: true });
            }
            askStreamAccum += payload.delta;
            updateMessage(askStreamMessageId, { message: askStreamAccum });
          }
          if (payload.done || payload.clearQuery) {
            askClearQuery = true;
            setAssistantInputValue('');
          }
          if (payload.error) {
            appendMessage({ type: 'assistant', message: `Error: ${payload.error} ${ERROR_TRY_AGAIN}` });
          }
          return;
        }

        switch (state) {
          case 'restate': {
            if (data.restate) {
              appendMessage({ type: 'assistant', message: data.restate });
            }
            break;
          }
          case 'plan': {
            // Currently unused in UI
            break;
          }
          case 'signpost': {
            const signpost = data.signpost;
            const targetFiles: string[] = data.target_files ?? [];
            if (signpost) {
              appendMessage({ type: 'assistant', message: signpost });
            }
            // One block per edit (backend sends index so key is unique: "1-js", "2-js", etc.)
            targetFiles.forEach((fileType: string) => {
              const key = `${data.index ?? ''}-${fileType}`;
              const displayName = fallbackNames[fileType] || defaultFileName(fileType);
              const id = createMessageId();
              toolMessageIds.set(key, id);
              appendMessage({
                id,
                type: 'tool',
                message: `Editing ${displayName}`,
                fileName: displayName,
                status: 'pending',
              });
            });
            break;
          }
          case 'tool_result': {
            const targetFiles: string[] = data.target_files ?? [];
            const diffStats: Record<string, { additions?: number; deletions?: number }> = data.diff_stats ?? {};
            const filename: string | undefined = data.filename;
            const updatedContent: string | undefined = data.updated_content;
            targetFiles.forEach((fileType: string) => {
              const key = `${data.index ?? ''}-${fileType}`;
              const messageId = toolMessageIds.get(key);
              if (messageId) {
                completedToolMessages.add(messageId);
                filesWereEdited = true;
                markStartedEdits("ai_agent");
                updateMessage(messageId, {
                  status: 'done',
                  diff: {
                    additions: diffStats?.[fileType]?.additions ?? 0,
                    deletions: diffStats?.[fileType]?.deletions ?? 0,
                  },
                });
              }
            });
            
            // Trigger preview refresh when file editing is completed (tool_result received)
            if (targetFiles.length > 0 && updatedContent) {
              setTimeout(() => {
                try {
                  previewTabRef.current?.refreshPreview();
                  // Code log is sent once at stream end, not per tool_result
                } catch (error) {
                  console.warn('Failed to refresh preview when file editing completed:', error);
                }
              }, 100); // Small delay to ensure editor content is updated
            }

            // Track modified files during stream for snapshot saving
            if (filename && typeof updatedContent === 'string') {
              // Map filename -> fileType used earlier (html/css/js)
              // Prioritize exact filename matches to avoid incorrect mappings
              const lower = filename.toLowerCase();
              let ftype: 'html' | 'css' | 'js' | null = null;
              
              // Exact matches first (most specific)
              if (lower === 'index.html') {
                ftype = 'html';
              } else if (lower === 'styles.css') {
                ftype = 'css';
              } else if (lower === 'frontend.js') {
                ftype = 'js';
              }
              // Then check for specific patterns
              else if (lower.endsWith('index.html')) {
                ftype = 'html';
              } else if (lower.endsWith('styles.css')) {
                ftype = 'css';
              } else if (lower.endsWith('frontend.js')) {
                ftype = 'js';
              }
              // Finally, generic extension matches (least specific)
              else if (lower.endsWith('.html')) {
                ftype = 'html';
              } else if (lower.endsWith('.css')) {
                ftype = 'css';
              } else if (lower.endsWith('.js') && !lower.endsWith('.json')) {
                ftype = 'js';
              }

              if (ftype) {
                const fileId = fileIdsByType[ftype];
                if (fileId) {
                  // Track this modified file for snapshot saving
                  modifiedFilesDuringStream[fileId] = updatedContent;
                  // Track that this file was modified in the current interaction
                  filesModifiedInCurrentInteractionRef.current.add(fileId);
                  
                  const originalContent = (allContents && typeof allContents[fileId] === 'string')
                    ? allContents[fileId]
                    : (currentFiles.find(f => f.id === fileId)?.content ?? '');

                  setPendingAgentChanges((prev: any) => {
                    const next = {
                      original: { ...(prev?.original || {}) },
                      modified: { ...(prev?.modified || {}) },
                      summary: prev?.summary,
                      steps: prev?.steps,
                    } as any;
                    const baseOriginal = (prev && prev.original && prev.original[fileId] != null)
                      ? String(prev.original[fileId] ?? '')
                      : String(originalContent ?? '');
                    next.original[fileId] = baseOriginal;
                    next.modified[fileId] = String(updatedContent);
                    return next;
                  });

                  // Notify editor to refresh diff modified content immediately
                  try {
                    window.dispatchEvent(new CustomEvent('editor-update-diff-modified', { detail: { fileId, content: String(updatedContent) } }));
                  } catch {}

                  // Do not switch focus automatically; keep user's current tab/editor active
                }
              }
            }
            break;
          }
          case 'summary': {
            if (data.summary) {
              appendMessage({ type: 'assistant', message: data.summary });
              setSummaryGeneratedForMode(submitMode, true);
              
              // Build the updated messages array with summary included
              // This ensures the snapshot includes the summary even though state updates are async
              const currentMessages = [...(assistantMessagesByModeRef.current[submitMode] ?? [])];
              
              // Create the summary message with an ID
              const summaryMessageId = createMessageId();
              const summaryMessage: AssistantItem = {
                id: summaryMessageId,
                type: 'assistant',
                message: data.summary,
              };
              
              // Get all tool message IDs from the map values
              const allToolMessageIds = new Set(Array.from(toolMessageIds.values()));
              
              // Mark all pending tool messages as done when summary is generated
              const updatedMessages = currentMessages.map((msg) => {
                if (msg.id && allToolMessageIds.has(msg.id) && !completedToolMessages.has(msg.id)) {
                  completedToolMessages.add(msg.id);
                  return { ...msg, status: 'done' as const };
                }
                return msg;
              });
              
              // Add the summary message to the array
              const messagesWithSummary = [...updatedMessages, summaryMessage];
              
              // Also update state for UI (these calls happen asynchronously)
              toolMessageIds.forEach((msgId) => {
                if (msgId && !completedToolMessages.has(msgId)) {
                  // Note: msgId is already the value from the Map (Map.forEach gives value, key)
                  updateMessage(msgId, { status: 'done' });
                }
              });
              
              // Save snapshot when summary is received, using tracked modified files
              // Only save once per agent completion
              if (!snapshotSaved) {
                // Build code state from modified files tracked during the stream
                if (Object.keys(modifiedFilesDuringStream).length > 0) {
                  // Start with current editor state (for unmodified files)
                  const codeStateFromStream: Record<string, string> = {};
                  if (actualEditorRef?.current?.getAllFileContents) {
                    const currentState = actualEditorRef.current.getAllFileContents();
                    Object.entries(currentState).forEach(([fileId, content]) => {
                      codeStateFromStream[fileId] = String(content);
                    });
                  }
                  
                  // Override with modified files from the stream
                  Object.entries(modifiedFilesDuringStream).forEach(([fileId, content]) => {
                    codeStateFromStream[fileId] = content;
                  });
                  
                  // Save snapshot with the final file state AND messages including summary
                  saveSnapshot(messagesWithSummary, codeStateFromStream);
                  snapshotSaved = true;
                } else if (finalPayload && finalPayload.final_files && Object.keys(finalPayload.final_files).length > 0) {
                  // Fallback: use finalPayload if available (in case summary comes after complete)
                  const codeStateFromFinalFiles: Record<string, string> = {};
                  ['html', 'css', 'js'].forEach(type => {
                    const fileId = fileIdsByType[type];
                    const content = finalPayload.final_files[type];
                    if (fileId && typeof content === 'string') {
                      codeStateFromFinalFiles[fileId] = content;
                    }
                  });
                  
                  // Also include any files that weren't modified
                  if (actualEditorRef?.current?.getAllFileContents) {
                    const currentState = actualEditorRef.current.getAllFileContents();
                    Object.entries(currentState).forEach(([fileId, content]) => {
                      if (!codeStateFromFinalFiles[fileId]) {
                        codeStateFromFinalFiles[fileId] = String(content);
                      }
                    });
                  }
                  
                  // Save snapshot with the final file state AND messages including summary
                  saveSnapshot(messagesWithSummary, codeStateFromFinalFiles);
                  snapshotSaved = true;
                }
              }
            }
            break;
          }
          case 'suggestions': {
            const suggestions: string[] = Array.isArray(data.suggestions) ? data.suggestions : [];
            if (suggestions.length) {
              latestSuggestionsRef.current = suggestions;
              appendMessage({ type: 'suggestions', suggestions });
            }
            break;
          }
          case 'error': {
            const messageText = data.message || 'Unknown error';
            appendMessage({ type: 'assistant', message: `Error: ${messageText} ${ERROR_TRY_AGAIN}` });
            break;
          }
          case 'complete': {
            finalPayload = data;
            break;
          }
          case 'session_uuid': {
            if (data.assistantLogId != null) {
              lastAssistantLogId = Number(data.assistantLogId);
            }
            break;
          }
          default: {
            break;
          }
        }
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) {
          break;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        lines.forEach(maybeHandleChunk);
      }

      if (buffer) {
        maybeHandleChunk(buffer);
      }

      // Mark any remaining pending tool messages as failed when stream completes
      toolMessageIds.forEach((msgId) => {
        if (msgId && !completedToolMessages.has(msgId)) {
          updateMessage(msgId, { status: 'failed' });
        }
      });

      // Log code once at stream end (final merged state) instead of per tool_result
      if (filesWereEdited && Object.keys(modifiedFilesDuringStream).length > 0) {
        let currentState: Record<string, string> = {};
        if (actualEditorRef?.current?.getAllFileContents) {
          currentState = actualEditorRef.current.getAllFileContents() || {};
        }
        const codeByLanguage: Record<string, string> = {};
        (['html', 'css', 'js'] as const).forEach((type) => {
          const fileId = fileIdsByType[type];
          const content = fileId ? (modifiedFilesDuringStream[fileId] ?? currentState[fileId] ?? '') : '';
          codeByLanguage[type] = typeof content === 'string' ? content : String(content ?? '');
        });
        void sendCodeLog('AI-refresh', {
          refreshSource: 'stream_end',
          codeByLanguage,
          ...(lastAssistantLogId != null ? { assistant_log_id: lastAssistantLogId } : {}),
        });
      }

    } catch (error: any) {
      console.error('Error during agent stream:', error);
      // Mark all pending tool messages as failed on error
      toolMessageIds.forEach((msgId) => {
        if (msgId && !completedToolMessages.has(msgId)) {
          updateMessage(msgId, { status: 'failed' });
        }
      });
      if (error?.name === 'AbortError') {
        wasAborted = true;
        appendMessage({ type: 'system', message: 'Stopped coding' });
        // Restore original query and focus input
        try {
          setAssistantInputValue(trimmedMessage);
          setTimeout(() => {
            assistantTerminalPaneRef.current?.focusInput?.();
          }, 0);
        } catch {}
      } else {
        appendMessage({ type: 'assistant', message: `Error: ${(error as Error).message} ${ERROR_TRY_AGAIN}` });
      }
    } finally {
      setAwaitingResponse(false);
      assistantAbortControllerRef.current = null;
      // Only clear input if files were successfully edited, or ask/brainstorm completed (clearQuery), otherwise restore it
      if (!wasAborted) {
        if (filesWereEdited || isChatStyleMode || askClearQuery) {
          try { setAssistantInputValue(''); } catch {}
        } else {
          // No files were edited, restore the input so user can try again
          try {
            setAssistantInputValue(trimmedMessage);
            setTimeout(() => {
              assistantTerminalPaneRef.current?.focusInput?.();
            }, 0);
          } catch {}
        }
      }
    }

    if (finalPayload && finalPayload.final_files && Object.keys(finalPayload.final_files).length > 0) {
      filesWereEdited = true; // Mark that files were edited via final payload
      markStartedEdits("ai_agent");
      const originalFiles: Record<string, string> = {};
      const modifiedFilesByFileId: Record<string, string> = {};

      ['html', 'css', 'js'].forEach(type => {
        const fileId = fileIdsByType[type];
        const modifiedContent = finalPayload.final_files[type];
        if (fileId && typeof modifiedContent === 'string') {
          // Track that this file was modified in the current interaction
          filesModifiedInCurrentInteractionRef.current.add(fileId);
          
          const originalContent =
            (allContents && typeof allContents[fileId] === 'string')
              ? allContents[fileId]
              : (currentFiles.find(f => f.id === fileId)?.content ?? '');
          const priorOriginal = pendingAgentChanges?.original?.[fileId];
          originalFiles[fileId] = priorOriginal != null
            ? String(priorOriginal)
            : String(originalContent ?? '');
          modifiedFilesByFileId[fileId] = modifiedContent;

          // Notify editor to refresh diff modified content immediately for final payload
          try {
            window.dispatchEvent(new CustomEvent('editor-update-diff-modified', { detail: { fileId, content: String(modifiedContent) } }));
          } catch {}
        }
      });

      if (Object.keys(modifiedFilesByFileId).length > 0) {
        // Record timestamp when AI code is loaded for marking saves as AI_generated
        aiCodeLoadedTimestampRef.current = Date.now();
        
        setPendingAgentChanges({
          original: originalFiles,
          modified: modifiedFilesByFileId,
          summary: finalPayload.summary,
          steps: finalPayload.steps,
        });
        
        // Apply changes to the regular editor immediately (not just diff editor)
        // This makes the changes available for undo/redo
        Object.entries(modifiedFilesByFileId).forEach(([fileId, content]) => {
          if (actualEditorRef?.current?.updateFileContent) {
            actualEditorRef.current.updateFileContent(fileId, String(content));
          }
        });
        
        // Save snapshot after files are applied (if not already saved in summary case)
        // Use finalPayload which has the complete final state
        if (!snapshotSaved) {
          const codeStateFromFinalFiles: Record<string, string> = {};
          ['html', 'css', 'js'].forEach(type => {
            const fileId = fileIdsByType[type];
            const content = finalPayload.final_files[type];
            if (fileId && typeof content === 'string') {
              codeStateFromFinalFiles[fileId] = content;
            }
          });
          
          // Also include any files that weren't modified
          if (actualEditorRef?.current?.getAllFileContents) {
            const currentState = actualEditorRef.current.getAllFileContents();
            Object.entries(currentState).forEach(([fileId, content]) => {
              if (!codeStateFromFinalFiles[fileId]) {
                codeStateFromFinalFiles[fileId] = String(content);
              }
            });
          }
          
          saveSnapshot(undefined, codeStateFromFinalFiles);
          snapshotSaved = true;
        }
      }
    }
  };

  const handleClearAssistantMessages = useCallback(async () => {
    // Save snapshot before clearing (with current code + current messages)
    // This allows undo to restore both code and messages
    saveSnapshot();

    // Clear messages visually first (keep only suggestions) - immediate feedback
    const clearedMessages = assistantMessages.filter((msg: any) => msg.type === 'suggestions');
    setAssistantMessagesForMode(agentMode, clearedMessages);

    // Save snapshot after clearing (with current code + empty messages)
    // This represents the cleared state, so redo can restore it
    saveSnapshot(clearedMessages);

    // Do time-consuming async operations after visual update
    try {
      await fetch(`${ENV.BACKEND_URL}/api/agent-history/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          userId: numericUserId,
        }),
      });
    } catch (e) {
      // no-op: clearing history is best-effort
    }
  }, [agentMode, assistantMessages, numericUserId, saveSnapshot, setAssistantMessagesForMode]);

  const handleAssistantHalt = useCallback(() => {
    try {
      assistantAbortControllerRef.current?.abort();
    } catch {}
  }, []);

  const handleSuggestionSelection = useCallback(async (suggestion: string) => {
    // Skip in playground mode - no database saving or logging
    if (isPlaygroundMode || selectedTask === 'playground') {
      return;
    }
    
    const cleaned = (suggestion || '').trim();
    if (!cleaned) return;

    const suggestions = latestSuggestionsRef.current;
    if (!Array.isArray(suggestions) || suggestions.length === 0) {
      return;
    }

    try {
      const numericUserId = user?.id && !Number.isNaN(Number(user.id)) ? Number(user.id) : null;
      await fetch(`${ENV.BACKEND_URL}/api/code-preferences`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          suggestions,
          user_selection: cleaned,
          taskId: taskId || currentTaskMeta?.id || null,
          task_name: currentTaskMeta?.name || null,
          user_id: numericUserId,
        }),
      });
    } catch (error) {
      console.warn('Failed to log suggestion selection', error);
    }
  }, [taskId, currentTaskMeta, user]);

  // Resize handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  };

  const handleVerticalMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsVerticalResizing(true);
  };

  const handleEditorMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsEditorResizing(true);
  };

  const handleMouseMove = (e: MouseEvent) => {
    if (!isResizing) return;
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const containerWidth = rect.width;
    const resizeHandleWidth = 4;
    const relativeX = e.clientX - rect.left;
    let newLeftWidth = relativeX - (resizeHandleWidth / 2);
    if (isSwapped) {
      newLeftWidth = containerWidth - relativeX - (resizeHandleWidth / 2);
    }
    const minWidthPercent = 25;
    const minWidth = (containerWidth * minWidthPercent) / 100;
    const rightMinWidth = (containerWidth * 30) / 100; // RIGHT_MIN_WIDTH_PERCENT
    const maxWidth = Math.max(minWidth, containerWidth - rightMinWidth - resizeHandleWidth);
    const constrainedWidth = Math.max(minWidth, Math.min(maxWidth, newLeftWidth));

    // Save last constrained width for commit on mouseup
    lastConstrainedWidthRef.current = constrainedWidth;
    pendingLeftWidthRef.current = constrainedWidth;

    // Apply width directly via rAF to avoid React re-render on every mousemove
    if (!rafScheduledRef.current) {
      rafScheduledRef.current = true;
      requestAnimationFrame(() => {
        rafScheduledRef.current = false;
        if (leftPaneRef.current) {
          try {
            leftPaneRef.current.style.width = `${pendingLeftWidthRef.current}px`;
          } catch (e) {
            // no-op
          }
        }
      });
    }
  };

  const handleVerticalMouseMove = (e: MouseEvent) => {
    if (!isVerticalResizing) return;
    
    const containerHeight = window.innerHeight - 32;
    const resizeHandleHeight = 16;
    const padding = 16;
    
    const relativeY = e.clientY - padding;
    const newTaskHeight = relativeY;
    
    const minHeightPercent = 25;
    const maxHeightPercent = 75;
    const minHeight = (containerHeight * minHeightPercent) / 100;
    const maxHeight = (containerHeight * maxHeightPercent) / 100;
    const constrainedHeight = Math.max(minHeight, Math.min(maxHeight, newTaskHeight));
    setTaskInstructionHeight(constrainedHeight);
  };

  const handleEditorMouseMove = (e: MouseEvent) => {
    if (!isEditorResizing) return;
    
    const containerHeight = window.innerHeight - 32;
    const resizeHandleHeight = 16;
    const padding = 16;
    
    const relativeY = e.clientY - padding;
    const newEditorHeight = relativeY;
    
    const minHeightPercent = 20;
    const maxHeightPercent = 70;
    const minHeight = (containerHeight * minHeightPercent) / 100;
    const maxHeight = (containerHeight * maxHeightPercent) / 100;
    const constrainedHeight = Math.max(minHeight, Math.min(maxHeight, newEditorHeight));
    setEditorHeight(constrainedHeight);
  };

  const handleMouseUp = () => {
    setIsResizing(false);
    setIsVerticalResizing(false);
    setIsEditorResizing(false);
    // Commit final width to state (single React update)
    if (lastConstrainedWidthRef.current > 0) {
      setLeftColumnWidth(lastConstrainedWidthRef.current);
    }
    try {
      (actualEditorRef.current as any)?.layout?.();
    } catch (e) {
      // no-op
    }
  };

  // Add global mouse event listeners
  useEffect(() => {
    if (isResizing || isVerticalResizing || isEditorResizing) {
      let mouseMoveHandler: (e: MouseEvent) => void;
      if (isResizing) mouseMoveHandler = handleMouseMove;
      else if (isVerticalResizing) mouseMoveHandler = handleVerticalMouseMove;
      else if (isEditorResizing) mouseMoveHandler = handleEditorMouseMove;
      else return;
      
      document.addEventListener('mousemove', mouseMoveHandler);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = isResizing ? 'col-resize' : 'row-resize';
      document.body.style.userSelect = 'none';
    } else {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mousemove', handleVerticalMouseMove);
      document.removeEventListener('mousemove', handleEditorMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mousemove', handleVerticalMouseMove);
      document.removeEventListener('mousemove', handleEditorMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing, isVerticalResizing, isEditorResizing]);

  // Helper function to filter tasks by required status
  const filterTasksByRequiredStatus = useCallback((tasks: any[]): any[] => {
    // Always include playground task regardless of filtering
    const playgroundTask = tasks.find((task: any) => task.id === 'playground');
    const otherTasks = tasks.filter((task: any) => task.id !== 'playground');

    if (studyEnded) {
      return playgroundTask ? [playgroundTask, ...otherTasks] : otherTasks;
    }
    
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);

    if (mode === 'website-requirements') {
      const requiredTaskNames = getRequiredTasksForMode(mode, otherTasks);
      const requiredTaskNamesSet = new Set(requiredTaskNames);
      const filteredOtherTasks = otherTasks.filter((task: any) => requiredTaskNamesSet.has(task.name));
      const orderedRequiredTasks = [...filteredOtherTasks].sort(
        (a: any, b: any) => requiredTaskNames.indexOf(a.name) - requiredTaskNames.indexOf(b.name)
      );
      return playgroundTask ? [playgroundTask, ...orderedRequiredTasks] : orderedRequiredTasks;
    }

    const gameRequiredNamesSet = new Set(GAME_REQUIRED_TASKS);
    const gameModeTasks = otherTasks.filter((task: any) => !isWebsiteRequirementTask(task));
    const gameRequiredTasks = gameModeTasks
      .filter((task: any) => gameRequiredNamesSet.has(task.name))
      .sort((a: any, b: any) => GAME_REQUIRED_TASKS.indexOf(a.name) - GAME_REQUIRED_TASKS.indexOf(b.name));
    const remainingTasks = gameModeTasks.filter((task: any) => !gameRequiredNamesSet.has(task.name));

    return playgroundTask ? [playgroundTask, ...gameRequiredTasks, ...remainingTasks] : [...gameRequiredTasks, ...remainingTasks];
  }, [studyEnded, websiteRequirementsSkipped]);

  const studyTaskMode = useMemo(() => {
    const otherTasks = allTasks.filter((task: any) => task.id !== 'playground');
    return getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
  }, [allTasks, websiteRequirementsSkipped]);

  const handleCopyFromAssistant = useCallback((payload: AssistantCopyPayload) => {
    const assistantModeForLog =
      agentMode === 'ask' && isWebsiteRequirementsTaskSelected
        ? 'debug'
        : agentMode === 'ask'
          ? 'chat'
          : agentMode;
    sendCodeLog('copy_from_assistant', {
      copy_source: payload.source,
      copy_language: payload.language ?? undefined,
      copied_text_preview: payload.text != null ? String(payload.text).slice(0, 500) : undefined,
      assistantMode: assistantModeForLog,
    });
  }, [sendCodeLog, agentMode, isWebsiteRequirementsTaskSelected]);

  const assistantPaneNode = useMemo(() => (
    <AssistantTerminalPane
      ref={assistantTerminalPaneRef}
      title={assistantPaneTitle}
      modeLabel={
        isWebsiteRequirementsTaskSelected
          ? (agentMode === 'ask' ? 'Chat' : 'Agent')
          : modeLabelMap[agentMode]
      }
      modeValue={agentMode}
      onModeChange={
        (selectedTask === 'playground' || !WEBSITE_REQUIREMENT_TASKS.includes(normalizeTaskNameKey(currentTaskMeta?.name) as any))
          ? handleAssistantModeChange
          : undefined
      }
      modeSwitchDisabled={awaitingResponse}
      initialMessage={
        agentMode === 'ask' && isWebsiteRequirementsTaskSelected
          ? "Hello, I'm in Chat Mode! I can help with syntax questions, but I can't read or edit your code directly."
          : modeInitialMessageMap[agentMode]
      }
      items={assistantMessages}
      onClearMessages={handleClearAssistantMessages}
      inputValue={assistantInputValue}
      onInputChange={setAssistantInputValue}
      onSubmit={handleAssistantSubmit}
      onSuggestionClick={handleSuggestionSelection}
      awaitingResponse={awaitingResponse}
      summaryGenerated={summaryGenerated}
      isEditorLoading={isSpinning}
      onHalt={handleAssistantHalt}
      assistantPlacement={assistantPlacement}
      onAssistantPlacementChange={setAssistantPlacement}
      onUndo={handleUndo}
      onRedo={handleRedo}
      canUndo={canUndo}
      canRedo={canRedo}
      hideSuggestions={studyTaskMode === 'website-requirements' && selectedTask !== 'playground'}
      disablePaste={isWebsiteRequirementsTaskSelected}
      onCopyFromAssistant={handleCopyFromAssistant}
    />
  ), [
    agentMode,
    assistantMessages,
    assistantPaneTitle,
    assistantPlacement,
    awaitingResponse,
    canRedo,
    canUndo,
    currentTaskMeta?.name,
    handleAssistantHalt,
    handleAssistantModeChange,
    handleAssistantSubmit,
    handleClearAssistantMessages,
    handleCopyFromAssistant,
    handleRedo,
    handleSuggestionSelection,
    handleUndo,
    isSpinning,
    isWebsiteRequirementsTaskSelected,
    selectedTask,
    setAssistantInputValue,
    setAssistantPlacement,
    studyTaskMode,
    summaryGenerated,
  ]);

  const renderAssistantPane = useCallback(() => assistantPaneNode, [assistantPaneNode]);

  // Filter tasks based on required status, filters, and search query
  useEffect(() => {
    // Update playground task status based on user settings before filtering
    // This re-checks the settings every time the effect runs, including when returning to tasks view
    const playgroundCompleted = isPlaygroundCompletedFromSettings(user?.settings);
    const tasksWithUpdatedPlayground = allTasks.map((task: any) => {
      if (task.id === 'playground') {
        return { ...task, status: playgroundCompleted ? 'completed' : 'not-started' };
      }
      return task;
    });
    
    // First filter by required status (skip if secret password or internal reviewer — full catalog)
    let tasksAfterRequiredFilter: any[];
    if (hasSecretPassword || isInternalReviewer) {
      // Show all tasks but:
      // 1. Deduplicate by ID to avoid duplicate playground/tutorial tasks
      // 2. Filter out any tutorial tasks from API (we already have playground)
      const seenIds = new Set<string>();
      tasksAfterRequiredFilter = tasksWithUpdatedPlayground.filter((task: any) => {
        // Skip if duplicate ID
        if (seenIds.has(task.id)) {
          return false;
        }
        // Skip tutorial tasks from API (we already have playground)
        if (task.id !== 'playground' && (task.category === 'tutorial' || task.tags?.includes('tutorial'))) {
          return false;
        }
        seenIds.add(task.id);
        return true;
      });
    } else {
      tasksAfterRequiredFilter = filterTasksByRequiredStatus(tasksWithUpdatedPlayground);
    }

    if (allowsMultipleSubmissions) {
      tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
        if (task.id === 'playground') return false;
        if (task.category === 'tutorial' || task.tags?.includes('tutorial')) return false;
        if (isWebsiteRequirementTask(task)) return false;
        return true;
      });
    }
    
    // Then filter by status
    // Always include playground task regardless of status filters
    tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
      if (task.id === 'playground') {
        return true; // Always show playground
      }
      const taskStatus = task.status || 'not-started';
      return statusFilters[taskStatus as keyof typeof statusFilters];
    });
    
    // Then filter by category (label field)
    // Always include playground task regardless of category filters
    tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
      if (task.id === 'playground') {
        return true; // Always show playground
      }
      const rawTaskLabel = (task.label || 'open-ended').toLowerCase();
      const taskLabel = rawTaskLabel === 'website_requirements' ? 'replication' : rawTaskLabel;
      return categoryFilters[taskLabel as keyof typeof categoryFilters];
    });
    
    // Then filter by search query if provided
    if (searchQuery.trim() === "") {
      setFilteredTasks(tasksAfterRequiredFilter);
    } else {
      const filtered = tasksAfterRequiredFilter.filter((task: any) =>
        task.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        task.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (task.tags && task.tags.some((tag: string) => tag.toLowerCase().includes(searchQuery.toLowerCase())))
      );
      setFilteredTasks(filtered);
    }
  }, [
    searchQuery,
    allTasks,
    filterTasksByRequiredStatus,
    statusFilters,
    categoryFilters,
    showCodingTerminal,
    pathname,
    hasSecretPassword,
    isInternalReviewer,
    allowsMultipleSubmissions,
    user,
  ]);

  // Close filter modal when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      // Check if click is outside the modal and not on the filter button
      if (
        filterModalRef.current && 
        !filterModalRef.current.contains(target) &&
        !target.closest('button')?.querySelector('svg[class*="lucide-filter"]')
      ) {
        setShowFilterModal(false);
      }
    };

    if (showFilterModal) {
      // Use a small delay to avoid closing immediately when opening
      const timeoutId = setTimeout(() => {
        document.addEventListener('mousedown', handleClickOutside);
      }, 100);

      return () => {
        clearTimeout(timeoutId);
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [showFilterModal]);

  // Track when we want to focus the assistant input
  const [shouldFocusAssistant, setShouldFocusAssistant] = useState(false);

  // Focus assistant input when it becomes available and we requested focus
  useEffect(() => {
    if (shouldFocusAssistant && showAIAssistant && assistantTerminalPaneRef.current) {
      // Use requestAnimationFrame to ensure DOM is ready
      requestAnimationFrame(() => {
        assistantTerminalPaneRef.current?.focusInput();
        setShouldFocusAssistant(false);
      });
    }
  }, [shouldFocusAssistant, showAIAssistant]);

  // Keyboard shortcuts for sidebar toggle and AI assistant toggle
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const isCmdOrCtrl = event.metaKey || event.ctrlKey;
      const key = (event.key || '').toLowerCase();
      const code = event.code || '';

      // Escape: Exit out of any text field (blur focused editable elements, including Monaco)
      if (!event.metaKey && !event.ctrlKey && !event.altKey && !event.shiftKey && (key === 'escape' || code === 'Escape')) {
        const activeEl = document.activeElement as HTMLElement | null;
        
        // If active element is a standard input/textarea (but not Monaco), blur it
        if (activeEl) {
          // Check if it's Monaco first - Monaco handles its own Escape, but we provide fallback
          const isMonacoFocused = activeEl.closest('.monaco-editor') || 
                                  activeEl.closest('[data-editor-id]') ||
                                  activeEl.closest('.monaco-scrollable-element');
          
          if (!isMonacoFocused) {
            // Not Monaco - handle standard inputs/textareas
            if (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA') {
              event.preventDefault();
              event.stopPropagation();
              (activeEl as HTMLElement).blur();
              return;
            }
            
            // If it's contentEditable, blur it
            if (activeEl.isContentEditable) {
              event.preventDefault();
              event.stopPropagation();
              activeEl.blur();
              return;
            }
          } else {
            // Monaco is focused - let Monaco's handler deal with it, but also ensure we blur
            // Monaco should handle Escape via our custom action, but provide fallback
            try {
              const monacoEditor = (actualEditorRef.current as any)?.getMonacoEditor?.();
              if (monacoEditor && typeof monacoEditor.trigger === 'function') {
                // Try to trigger escape action if it exists
                monacoEditor.trigger('editor', 'escape-to-unfocus', null);
              }
            } catch (e) {
              // Fallback: focus body
              document.body.focus();
            }
            // Don't prevent default for Monaco - let Monaco handle it
            return;
          }
        }
        
        // Final fallback: ensure body has focus
        document.body.focus();
      }

      // Cmd/Ctrl + I or L: Focus AI assistant input (open pane if closed)
      // MUST be checked FIRST and override ALL other handlers, even when typing
      // This should work regardless of focus state or what the user is typing
      if (isCmdOrCtrl && (key === 'i' || key === 'l' || code === 'KeyI' || code === 'KeyL')) {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation(); // Stop all other handlers from running
        
        // If assistant is closed, open it first and mark that we want to focus
        if (!showAIAssistant) {
          setShowAIAssistant(true);
          setShouldFocusAssistant(true);
        } else {
          // Focus immediately if already open
          requestAnimationFrame(() => {
            assistantTerminalPaneRef.current?.focusInput();
          });
        }
        return;
      }

      // Cmd/Ctrl + B: Toggle AI assistant
      // MUST override ALL other handlers, even when typing
      if (isCmdOrCtrl && (key === 'b' || code === 'KeyB')) {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation(); // Stop all other handlers from running
        setShowAIAssistant(!showAIAssistant);
        return;
      }

      const activeEl = document.activeElement as HTMLElement | null;
      const tag = activeEl?.tagName?.toLowerCase();
      const isTyping = tag === 'input' || tag === 'textarea' || activeEl?.isContentEditable;

      // Cmd/Ctrl + shortcuts for panes
      if (isCmdOrCtrl && showCodingTerminal && selectedTask) {
        const isBracketLeft = key === '[' || code === 'BracketLeft';
        const isBracketRight = key === ']' || code === 'BracketRight';
        const isOpenParen = key === '(' || code === 'Digit9' || code === 'Numpad9';
        const isCloseParen = key === ')' || code === 'Digit0' || code === 'Numpad0';
        if (isOpenParen || isCloseParen) {
          event.preventDefault();
          event.stopPropagation();
          try { (event as any).stopImmediatePropagation?.(); } catch(_) {}
          if (isOpenParen) {
            setRightTab('code');
          } else if (isCloseParen && canShowViewSubmissionsTab && isViewSubmissionsUnlocked) {
            setRightTab('submissions');
          }
          return;
        }
        if (!isTyping && (isBracketLeft || isBracketRight)) {
          event.preventDefault();
          event.stopPropagation();
          try { (event as any).stopImmediatePropagation?.(); } catch(_) {}
          if (isBracketLeft) {
            setLeftTab('task');
          } else if (isBracketRight) {
            // Switch to preview or project details based on tab
            if (rightTab === 'submissions' && viewedSubmission) {
              setLeftTab('project-details');
            } else if (rightTab !== 'submissions') {
              void sendCodeLog('preview-refresh', { refreshSource: 'tab-switch' });
              setLeftTab('preview');
            }
          }
          return;
        }
      }

      // Only handle other shortcuts when not typing
      if (isTyping) return;

      // Tab: Toggle sidebar whenever user isn't typing (prevents focus navigation)
      if (key === 'tab') {
        event.preventDefault();
        event.stopPropagation();
        toggleSidebar();
        return;
      }
    };

    // Also intercept keyup to guard against browser history on keyup
    const handleKeyUp = (event: KeyboardEvent) => {
      const isCmdOrCtrl = event.metaKey || event.ctrlKey;
      const key = (event.key || '').toLowerCase();
      const code = event.code || '';
      const activeEl = document.activeElement as HTMLElement | null;
      const tag = activeEl?.tagName?.toLowerCase();
      const isTyping = tag === 'input' || tag === 'textarea' || activeEl?.isContentEditable;
      if (isCmdOrCtrl && showCodingTerminal && selectedTask) {
        if (
          key === '[' || code === 'BracketLeft' ||
          key === ']' || code === 'BracketRight' ||
          key === '(' || code === 'Digit9' || code === 'Numpad9' ||
          key === ')' || code === 'Digit0' || code === 'Numpad0'
        ) {
          event.preventDefault();
          event.stopPropagation();
          try { (event as any).stopImmediatePropagation?.(); } catch(_) {}
        }
      }
    };

    // Use capture phase to catch events early, before ALL other handlers
    // This ensures our handler runs first and can override others
    document.addEventListener('keydown', handleKeyDown, true);
    document.addEventListener('keyup', handleKeyUp, true);
    return () => {
      document.removeEventListener('keydown', handleKeyDown, true);
      document.removeEventListener('keyup', handleKeyUp, true);
    };
  }, [showAIAssistant, showCodingTerminal, selectedTask, canShowViewSubmissionsTab, isViewSubmissionsUnlocked]);

  // Generate initial files for each task type
  const getInitialFilesForTask = async (
    taskId: string,
    abortSignal?: AbortSignal
  ): Promise<{
    files: any[];
    projectId: number | null;
    votingStartDate: string | null;
    codeStartDate: string | null;
    votingEndDate: string | null;
  }> => {
    try {
      // Use playground-files API for playground mode
      if (isPlaygroundMode || taskId === 'playground') {
        const response = await fetch(`/api/playground-files`, { signal: abortSignal });
        if (response.ok) {
          const data = await response.json();
          const files = Array.isArray(data.files) ? data.files : [];
          // Playground doesn't have projectId or dates
          return { files, projectId: null, votingStartDate: null, codeStartDate: null, votingEndDate: null };
        } else {
          console.error('Failed to load playground files:', response.statusText);
          return { files: [], projectId: null, votingStartDate: null, codeStartDate: null, votingEndDate: null };
        }
      }
      
      // Include userId if user is authenticated
      const userIdParam = user?.id ? `&userId=${encodeURIComponent(user.id)}` : '';
      const response = await fetch(`/api/task-files?taskId=${taskId}${userIdParam}`, { signal: abortSignal });
      if (response.ok) {
        const data = await response.json();
        const files = Array.isArray(data.files) ? data.files : [];
        const projectId = typeof data.projectId === 'number' ? data.projectId : null;
        const votingStartDate = typeof data.votingStartDate === 'string' ? data.votingStartDate : null;
        const codeStartDate = typeof data.codeStartDate === 'string' ? data.codeStartDate : null;
        const votingEndDate = typeof data.votingEndDate === 'string' ? data.votingEndDate : null;
        return { files, projectId, votingStartDate, codeStartDate, votingEndDate };
      } else {
        console.error('Failed to load task files:', response.statusText);
        return { files: [], projectId: null, votingStartDate: null, codeStartDate: null, votingEndDate: null };
      }
    } catch (error: any) {
      // Ignore abort errors
      if (error.name === 'AbortError') {
        return { files: [], projectId: null, votingStartDate: null, codeStartDate: null, votingEndDate: null };
      }
      console.error('Error loading task files:', error);
      return { files: [], projectId: null, votingStartDate: null, codeStartDate: null, votingEndDate: null };
    }
  };

  // Helper functions to get task data

  const getTaskDescription = (taskId: string): string => {
    // For playground mode, return empty string - instructions will be loaded from blank_site/instructions.html
    if (isPlaygroundMode || taskId === 'playground') {
      return "";
    }
    const task = allTasks.find(t => t.id === taskId);
    return task?.description || "";
  };

  const getTaskRequirements = (taskId: string): string[] => {
    if (isPlaygroundMode || taskId === 'playground') {
      return [];
    }
    const task = allTasks.find(t => t.id === taskId);
    return Array.isArray(task?.requirements) ? task.requirements : [];
  };

  // Function to load and organize test cases
  const loadTestCases = async (task: any, abortSignal?: AbortSignal) => {
    try {
      // Check if there are any tests defined in the task
      const tests = task?.tests || [];
      if (!tests || tests.length === 0) {
        setTestCases([]);
        return;
      }

      const response = await fetch(`${ENV.BACKEND_URL}/api/load-test-cases`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ 
          task, 
          public_only: ENV.SHOW_PUBLIC_TESTS_ONLY 
        }),
        signal: abortSignal,
      });
      
      if (response.ok) {
        const data = await response.json();
        setTestCases(data.testCases || []);
      } else {
        console.error("Failed to load test cases:", response.status, response.statusText);
        setTestCases([]);
      }
    } catch (error: any) {
      // Ignore abort errors
      if (error.name === 'AbortError') {
        return;
      }
      console.error("Error loading test cases:", error);
      setTestCases([]);
    }
  };

  const startTask = async (taskId: string, updateUrl: boolean) => {
    suppressCodeLogsRef.current = false;
    // Abort any previous task loading requests
    if (taskAbortControllerRef.current) {
      taskAbortControllerRef.current.abort();
    }
    
    const abortController = new AbortController();
    taskAbortControllerRef.current = abortController;
    
    // Clear previous task state before starting new task
    cleanupTaskState();
    
    // Set loading state for files
    setIsLoadingFiles(true);
    
    // Calculate width immediately to prevent glitch before showing coding terminal
    const container = containerRef.current;
    if (container) {
      const rect = container.getBoundingClientRect();
      const handleWidth = 4;
      const leftWidth = (rect.width - handleWidth) * (1/3);
      setLeftColumnWidth(leftWidth);
    } else {
      // Fallback: estimate based on viewport width (accounting for sidebar)
      const sidebarWidth = sidebarOpen ? 256 : 48; // ml-64 = 256px, ml-12 = 48px
      const estimatedContainerWidth = window.innerWidth - sidebarWidth - 48; // subtract padding
      const handleWidth = 4;
      const leftWidth = (estimatedContainerWidth - handleWidth) * (1/3);
      setLeftColumnWidth(leftWidth);
    }
    
    // Batch critical state updates to prevent glitching using React transitions
    startTransition(() => {
      setSelectedTask(taskId);
      setTaskId(taskId);
      setShowCodingTerminal(true);
      // Default left pane to Task tab
      setLeftTab('task');
    });

    // For playground mode, load instructions from playground files
    let description = getTaskDescription(taskId);
    if (isPlaygroundMode || taskId === 'playground') {
      try {
        const playgroundResponse = await fetch(`/api/playground-files`, { signal: abortController.signal });
        if (playgroundResponse.ok) {
          const playgroundData = await playgroundResponse.json();
          // Instructions are returned separately, not in the files array
          if (playgroundData.instructions) {
            description = playgroundData.instructions;
          }
        }
      } catch (error) {
        console.warn('Failed to load playground instructions:', error);
      }
    }
    setTaskDescriptions([description]);

    let fetchedProjectId: number | null = null;
    let fetchedVotingStartDate: string | null = null;
    let fetchedCodeStartDate: string | null = null;
    let fetchedVotingEndDate: string | null = null;

    try {
      const {
        files,
        projectId,
        votingStartDate,
        codeStartDate,
        votingEndDate,
      } = await getInitialFilesForTask(taskId, abortController.signal);
      const task = allTasks.find((t: any) => t.id === taskId);
      const isTimedTask =
        !allowsMultipleSubmissions &&
        task &&
        TIMED_TASKS.includes(normalizeTaskNameKey(task.name) as any);
      if (isTimedTask) {
        timedTaskFilesPendingRef.current = files;
        setInitialFiles([]);
      } else {
        setInitialFiles(files);
      }
      fetchedProjectId = projectId;
      fetchedVotingStartDate = votingStartDate;
      fetchedCodeStartDate = codeStartDate;
      fetchedVotingEndDate = votingEndDate;
    } catch (error) {
      // Ignore abort errors
      if (error instanceof Error && error.name === 'AbortError') {
        return;
      }
      console.error('Error loading files:', error);
      setInitialFiles([]);
    } finally {
      setIsLoadingFiles(false);
    }

    const task = allTasks.find(t => t.id === taskId);
    const resolvedProjectId = fetchedProjectId ?? task?.projectId ?? null;
    
    // For playground mode, set a default task name
    const taskName = isPlaygroundMode || taskId === 'playground' 
      ? 'Playground' 
      : (task?.name || task?.title || '');
    
    setCurrentTaskMeta(
      task
        ? {
            id: task.id,
            name: task.name,
            projectId: resolvedProjectId ?? undefined,
            votingStartDate: task.votingStartDate ?? fetchedVotingStartDate ?? null,
            votingEndDate: task.votingEndDate ?? fetchedVotingEndDate ?? null,
            codeStartDate: task.codeStartDate ?? fetchedCodeStartDate ?? null,
          }
        : {
            id: taskId,
            name: taskName,
            projectId: resolvedProjectId ?? undefined,
            votingStartDate: fetchedVotingStartDate ?? null,
            votingEndDate: fetchedVotingEndDate ?? null,
            codeStartDate: fetchedCodeStartDate ?? null,
          }
    );
    // Clear suggestions when starting a new task (already done in cleanupTaskState above)
    // Don't load test cases for playground mode
    if (task && !isPlaygroundMode && taskId !== 'playground') {
      loadTestCases(task, abortController.signal);
    }

    const viewportHeight = window.innerHeight - 32;
    const halfHeight = viewportHeight * 0.5;
    setTaskInstructionHeight(halfHeight);
    setEditorHeight(halfHeight);
    // Refine width with rAF once container is fully laid out (width already set above)
    requestAnimationFrame(() => {
      const container = containerRef.current;
      if (container) {
        const rect = container.getBoundingClientRect();
        const handleWidth = 4;
        const leftWidth = (rect.width - handleWidth) * (1/3);
        setLeftColumnWidth(leftWidth);
      }
    });

    if (updateUrl) {
      router.push(`/vibe?task=${taskId}`);
    }
  };


  // Prevent submissions tab in playground and website-requirements modes
  useEffect(() => {
    if (
      (isPlaygroundMode ||
        selectedTask === 'playground' ||
        isWebsiteRequirementsTaskSelected ||
        (isTimedTaskSelected && !isInternalReviewer)) &&
      rightTab === 'submissions'
    ) {
      setRightTab('code');
    }
  }, [
    isPlaygroundMode,
    selectedTask,
    isWebsiteRequirementsTaskSelected,
    isTimedTaskSelected,
    isInternalReviewer,
    rightTab,
  ]);

  // Handle task parameter from URL
  useEffect(() => {
    const handleTaskParam = async () => {
      const taskParam = searchParams.get('task');
      const redirectToBrowse = () => {
        router.replace('/browse');
        setShowCodingTerminal(false);
        setSelectedTask(null);
        setTaskId("");
        setCurrentTaskMeta(null);
        cleanupTaskState();
      };
      
      // Playground task is handled the same way as other tasks via task parameter
      
      if (taskParam && allTasks.length > 0) {
        // URL may use raw project name (e.g. snake_game) while task.id is slugified (snake-game).
        const normalizeRouteSlug = (s: string) =>
          s
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-|-$/g, "");
        let canonicalTaskId = taskParam;
        const byId = allTasks.find((t: any) => t.id === taskParam);
        if (byId) {
          canonicalTaskId = byId.id;
        } else {
          const byName = allTasks.find(
            (t: any) =>
              String(t.name || "").toLowerCase() === taskParam.toLowerCase()
          );
          if (byName) {
            canonicalTaskId = byName.id;
          } else {
            const slug = normalizeRouteSlug(taskParam);
            const bySlug = allTasks.find((t: any) => t.id === slug);
            if (bySlug) canonicalTaskId = bySlug.id;
          }
        }

        // Match browse behavior for visibility and lock checks before honoring direct URL access.
        const playgroundCompleted = isPlaygroundCompletedFromSettings(user?.settings);
        const tasksWithUpdatedPlayground = allTasks.map((task: any) => {
          if (task.id === 'playground') {
            return { ...task, status: playgroundCompleted ? 'completed' : 'not-started' };
          }
          return task;
        });

        let visibleTasks: any[];
        if (hasSecretPassword || isInternalReviewer) {
          const seenIds = new Set<string>();
          visibleTasks = tasksWithUpdatedPlayground.filter((task: any) => {
            if (seenIds.has(task.id)) {
              return false;
            }
            if (task.id !== 'playground' && (task.category === 'tutorial' || task.tags?.includes('tutorial'))) {
              return false;
            }
            seenIds.add(task.id);
            return true;
          });
        } else {
          visibleTasks = filterTasksByRequiredStatus(tasksWithUpdatedPlayground);
        }

        if (allowsMultipleSubmissions) {
          visibleTasks = visibleTasks.filter((task: any) => {
            if (task.id === 'playground') return false;
            if (task.category === 'tutorial' || task.tags?.includes('tutorial')) return false;
            if (isWebsiteRequirementTask(task)) return false;
            return true;
          });
        }

        const visibleTaskIds = new Set(visibleTasks.map((task: any) => task.id));
        if (!visibleTaskIds.has(canonicalTaskId)) {
          redirectToBrowse();
          return;
        }

        const lockedTaskIds = new Set<string>();
        if (!hasSecretPassword && !isInternalReviewer && !allowsMultipleSubmissions) {
          const otherTasks = tasksWithUpdatedPlayground.filter((task: any) => task.id !== 'playground');
          const completedTaskNames = new Set(
            otherTasks
              .filter((task: any) => task.status === 'completed')
              .map((task: any) => task.name)
          );
          const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
          const requiredTaskNames = getRequiredTasksForMode(mode, otherTasks);
          const allRequiredCompleted = requiredTaskNames.every((taskName) => completedTaskNames.has(taskName));
          const effectiveRequiredCompleted = studyEnded ? true : allRequiredCompleted;
          const isWebsiteRequirementsMode = mode === 'website-requirements';
          const shouldEnableLocking = isWebsiteRequirementsMode || !effectiveRequiredCompleted;

          if (shouldEnableLocking) {
            let activeId: string | null = null;
            const playgroundTask = visibleTasks.find((task: any) => task.id === 'playground');
            const hasPlaygroundTask = !!playgroundTask;
            const isPlaygroundCompleted = playgroundTask?.status === 'completed';

            for (const task of visibleTasks) {
              if (task.id === 'playground') continue;

              if (!isWebsiteRequirementsMode && hasPlaygroundTask && !isPlaygroundCompleted) {
                lockedTaskIds.add(task.id);
                continue;
              }

              const isCompleted = task.status === 'completed';
              if (isWebsiteRequirementsMode && isCompleted && !allowsMultipleSubmissions) {
                lockedTaskIds.add(task.id);
              } else if (!isCompleted && activeId === null) {
                activeId = task.id;
              } else if (!isCompleted && activeId !== null) {
                lockedTaskIds.add(task.id);
              }
            }
          }
        }

        if (lockedTaskIds.has(canonicalTaskId)) {
          redirectToBrowse();
          return;
        }

        const task = allTasks.find((t) => t.id === canonicalTaskId);
        if (!task) {
          redirectToBrowse();
          return;
        }
        if (selectedTask !== task.id) {
          await startTask(task.id, false);
        }
        if (taskParam !== canonicalTaskId) {
          router.replace(`/vibe?task=${encodeURIComponent(canonicalTaskId)}`);
        }
      } else if (!taskParam && pathname === '/vibe' && showCodingTerminal) {
        // No task param and we're in coding terminal mode, redirect to browse
        redirectToBrowse();
      }
    };

    handleTaskParam();
  }, [
    searchParams,
    allTasks,
    selectedTask,
    pathname,
    showCodingTerminal,
    isPlaygroundMode,
    hasSecretPassword,
    isInternalReviewer,
    allowsMultipleSubmissions,
    filterTasksByRequiredStatus,
    user?.settings,
    studyEnded,
    router,
    cleanupTaskState,
    websiteRequirementsSkipped,
  ]);

  // Force hide tooltips when pathname changes (tab navigation)
  useEffect(() => {
    // Hide React state-based tooltips immediately
    setTooltipVisible(false);
    setTooltipText("");
    
    // Hide all CSS-based tooltips by adding a class that forces them to be hidden
    document.body.classList.add('force-hide-tooltips');
    
    // Remove the class after a delay to allow normal tooltip behavior
    const timer = setTimeout(() => {
      document.body.classList.remove('force-hide-tooltips');
    }, 100);

    return () => {
      clearTimeout(timer);
      document.body.classList.remove('force-hide-tooltips');
    };
  }, [pathname]);

  // Force hide tooltips when showCodingTerminal changes (when entering/exiting task mode)
  useEffect(() => {
    setTooltipVisible(false);
    setTooltipText("");
  }, [showCodingTerminal]);

  // When on /vibe without a task parameter, we'll redirect to playground in the effect above
  // This effect handles cleanup if we somehow end up on /vibe without a task
  useEffect(() => {
    const taskParam = searchParams.get('task');
    const currentPath = pathname;
    
    // Only run this logic when we're on /vibe without a task param (shouldn't happen due to redirect, but handle it)
    if (currentPath === '/vibe' && !taskParam) {
      // This should be handled by the redirect above, but just in case
      router.push('/browse');
      setExpandedTask(null);
      setShowCodingTerminal(false);
      setSelectedTask(null);
      setTaskId("");
      setCurrentTaskMeta(null);
      cleanupTaskState();
    }
  }, [searchParams, pathname]);

  // Handle browser back/forward navigation
  useEffect(() => {
    const handlePopState = async () => {
      try {
        const path = window.location.pathname;
        const urlParams = new URLSearchParams(window.location.search);
        const taskParam = urlParams.get('task');
        
        if (path === '/vibe' || path === '/') {
          if (taskParam) {
            const taskExists = allTasks.some(t => t.id === taskParam);
            if (taskExists) {
              await startTask(taskParam, false);
            } else {
              // Task doesn't exist, redirect to browse
              router.push('/browse');
              setExpandedTask(null);
              setShowCodingTerminal(false);
              setSelectedTask(null);
              setTaskId("");
              setCurrentTaskMeta(null);
              cleanupTaskState();
            }
          } else {
            // No task param, redirect to browse
            router.push('/browse');
            setExpandedTask(null);
            setShowCodingTerminal(false);
            setSelectedTask(null);
            setTaskId("");
            setCurrentTaskMeta(null);
            cleanupTaskState();
          }
        }
      } catch (e) {
        // no-op
      }
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  // Define handleDownloadProject before conditional returns (hooks must be called unconditionally)
  const handleDownloadProject = useCallback(async () => {
    try {
      const codeByLanguage = getCodeByLanguage();
      if (!codeByLanguage) {
        console.warn('No code available to download');
        return;
      }

      const projectName = currentTaskMeta?.name || selectedTask || 'VibeJam Project';
      const taskName = currentTaskMeta?.name || undefined;
      
      // Compute task description directly to avoid hook dependency issues
      let taskDescription: string | undefined = undefined;
      if (taskDescriptions.length > 0) {
        taskDescription = taskDescriptions[0];
      }
      
      // Use custom title/description if provided (from title/description page), otherwise use task info
      const customTitle = customProjectTitle.trim() || undefined;
      const customDescription = customProjectDescription.trim() || undefined;
      
      await downloadProjectAsRepository(
        {
          html: codeByLanguage.html || '',
          css: codeByLanguage.css || '',
          js: codeByLanguage.js || '',
        },
        projectName,
        taskName,
        taskDescription,
        customTitle,
        customDescription
      );
      
      showSnackbar('Thanks for downloading! Unzip the file to see a GitHub repo with steps to run your game locally or host it online for free!');
      
      // Log download event
      await sendCodeLog('download');
    } catch (error) {
      console.error('Failed to download project:', error);
    }
  }, [getCodeByLanguage, currentTaskMeta?.name, selectedTask, taskDescriptions, customProjectTitle, customProjectDescription, sendCodeLog]);

  const handleOpenSubmitModal = useCallback(() => {
    setIsSubmitModalExitLocked(false);
    try {
      window.dispatchEvent(new Event('open-submit-modal'));
    } catch {}
  }, []);

  const handleOpenSubmitQuestionsDirect = useCallback(() => {
    setIsSubmitModalExitLocked(true);
    try {
      window.dispatchEvent(new CustomEvent('open-submit-modal', {
        detail: {
          skipInitialConfirmation: true,
        },
      }));
    } catch {}
  }, []);

  // Show loading state while checking authentication
  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="xl" color="white" className="mx-auto mb-4" />
          <p className="text-gray-400">Loading...</p>
        </div>
      </div>
    );
  }

  // Don't render anything if not authenticated - will redirect via useRouteProtection
  if (!isAuthenticated) {
    return null;
  }

  // If we're on /vibe without a task parameter, show loading while redirecting
  const taskParam = searchParams?.get('task');
  if (pathname === '/vibe' && searchParams && !taskParam) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="xl" color="white" className="mx-auto mb-4" />
          <p className="text-gray-400">Redirecting...</p>
        </div>
      </div>
    );
  }

  // When on /vibe?task=X, stay in loading until tasks have loaded so we can resolve task type
  if (pathname === '/vibe' && taskParam && isLoadingTasks) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="xl" color="white" className="mx-auto mb-4" />
          <p className="text-gray-400">Loading...</p>
        </div>
      </div>
    );
  }

  // For website_requirements tasks, stay in initial loading until task type and experiment_group have loaded
  if (isWebsiteRequirementsTaskSelected && showCodingTerminal && !isWebsiteRequirementsDataReady) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="xl" color="white" className="mx-auto mb-4" />
          <p className="text-gray-400">Loading...</p>
        </div>
      </div>
    );
  }

  // (removed duplicate useEffects that were placed after auth guards)

  const handleTaskClick = (taskId: string) => {
    try {
      window.history.pushState(null, '', `/vibe?task=${taskId}`);
    } catch (e) {
      // no-op
    }
  };

  const handleRandomTask = async () => {
    if (!confirmLeaveStudyIfNeeded()) return;

    // Exclude playground/tutorial task from random selection
    const tasksWithoutPlayground = filteredTasks.filter(task => task.id !== 'playground');
    if (tasksWithoutPlayground.length === 0) return;
    const randomIndex = Math.floor(Math.random() * tasksWithoutPlayground.length);
    const randomTask = tasksWithoutPlayground[randomIndex];
    router.push(`/vibe?task=${randomTask.id}`);
  };

  const handleSaveToggle = (taskId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent task click
    // Update the task's saved status
    const updatedTasks = filteredTasks.map(task => 
      task.id === taskId ? { ...task, saved: !task.saved } : task
    );
    setFilteredTasks(updatedTasks);
  };

  const handleGetStarted = (taskId: string) => {
    if (!confirmLeaveStudyIfNeeded()) return;

    // Optimistically expand the task immediately
    setExpandedTask(taskId);

    // Push URL immediately for instant navigation; URL effect will start the task
    router.push(`/vibe?task=${taskId}`);
  };

  const handleViewSubmissionsFromGrid = (taskId: string) => {
    if (!confirmLeaveStudyIfNeeded()) return;
    router.push(`/vibe?task=${taskId}&view=submissions`);
  };

  const handleGoBack = () => {
    if (!confirmLeaveStudyIfNeeded()) return;

    startTransition(() => {
      setExpandedTask(null);
      setShowCodingTerminal(false);
      setSelectedTask(null);
      setTaskId("");
      setCurrentTaskMeta(null);
    });
    // Navigate to browse page
    router.push('/browse');
  };

  const handleTaskExpand = (taskId: string) => {
    if (expandedTask === taskId) {
      setExpandedTask(null);
    } else {
      setExpandedTask(taskId);
    }
  };

  // Editor mount handler
  const handleEditorMount = (editor: any, monaco: any) => {
    editorRef.current = editor;
  };

  // Helper function to get status icon (LeetCode style)
  const getStatusIcon = (status: string) => {
    switch (status) {
      case "completed":
        return (
          <div className="relative">
            <CheckCircle className="peer h-5 w-5 text-green-500 hover:text-green-400 transition-colors cursor-help" />
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 peer-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
              Submitted
            </div>
          </div>
        );
      case "in-progress":
        return (
          <div className="relative">
            <div className="peer h-5 w-5 relative hover:scale-110 transition-transform cursor-help">
              <Circle className="h-5 w-5 text-white hover:text-blue-300 transition-colors" />
              <div className="absolute inset-0 flex items-center justify-center">
                <div className="w-3 h-0.5 bg-white"></div>
              </div>
            </div>
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 peer-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
              In Progress
            </div>
          </div>
        );
      case "not-started":
      default:
        return (
          <div className="relative">
            <Circle className="peer h-5 w-5 text-gray-500 hover:text-gray-400 transition-colors cursor-help" />
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 peer-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
              Not Started
            </div>
          </div>
        );
    }
  };

  // Helper function to get save icon with tooltip (LeetCode style)
  const getSaveIcon = (saved: boolean) => {
    return (
      <div className="relative">
        {saved ? (
          <Star className="peer h-4 w-4 text-yellow-400 fill-current hover:text-yellow-300 transition-colors cursor-help" />
        ) : (
          <Star className="peer h-4 w-4 text-gray-500 hover:text-gray-400 transition-colors cursor-help" />
        )}
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 peer-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
          {saved ? "Remove from saved" : "Save task"}
        </div>
      </div>
    );
  };

  // Helper function to get difficulty color
  const getDifficultyColor = (difficulty: string) => {
    switch (difficulty) {
      case "Beginner":
        return "text-green-400";
      case "Intermediate":
        return "text-yellow-400";
      case "Advanced":
        return "text-red-400";
      default:
        return "text-gray-400";
    }
  };

  // Helper function to get difficulty badge colors (LeetCode style)
  const getDifficultyBadgeColors = (difficulty: string) => {
    switch (difficulty) {
      case "Beginner":
        return "text-green-400";
      case "Intermediate":
        return "text-orange-400";
      case "Advanced":
        return "text-red-400";
      default:
        return "text-gray-400";
    }
  };

  // Helper function to get app type badge colors
  const getAppTypeBadgeColors = (appType: string) => {
    switch (appType) {
      case "Game":
        return "bg-purple-500/20 text-purple-400 border-purple-500/30";
      case "Widget":
        return "bg-blue-500/20 text-blue-400 border-blue-500/30";
      default:
        return "bg-gray-500/20 text-gray-400 border-gray-500/30";
    }
  };


  return (
    <div className="h-screen overflow-y-auto overflow-x-hidden bg-gray-900 text-white relative">
      {/* Space Theme with Jam-Colored Stars */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none z-0">
          {/* Large stars */}
          {backgroundStars.largeStars.map((star, i) => (
            <div
              key={`star-${i}`}
              className="absolute rounded-full"
              style={{
                width: `${star.size}px`,
                height: `${star.size}px`,
                left: `${star.left}%`,
                top: `${star.top}%`,
                backgroundColor: star.color,
                opacity: star.opacity,
                boxShadow: `0 0 ${star.size * 2}px ${star.color}, 0 0 ${star.size * 4}px ${star.color}`,
              }}
            />
          ))}
          
          {/* Medium stars */}
          {backgroundStars.mediumStars.map((star, i) => (
            <div
              key={`medium-${i}`}
              className="absolute rounded-full"
              style={{
                width: `${star.size}px`,
                height: `${star.size}px`,
                left: `${star.left}%`,
                top: `${star.top}%`,
                backgroundColor: star.color,
                opacity: star.opacity,
                boxShadow: `0 0 ${star.size * 1.5}px ${star.color}`,
              }}
            />
          ))}
          
          {/* Small twinkling dots */}
          {backgroundStars.smallDots.map((dot, i) => (
            <div
              key={`dot-${i}`}
              className="absolute rounded-full"
              style={{
                width: `${dot.size}px`,
                height: `${dot.size}px`,
                left: `${dot.left}%`,
                top: `${dot.top}%`,
                backgroundColor: dot.color,
                opacity: dot.opacity,
              }}
            />
          ))}
          
          {/* Animated jam-like dots moving across screen - hidden on vibe page */}
          {pathname !== '/vibe' && backgroundStars.animatedDots.map((dot, i) => (
            <div
              key={`animated-dot-${i}`}
              className="absolute rounded-full"
              style={{
                width: `${dot.size}px`,
                height: `${dot.size}px`,
                top: `${dot.top}%`,
                left: dot.direction === 'left-to-right' ? '-20px' : 'calc(100% + 20px)',
                backgroundColor: dot.color,
                opacity: dot.opacity,
                boxShadow: `0 0 ${dot.size * 1.5}px ${dot.color}, 0 0 ${dot.size * 3}px ${dot.color}`,
                animation: `moveAcross${dot.direction === 'left-to-right' ? 'Right' : 'Left'} ${dot.duration}s linear ${dot.delay}s infinite`,
              }}
            />
          ))}
        </div>
      
      {/* View menu button removed */}

      {/* Main Content */}
      <div ref={containerRef} className={`${sidebarOpen ? 'ml-60' : 'ml-12'} flex h-screen overflow-x-hidden ${showCodingTerminal ? 'px-6' : ''} relative z-10`}>
        {/* Left Side */}
        <div
          ref={leftPaneRef}
          className={`flex flex-col box-border ${showCodingTerminal ? 'pt-2 pb-6 px-6' : ''} h-full max-w-full ${showCodingTerminal && selectedTask && leftTab === 'leaderboard' ? 'flex-1 min-w-0' : ''}`}
          style={{ width: showCodingTerminal && leftTab !== 'leaderboard' ? (leftColumnWidth || '33.333%') : '100%', willChange: isResizing ? 'width' as any : undefined, order: isSwapped ? 2 as any : 0 as any }}
        >
          {/* Header, Search Bar, and Content Container - browse page handles this now, vibe only shows coding terminal */}
          {!showCodingTerminal && taskParam && false && (
            <div className="px-40 pt-16 pb-6 w-full max-w-full flex flex-col h-full">
              {/* Greeting - Only show on tasks tab */}
              {activeTab === 'tasks' && (
                <h1 className="text-4xl font-light mb-2 text-center">
                  Hi <span className="font-medium">{user?.username}</span>,
                </h1>
              )}
              {/* Header - Only show for tasks tab */}
              {activeTab === 'tasks' && (
                <div className="text-center mb-16 w-full">
                  <h1 className="text-4xl font-light mb-2 text-center">
                    What do you want to build on {" "}
                    <span className="animated-gradient font-semibold">
                      Vibe Jam
                    </span>
                    ?
                  </h1>
                </div>
              )}

              {/* Search Bar - Only show for tasks tab */}
              {activeTab === 'tasks' && (
                <div className="flex items-center justify-between w-full mb-6">
                  {/* Left side - Search questions, Filter button - 50% width */}
                  <div className="flex items-center space-x-3 w-1/2">
                    {/* Search bar */}
                    <div className="relative flex-1">
                      <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                        <Search className="h-5 w-5 text-gray-400" />
                      </div>
                      <input
                        type="text"
                        placeholder="Search problems"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="block w-full pl-10 pr-3 py-2 border-gray-600 rounded-lg bg-gray-800 text-white placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                      />
                    </div>
                    
                    {/* Filter button with tooltip */}
                    <div className="relative group">
                      <button 
                        onClick={() => setShowFilterModal(!showFilterModal)}
                        className="px-3 h-[38px] rounded-lg bg-gray-800 border border-gray-600 hover:bg-gray-700 transition-colors flex items-center justify-center"
                      >
                        <Filter className="h-4 w-4 text-gray-400 group-hover:text-white transition-colors" />
                      </button>
                      {/* Tooltip */}
                      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
                        Filter tasks
                      </div>
                      
                      {/* Filter dropdown */}
                      {showFilterModal && (
                        <div ref={filterModalRef} className="absolute top-full left-0 mt-2 w-64 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-[200] p-4">
                          <div className="mb-4">
                            <h3 className="text-sm font-semibold text-white mb-2">Status</h3>
                            <div className="space-y-2">
                              <label className="flex items-center space-x-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={statusFilters['completed']}
                                  onChange={(e) => setStatusFilters({...statusFilters, 'completed': e.target.checked})}
                                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-gray-300">Completed</span>
                              </label>
                              <label className="flex items-center space-x-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={statusFilters['in-progress']}
                                  onChange={(e) => setStatusFilters({...statusFilters, 'in-progress': e.target.checked})}
                                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-gray-300">In Progress</span>
                              </label>
                              <label className="flex items-center space-x-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={statusFilters['not-started']}
                                  onChange={(e) => setStatusFilters({...statusFilters, 'not-started': e.target.checked})}
                                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-gray-300">Not Started</span>
                              </label>
                            </div>
                          </div>
                          <div>
                            <h3 className="text-sm font-semibold text-white mb-2">Category</h3>
                            <div className="space-y-2">
                              <label className="flex items-center space-x-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={categoryFilters['open-ended']}
                                  onChange={(e) => setCategoryFilters({...categoryFilters, 'open-ended': e.target.checked})}
                                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-gray-300">Open-Ended</span>
                              </label>
                              <label className="flex items-center space-x-2 cursor-pointer">
                                <input
                                  type="checkbox"
                                  checked={categoryFilters['replication']}
                                  onChange={(e) => setCategoryFilters({...categoryFilters, 'replication': e.target.checked})}
                                  className="rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                                />
                                <span className="text-sm text-gray-300">Replication</span>
                              </label>
                            </div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  
                  {/* Right side - Number of problems and Random button */}
                  <div className="flex items-center space-x-4">
                    {/* Number of problems */}
                    <div className="flex items-center space-x-2">
                      <div className="flex items-center space-x-1">
                        <div className="w-4 h-4 rounded-full border-2 border-gray-600 bg-gray-800 relative">
                          <div className="absolute inset-0 rounded-full bg-blue-500" style={{clipPath: 'circle(50% at 50% 50%)'}}></div>
                        </div>
                        <span className="text-sm text-gray-400">
                          {filteredTasks.length} problems
                        </span>
                      </div>
                    </div>
                    
                    {/* Random button with tooltip */}
                    <div className="relative group">
                      <button 
                        onClick={handleRandomTask}
                        className="px-3 h-[38px] rounded-lg bg-gray-800 border border-gray-600 hover:bg-gray-700 transition-colors flex items-center justify-center"
                      >
                        <Shuffle className="h-4 w-4 text-gray-400 group-hover:text-white transition-colors" />
                      </button>
                      {/* Tooltip */}
                      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2 py-1 bg-white text-black text-xs rounded opacity-0 group-hover:opacity-100 transition-opacity duration-200 whitespace-nowrap z-50 pointer-events-none border border-gray-300">
                        Surprise Me!
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Content Area */}
              <div className="flex-1 flex flex-col min-h-0">
                {/* Task List */}
                {filteredTasks.length > 0 && (
                  <div className="flex-1 overflow-visible">
                    <TaskCardGrid
                      tasks={filteredTasks}
                      onGetStarted={handleGetStarted}
                      isInternalReviewer={isInternalReviewer}
                      onViewSubmissions={isInternalReviewer ? handleViewSubmissionsFromGrid : undefined}
                      submissionGalleryCounts={submissionGalleryCounts}
                    />
                  </div>
                )}

                {/* No results or loading state */}
                {filteredTasks.length === 0 && (
                  <div className="py-12 w-full">
                    <div className="text-center">
                      {searchQuery.trim() === "" ? (
                        <div className="flex items-center justify-center space-x-3">
                          <LoadingSpinner size="lg" color="blue" />
                          <p className="text-gray-400 text-lg">Loading tasks...</p>
                        </div>
                      ) : (
                        <>
                          <p className="text-gray-400 text-lg">No tasks found matching your search.</p>
                          <button 
                            onClick={() => setSearchQuery("")}
                            className="mt-4 text-base font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 hover:bg-transparent hover:-translate-y-0.5 text-gray-400 hover:text-blue-400 after:content-[''] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200"
                          >
                            Clear search
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Coding Terminal Content - only shown when active */}
          {showCodingTerminal && (
            selectedTask && (
              <div className="flex-1 min-h-0 flex flex-col">
                {/* Left Tabs */}
                <div className="mt-0 px-0 py-1 bg-transparent">
                  <div className="flex items-center justify-between gap-2">
                    {/* Tabs (left-aligned, scrollable) */}
                    <div className="flex items-center space-x-6 overflow-x-auto whitespace-nowrap min-w-0" style={{ WebkitOverflowScrolling: 'touch' }}>
                      <button 
                        className={`text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 hover:bg-transparent hover:-translate-y-0.5 after:content-[\"\"] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 ${leftTab === 'task' ? 'text-blue-400 after:opacity-100' : 'text-gray-400 hover:text-blue-400 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200'}`}
                        onClick={() => setLeftTab('task')}
                      >
                        Task
                        <span className="ml-1 text-[10px] opacity-70 inline-flex items-center align-middle leading-none">
                          {isMac ? '⌘+[' : 'Ctrl+['}
                        </span>
                      </button>
                      {rightTab !== 'submissions' && (
                        <button 
                          className={`text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 after:content-[\"\"] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 ${
                            isTimedTaskPreStartGateActive
                              ? "text-gray-500 opacity-60 after:opacity-0 pointer-events-none"
                              : leftTab === 'preview'
                                ? 'text-blue-400 after:opacity-100 hover:bg-transparent hover:-translate-y-0.5'
                                : 'text-gray-400 hover:text-blue-400 hover:bg-transparent hover:-translate-y-0.5 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200'
                          }`}
                          onClick={() => {
                            if (isTimedTaskPreStartGateActive) return;
                            void sendCodeLog('preview-refresh', { refreshSource: 'tab-switch' });
                            setLeftTab('preview');
                          }}
                          disabled={isTimedTaskPreStartGateActive}
                          title={isTimedTaskPreStartGateActive ? "Start the timed task to enable preview." : undefined}
                        >
                          My Preview
                          <span className="ml-1 text-[10px] opacity-70 inline-flex items-center align-middle leading-none">
                            {isMac ? '⌘+]' : 'Ctrl+]'}
                          </span>
                        </button>
                      )}
                      {rightTab === 'submissions' && viewedSubmission && (
                        <button 
                          className={`text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 hover:bg-transparent hover:-translate-y-0.5 after:content-[\"\"] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 ${leftTab === 'project-details' ? 'text-blue-400 after:opacity-100' : 'text-gray-400 hover:text-blue-400 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200'}`}
                          onClick={() => setLeftTab('project-details')}
                        >
                          Project Details
                          <span className="ml-1 text-[10px] opacity-70 inline-flex items-center align-middle leading-none">
                            {isMac ? '⌘+]' : 'Ctrl+]'}
                          </span>
                        </button>
                      )}
                      {/* <button 
                        className={`text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 hover:bg-transparent hover:-translate-y-0.5 after:content-[\"\"] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 ${leftTab === 'submissions' ? 'text-blue-400 after:opacity-100' : 'text-gray-400 hover:text-blue-400 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200'}`}
                        onClick={() => setLeftTab('submissions')}
                      >
                        Submissions
                      </button> */}
                      {/* <button 
                        className={`text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 hover:bg-transparent hover:-translate-y-0.5 after:content-[\"\"] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px after:bg-blue-400 ${leftTab === 'leaderboard' ? 'text-blue-400 after:opacity-100' : 'text-gray-400 hover:text-blue-400 after:opacity-0 hover:after:opacity-100 after:transition-opacity after:duration-200'}`}
                        onClick={() => setLeftTab('leaderboard')}
                      >
                        Leaderboard
                      </button> */}
                    </div>
                    {/* Go Back / Swap Buttons (right-aligned) */}
                    <div className={`flex items-center ${isSwapped ? 'flex-row-reverse space-x-reverse' : ''} space-x-2 ml-auto`}>
                      {!(allTasks.find(t => t.id === selectedTask)?.label === 'website_requirements' && isSubmissionQuestionsPaneOpen) && (
                      <button
                        onClick={handleGoBack}
                        className="flex items-center justify-center w-6 h-6 rounded-md bg-gray-700/50 hover:bg-gray-600/50 transition-colors text-gray-300 hover:text-white"
                        onMouseEnter={e => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const vw = window.innerWidth || document.documentElement.clientWidth;
                          const vh = window.innerHeight || document.documentElement.clientHeight;
                          const margin = 8;
                          let left = rect.left + rect.width / 2;
                          left = Math.min(Math.max(left, margin), vw - margin);
                          const spaceAbove = rect.top;
                          const spaceBelow = vh - rect.bottom;
                          const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                          const top = placeAbove ? rect.top : rect.bottom;
                          setTooltipText('Back to tasks');
                          setTooltipLeft(left);
                          setTooltipTop(top);
                          setTooltipPlaceAbove(placeAbove);
                          setTooltipVisible(true);
                        }}
                        onMouseLeave={() => {
                          setTooltipVisible(false);
                        }}
                        onMouseMove={e => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const vw = window.innerWidth || document.documentElement.clientWidth;
                          const vh = window.innerHeight || document.documentElement.clientHeight;
                          const margin = 8;
                          let left = rect.left + rect.width / 2;
                          left = Math.min(Math.max(left, margin), vw - margin);
                          const spaceAbove = rect.top;
                          const spaceBelow = vh - rect.bottom;
                          const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                          const top = placeAbove ? rect.top : rect.bottom;
                          setTooltipLeft(left);
                          setTooltipTop(top);
                          setTooltipPlaceAbove(placeAbove);
                        }}
                      >
                        <ArrowLeft size={14} />
                      </button>
                      )}
                      <button
                        onClick={() => setIsSwapped(s => !s)}
                        className="flex items-center justify-center w-6 h-6 rounded-md bg-gray-700/50 hover:bg-gray-600/50 transition-colors text-gray-300 hover:text-white"
                        onMouseEnter={e => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const vw = window.innerWidth || document.documentElement.clientWidth;
                          const vh = window.innerHeight || document.documentElement.clientHeight;
                          const margin = 8;
                          let left = rect.left + rect.width / 2;
                          left = Math.min(Math.max(left, margin), vw - margin);
                          const spaceAbove = rect.top;
                          const spaceBelow = vh - rect.bottom;
                          const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                          const top = placeAbove ? rect.top : rect.bottom;
                          setTooltipText('Swap panes');
                          setTooltipLeft(left);
                          setTooltipTop(top);
                          setTooltipPlaceAbove(placeAbove);
                          setTooltipVisible(true);
                        }}
                        onMouseLeave={() => {
                          setTooltipVisible(false);
                        }}
                        onMouseMove={e => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const vw = window.innerWidth || document.documentElement.clientWidth;
                          const vh = window.innerHeight || document.documentElement.clientHeight;
                          const margin = 8;
                          let left = rect.left + rect.width / 2;
                          left = Math.min(Math.max(left, margin), vw - margin);
                          const spaceAbove = rect.top;
                          const spaceBelow = vh - rect.bottom;
                          const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                          const top = placeAbove ? rect.top : rect.bottom;
                          setTooltipLeft(left);
                          setTooltipTop(top);
                          setTooltipPlaceAbove(placeAbove);
                        }}
                      >
                        <ArrowLeftRight size={14} />
                      </button>
                    </div>
                  </div>
                </div>

                {/* Left Tab Content */}
                <div className="flex-1 min-h-0">
                  {rightTab === 'submissions' && leftTab === 'project-details' && viewedSubmission && (
                    <div className="bg-gray-800/50 backdrop-blur-sm border border-gray-700/50 hover:border-gray-600/50 transition-all duration-300 flex-1 overflow-hidden h-full flex flex-col">
                      <div className="flex-1 overflow-y-auto overflow-x-hidden p-3">
                        <iframe
                          title="Project Details"
                          srcDoc={(() => {
                          const title = viewedSubmission.title || "Untitled Submission";
                          const description = viewedSubmission.description || "No description provided.";
                          const escapeHtml = (text: string) => {
                            const div = document.createElement('div');
                            div.textContent = text;
                            return div.innerHTML;
                          };
                          const escapedTitle = escapeHtml(title);
                          const escapedDescription = escapeHtml(description).replace(/\n/g, "<br/>");
                          return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root { color-scheme: dark; }
    html, body { margin: 0; padding: 0; height: 100%; user-select: none; -webkit-user-select: none; -moz-user-select: none; -ms-user-select: none; }
    *, *::before, *::after { box-sizing: border-box; user-select: none; -webkit-user-select: none; -moz-user-select: none; -ms-user-select: none; }
    body { background: #20232a; color: #d6dde6; font: 14px/1.5 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; }
    .pd-root { max-width: 900px; margin: 0 auto; padding: 24px; }
    .field-label { color: #8ac4ff; font-weight: 600; font-size: 14px; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
    .field-value { margin-bottom: 32px; }
    .title-value { color: #e6f6ff; font-size: 2em; font-weight: 600; line-height: 1.3; }
    .description-value { color: #d6dde6; line-height: 1.6; font-size: 15px; }
    .description-value p { margin: 12px 0; }
  </style>
  <base target="_blank" />
</head>
<body>
  <div class="pd-root">
    <div class="field-label">Submitted Title</div>
    <div class="field-value description-value">${escapedTitle}</div>
    <div class="field-label">Submitted Description</div>
    <div class="field-value description-value">${escapedDescription}</div>
  </div>
  <script>
    document.addEventListener('copy', function(e) { e.preventDefault(); return false; });
    document.addEventListener('cut', function(e) { e.preventDefault(); return false; });
    document.addEventListener('paste', function(e) { e.preventDefault(); return false; });
    document.addEventListener('keydown', function(e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === 'c' || e.key === 'x' || e.key === 'a')) {
        e.preventDefault(); return false;
      }
    });
    document.addEventListener('contextmenu', function(e) { e.preventDefault(); return false; });
  </script>
</body>
</html>`;
                        })()}
                          style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
                          sandbox="allow-same-origin allow-scripts"
                        />
                      </div>
                    </div>
                  )}
                  <div style={{ display: leftTab === 'task' ? 'block' : 'none', height: '100%' }}>
                    <TaskInstructionNew
                      taskDescription={isPlaygroundMode || selectedTask === 'playground' 
                        ? (taskDescriptions[0] || '') 
                        : getTaskDescription(selectedTask)}
                      requirements={getTaskRequirements(selectedTask)}
                      taskName={
                        isPlaygroundMode || selectedTask === 'playground'
                          ? 'Playground'
                          : (allTasks.find(t => t.id === selectedTask)?.name || allTasks.find(t => t.id === selectedTask)?.title)
                      }
                      taskLabel={allTasks.find(t => t.id === selectedTask)?.label}
                      aiAssistantMode={agentMode}
                      isAiGroupUser={normalizeExperimentGroup(user?.settings?.experiment_group) === 'agent'}
                      showAIAssistantDetails={AI_ASSISTANT_DETAILS_TASKS.has(normalizeTaskNameKey(allTasks.find(t => t.id === selectedTask)?.name))}
                      example={allTasks.find(t => t.id === selectedTask)?.example}
                      showHeader={false}
                    />
                  </div>
                  {leftTab === 'preview' && (
                    <div className="h-full">
                      <PreviewTab 
                        ref={previewTabRef}
                        files={currentFiles}
                        className="h-full"
                        taskName={allTasks.find(t => t.id === selectedTask)?.name || 'preview'}
                        actualEditorRef={actualEditorRef}
                        onRefresh={handlePreviewRefresh}
                        disablePopout={isWebsiteRequirementsTaskSelected || (!!selectedTaskName && GAME_REQUIRED_TASKS.includes(selectedTaskName as any))}
                        onDebugConsoleVisibilityChange={setDebugTerminalOpen}
                      />
                    </div>
                  )}
                  {leftTab === 'submissions' && (
                    <UserSubmissions />
                  )}
                  {leftTab === 'leaderboard' && (
                    <div className="text-center text-gray-400 bg-gray-950 p-8 rounded-lg">
                      <h3 className="text-lg font-semibold mb-2">Leaderboard</h3>
                      <p>Leaderboard functionality coming soon...</p>
                    </div>
                  )}
                </div>
              </div>
            )
          )}
        </div>

        {/* Vertical Resize Handle */}
        {showCodingTerminal && selectedTask && leftTab !== 'leaderboard' && (
          <div
            onMouseDown={handleMouseDown}
            className="flex-shrink-0 cursor-col-resize group"
            style={{ width: 4, order: 1 }}
          >
            <div className={`h-full w-px bg-gray-700 group-hover:bg-gray-600 mx-auto`} />
          </div>
        )}

        {/* Right Side - Coding Editor (kept mounted; hidden on some tabs) */}
        {showCodingTerminal && selectedTask && (
          <div
            className={`bg-gray-900 h-full flex-1 min-w-0 box-border overflow-hidden px-6 pt-2 pb-6`}
            style={{ order: isSwapped ? 0 as any : 2 as any, display: leftTab === 'leaderboard' ? 'none' : undefined }}
          >
            <div className="h-full flex flex-col min-h-0">
              {/* Code Editor Card */}
              <div className="bg-transparent w-full min-w-0 flex-1 flex flex-col min-h-0">
                {/* Top bar (mirrors left tabs style) */}
                <div className="mt-0 px-0 py-1 bg-transparent">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center space-x-6 overflow-x-auto whitespace-nowrap min-w-0" style={{ WebkitOverflowScrolling: 'touch' }}>
                      <button
                        type="button"
                        className={`text-sm font-medium transition-all duration-200 relative bg-transparent hover:bg-transparent focus:bg-transparent active:bg-transparent border-none outline-none py-2 after:content-[''] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px hover:-translate-y-0.5 ${
                          rightTab === 'code'
                            ? 'text-blue-400 after:bg-blue-400 after:opacity-100 cursor-default'
                            : 'text-gray-400 hover:text-blue-400 after:bg-blue-400 after:opacity-0 hover:after:opacity-100'
                        } inline-flex`}
                        style={{ position: 'relative', display: 'inline-flex' }}
                        onClick={() => {
                          setRightTab('code');
                          setTooltipVisible(false);
                          // If on project-details, switch to task tab
                          if (leftTab === 'project-details') {
                            setLeftTab('task');
                          }
                        }}
                      >
                        Code
                        {canShowViewSubmissionsTab && (
                          <span className="ml-1 text-[10px] opacity-70 inline-flex items-center align-middle leading-none">
                            {isMac ? '⌘+(' : 'Ctrl+('}
                          </span>
                        )}
                      </button>
                      {isTimedTaskSelected && (
                        <TaskTimer
                          isTimedTaskSelected={isTimedTaskSelected}
                          hasTimedTaskStarted={hasTimedTaskStarted}
                          taskTimerDurationSeconds={taskTimerDurationSeconds}
                          initialRemainingSeconds={initialTimerRemainingSeconds}
                          isPaused={isSubmissionQuestionsPaneOpen}
                          warningCheckpoints={taskTimerWarningCheckpoints}
                          onWarning={handleTaskTimerWarning}
                          onExpired={handleTaskTimerExpired}
                          warningKeysShownRef={timerWarningKeysShownRef}
                          expiredModalShownRef={timerExpiredModalShownRef}
                        />
                      )}
                      <div
                        style={{ position: 'relative' }}
                        onWheel={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                        }}
                      >
                        {canShowViewSubmissionsTab && (isViewSubmissionsUnlocked ? (
                          <button
                            type="button"
                            className={`text-sm font-medium transition-all duration-200 relative bg-transparent hover:bg-transparent focus:bg-transparent active:bg-transparent border-none outline-none py-2 after:content-[''] after:absolute after:bottom-1 after:left-0 after:w-full after:h-px hover:-translate-y-0.5 ${
                              rightTab === 'submissions'
                                ? 'text-blue-400 after:bg-blue-400 after:opacity-100'
                                : 'text-gray-400 hover:text-blue-400 after:bg-blue-400 after:opacity-0 hover:after:opacity-100'
                            } inline-flex items-center gap-1`}
                            style={{ position: 'relative', display: 'inline-flex' }}
                            onClick={() => {
                              setRightTab('submissions');
                              setTooltipVisible(false);
                            }}
                            onWheel={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                            }}
                          >
                            View Submissions
                            <span className="ml-1 text-[10px] opacity-70 inline-flex items-center align-middle leading-none">
                              {isMac ? '⌘+)' : 'Ctrl+)'}
                            </span>
                          </button>
                        ) : (
                          <span
                            className="text-sm font-medium transition-all duration-200 relative bg-transparent border-none outline-none py-2 text-gray-400 opacity-60 cursor-not-allowed flex items-center gap-1 hover:tooltip-parent"
                            style={{ position: 'relative', display: 'inline-flex' }}
                            onMouseEnter={e => {
                              const rect = e.currentTarget.getBoundingClientRect();
                              const vw = window.innerWidth || document.documentElement.clientWidth;
                              const vh = window.innerHeight || document.documentElement.clientHeight;
                              const margin = 8;
                              let left = rect.left + rect.width / 2;
                              left = Math.min(Math.max(left, margin), vw - margin);
                              const spaceAbove = rect.top;
                              const spaceBelow = vh - rect.bottom;
                              const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                              const top = placeAbove ? rect.top : rect.bottom;
                              setTooltipText(viewSubmissionsTooltip);
                              setTooltipLeft(left);
                              setTooltipTop(top);
                              setTooltipPlaceAbove(placeAbove);
                              setTooltipVisible(true);
                            }}
                            onMouseLeave={() => {
                              setTooltipVisible(false);
                            }}
                            onMouseMove={e => {
                              const rect = e.currentTarget.getBoundingClientRect();
                              const vw = window.innerWidth || document.documentElement.clientWidth;
                              const vh = window.innerHeight || document.documentElement.clientHeight;
                              const margin = 8;
                              let left = rect.left + rect.width / 2;
                              left = Math.min(Math.max(left, margin), vw - margin);
                              const spaceAbove = rect.top;
                              const spaceBelow = vh - rect.bottom;
                              const placeAbove = spaceAbove >= 40 || spaceAbove > spaceBelow;
                              const top = placeAbove ? rect.top : rect.bottom;
                              setTooltipLeft(left);
                              setTooltipTop(top);
                              setTooltipPlaceAbove(placeAbove);
                            }}
                            onWheel={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                            }}
                          >
                            <span className="flex items-center gap-1">
                              🔒 View Submissions
                              <span className="ml-1 text-[10px] opacity-60 inline-flex items-center align-middle leading-none">
                                {isMac ? '⌘+)' : 'Ctrl+)'}
                              </span>
                            </span>
                          </span>
                        ))}
                      </div>
                    </div>
                    {rightTab === 'code' &&
                      !isPlaygroundMode &&
                      selectedTask !== 'playground' && (
                    <div className="flex items-center space-x-2 ml-auto">
                      {allTasks.find(t => t.id === selectedTask)?.label !== 'website_requirements' && (
                      <button
                          className="px-2.5 py-1.5 rounded-md transition-colors text-xs bg-gray-700 hover:bg-gray-600 text-white cursor-pointer border border-gray-600"
                        onClick={handleDownloadProject}
                        title="Download project as repository"
                      >
                        <Download className="w-3.5 h-3.5 inline-block mr-1" />
                        Download Project
                      </button>
                      )}
                      <button
                          className={`px-2.5 py-1.5 rounded-md transition-colors text-xs text-white ${
                            isTimedTaskPreStartGateActive
                              ? "bg-blue-900/60 cursor-not-allowed opacity-60"
                              : "bg-blue-600 hover:bg-blue-700 cursor-pointer"
                          }`}
                        onClick={() => {
                          if (isTimedTaskPreStartGateActive) return;
                          handleOpenSubmitModal();
                        }}
                        disabled={isTimedTaskPreStartGateActive}
                        title={isTimedTaskPreStartGateActive ? "Start the timed task before submitting." : undefined}
                      >
                        Submit Project
                      </button>
                    </div>
                    )}
                    {rightTab === 'code' && isPlaygroundMode && selectedTask === 'playground' && (
                    <div className="flex items-center space-x-2 ml-auto">
                      <button
                          className="px-2.5 py-1.5 rounded-md transition-colors text-xs bg-blue-600 hover:bg-blue-700 text-white cursor-pointer"
                        onClick={handleOpenSubmitModal}
                      >
                        Submit / Finish Tutorial
                      </button>
                    </div>
                    )}
                  </div>
                </div>
                {/* Editor */}
                <div className="relative flex-1 min-w-0 min-h-0">
                  <div className="h-full min-w-0 flex flex-col min-h-0">
                    <div className="flex-1 min-h-0">
                      <div style={{ display: rightTab === 'code' ? 'flex' : 'none', flexDirection: 'column', height: '100%', width: '100%' }}>
                        <CodingEditor
                      onEditorMount={handleEditorMount}
                      contextLength={4000}
                      wait_time_for_sug={2000}
                      setSuggestionIdx={setSuggestionIdx}
                      setTelemetry={setTelemetry}
                      modelAutocomplete={modelAutocomplete}
                      taskIndex={taskIndex}
                      setLogprobsCompletion={setLogProbs}
                      logProbs={logProbs}
                      suggestionIdx={suggestionIdx}
                      messageAIIndex={messageAIIndex}
                      setIsSpinning={setIsSpinning}
                      proactive_refresh_time_inactive={5000}
                      chatRef={chatRef}
                      actualEditorRef={actualEditorRef}
                      setTaskDescriptions={setTaskDescriptions}
                      setFunctionSignatures={setFunctionSignatures}
                      setUnitTests={setUnitTests}
                      setExpCondition={setExpCondition}
                      setModel={setModel}
                      setMaxTokensTask={setMaxTokensTask}
                      editor={editorRef.current}
                      unit_tests={unitTests}
                      setMessages={setMessages}
                      exp_condition={expCondition}
                      response_id={responseId}
                      worker_id={workerId}
                      setTaskIndex={setTaskIndex}
                      function_signatures={functionSignatures}
                      task_id={taskId}
                      telemetry={telemetry}
                      skipTime={0}
                      editorHeight={editorHeight}
                      onEditorMouseDown={handleEditorMouseDown}
                      code={code}
                      setCode={setCode}
                      // Enable multi-file UI with dummy files in read-only navigation mode
                      enableMultiFile={true}
                      initialFiles={initialFiles}
                      readOnlyFiles={false}
                      testCases={testCases}
                      projectId={currentTaskMeta?.projectId ?? null}
                      userId={numericUserId}
                      taskName={currentTaskMeta?.name ?? null}
                      taskLabel={allTasks.find((t: any) => t.id === selectedTask)?.label}
                      assistantPromptCountRef={assistantPromptCountRef}
                      taskRequirements={getTaskRequirements(selectedTask)}
                      aiAssistantMode={agentMode}
                      sidebarOpen={sidebarOpen}
                      onProjectSubmitted={handleProjectSubmitted}
                      onQuestionsGenerationStarted={handleQuestionsGenerationStarted}
                      onQuestionsGenerationCompleted={handleQuestionsGenerationCompleted}
                      onContinuedToQuestions={handleContinuedToQuestions}
                      onProjectInfoChange={(title, description) => {
                        setCustomProjectTitle(title);
                        setCustomProjectDescription(description);
                      }}
                      onSubmissionQuestionsVisibilityChange={handleSubmissionQuestionsPaneVisibilityChange}
                      lockSubmitModalExit={isSubmitModalExitLocked}
                      // Pane visibility
                      showCodeEditor={showCodeEditor}
                      showTerminal={false}
                      onHideCodeEditor={() => setShowCodeEditor(false)}
                      onHideTerminal={() => setShowTerminal(false)}
                      onShowCodeEditor={() => setShowCodeEditor(true)}
                      onShowTerminal={() => setShowTerminal(true)}
                      assistantPlacement={assistantPlacement}
                      showAIAssistantForBottom={showAIAssistant}
                      isAIAssistantVisible={showAIAssistant}
                      renderAssistantPane={renderAssistantPane}
                      // Save shortcut callback for preview updates
                      onSaveShortcut={handleSaveShortcut}
                      // File content change callback for real-time preview updates
                      onFileContentChange={handleFileContentChange}
                      // Agent changes for diff view
                      pendingAgentChanges={pendingAgentChanges}
                      onAcceptAgentChanges={(fileId?: string, content?: string, action?: 'keep' | 'reject') => {
                        // Changes are already applied in real-time, just clear pending state
                        const prevPending = pendingAgentChanges;
                        // Use explicit action from editor when provided; otherwise infer from content match (for backwards compatibility)
                        const isKeepAction = action === 'keep' || (fileId && content && prevPending?.modified?.[fileId] &&
                                           String(content).trim() === String(prevPending.modified[fileId]).trim());
                        const isRejectAction = action === 'reject' || (fileId && content && prevPending?.original?.[fileId] &&
                                             String(content).trim() === String(prevPending.original[fileId]).trim());
                        
                        if (fileId && content) {
                          // Remove this file type from pending changes
                          setPendingAgentChanges((prev: any) => {
                            if (!prev) return null;
                            const newModified = { ...(prev.modified || {}) };
                            const newOriginal = { ...(prev.original || {}) };
                            delete newModified[fileId];
                            delete newOriginal[fileId];
                            // If no more files, clear everything
                            if (Object.keys(newModified).length === 0) {
                              // Don't save snapshot here - it's already saved when agent completes
                              return null;
                            }
                            return { ...prev, modified: newModified, original: newOriginal };
                          });
                          
                          // Always log keep/reject when we know the user's intent (explicit action or inferred from content), so we log even after user edits
                          if (isKeepAction) {
                            setTimeout(() => {
                              void sendCodeLog('keep', { fileId });
                              try {
                                previewTabRef.current?.refreshPreview();
                              } catch (error) {
                                console.warn('Failed to refresh preview on keep:', error);
                              }
                            }, 100);
                          } else if (isRejectAction) {
                            setTimeout(() => {
                              void sendCodeLog('reject', { fileId });
                              try {
                                previewTabRef.current?.refreshPreview();
                              } catch (error) {
                                console.warn('Failed to refresh preview on reject:', error);
                              }
                            }, 100);
                          }
                        } else {
                          // Accept all remaining changes (fallback - shouldn't be used in new workflow)
                          setPendingAgentChanges(null);
                          // Don't save snapshot here - it's already saved when agent completes
                        }

                        // No explicit preview refresh; preview will reflect latest editor state
                      }}
                      onRejectAgentChanges={(actionType?: 'keep_all' | 'reject_all') => {
                        // Determine action type from parameter or fallback to checking pendingAgentChanges
                        const hasModifiedFiles = pendingAgentChanges?.modified && 
                                                Object.keys(pendingAgentChanges.modified).length > 0;
                        const action: 'keep_all' | 'reject_all' = actionType || 
                                                                 (hasModifiedFiles ? 'keep_all' : 'reject_all');
                        
                        setPendingAgentChanges(null);
                        
                        // Don't save snapshot here - it's already saved when agent completes
                        
                        // Log code with appropriate mode and refresh preview (after state is cleared so we capture final code state)
                        // refreshPreview calls onRefresh with 'external', which handlePreviewRefresh skips logging for
                        setTimeout(() => {
                          void sendCodeLog(action, {});
                          try {
                            previewTabRef.current?.refreshPreview();
                          } catch (error) {
                            console.warn(`Failed to refresh preview on ${action}:`, error);
                          }
                        }, 100);
                      }}
                      isLoadingFiles={isLoadingFiles}
                    />
                      </div>
                      <div style={{ display: rightTab === 'submissions' ? 'flex' : 'none', flexDirection: 'column', height: '100%', width: '100%' }}>
                        <SubmissionsGallery 
                          projectId={currentTaskMeta?.projectId}
                          taskId={selectedTask}
                        />
                      </div>
                    </div>
                  </div>
                  {isTimedTaskPreStartGateActive && rightTab === 'code' && (
                    <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/95 px-6">
                      <div className="w-full max-w-xl rounded-xl border border-blue-400/50 bg-slate-900 p-6 text-slate-100 shadow-2xl">
                        <h3 className="text-xl font-bold text-blue-200">Timed task ready to begin</h3>
                        <p className="mt-3 text-sm leading-6 text-slate-200">
                          Read the instructions in the Task tab before starting. You will have <strong>{timedTaskLimitMinutes} minutes</strong> once you begin.
                        </p>
                        <p className="mt-2 text-sm leading-6 text-slate-200">
                          The editor and website preview will stay locked until you click 'Start'. Afterwards, this pane will disappear and your timer and the task will begin.
                        </p>
                        <button
                          type="button"
                          className="mt-6 inline-flex items-center justify-center rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-blue-500 disabled:cursor-not-allowed disabled:bg-blue-900/70 disabled:text-slate-300"
                          onClick={() => {
                            void handleStartTimedTask();
                          }}
                          disabled={isStartingTimedTask}
                        >
                          {isStartingTimedTask ? "Starting..." : "Start task"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {timerAlert && (
        <div
          role="alert"
          className="fixed top-6 left-1/2 -translate-x-1/2 z-[10020] w-[min(92vw,44rem)] rounded-xl border px-4 py-3 shadow-2xl"
          style={{
            backgroundColor: timerAlert.tone === "critical" ? "#7f1d1d" : "#78350f",
            borderColor: timerAlert.tone === "critical" ? "#f87171" : "#fbbf24",
            color: "#f8fafc",
          }}
        >
          <div className="flex items-start gap-3">
            <span className="text-base leading-none mt-0.5" aria-hidden>
              {timerAlert.tone === "critical" ? "⏰" : "⚠️"}
            </span>
            <p className="text-sm font-semibold leading-5">{timerAlert.message}</p>
            {timerAlert.dismissible && (
              <button
                type="button"
                onClick={() => setTimerAlert(null)}
                className="ml-auto text-slate-100/80 hover:text-white text-sm bg-transparent hover:bg-transparent focus:bg-transparent active:bg-transparent"
                aria-label="Dismiss timer alert"
              >
                <X size={16} />
              </button>
            )}
          </div>
        </div>
      )}

      {showTimerExpiredModal &&
        typeof document !== 'undefined' &&
        createPortal(
          <div
            className="fixed inset-0 z-[10030] flex items-center justify-center px-4"
            style={{ backgroundColor: "rgba(2, 6, 23, 0.75)" }}
          >
            <div className="w-full max-w-lg rounded-xl border border-red-400/60 bg-slate-900 p-6 text-slate-100 shadow-2xl">
              <h2 className="text-xl font-bold text-red-300">Time&apos;s up!</h2>
              <p className="mt-3 text-sm text-slate-200">
                You will now proceed to the post-submission questions.
              </p>
              <div className="mt-6 flex justify-end">
                <button
                  type="button"
                  className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-500"
                  onClick={() => {
                    setShowTimerExpiredModal(false);
                    handleOpenSubmitQuestionsDirect();
                  }}
                >
                  Continue
                </button>
              </div>
            </div>
          </div>,
          document.body
        )}

      {/* Transparent overlay for mouse tracking during drag */}
      {(isResizing || isVerticalResizing || isEditorResizing) && (
        <div 
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'transparent',
            zIndex: 9999,
            cursor: isResizing ? 'col-resize' : 'row-resize',
            pointerEvents: 'all'
          }}
        />
      )}
      {tooltipVisible && typeof document !== 'undefined' && createPortal(
        <div
          role="tooltip"
          style={{
            position: 'fixed',
            left: tooltipLeft,
            top: tooltipTop,
            transform: tooltipPlaceAbove ? 'translate(-50%, -100%) translateY(-8px)' : 'translate(-50%, 8px)',
            backgroundColor: '#ffffff',
            color: '#000000',
            fontSize: '12px',
            padding: '4px 8px',
            borderRadius: '6px',
            border: '1px solid #d1d5db',
            boxShadow: '0 10px 15px -3px rgba(0,0,0,0.1), 0 4px 6px -2px rgba(0,0,0,0.05)',
            zIndex: 100000,
            whiteSpace: 'nowrap',
            pointerEvents: 'none'
          }}
        >
          {tooltipText}
        </div>,
        document.body
      )}
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <LoadingSpinner size="xl" color="white" />
      </div>
    }>
      <HomeInner />
    </Suspense>
  );
}