"use client";

// Disable static prerender to avoid CSR bailout issues
export const dynamic = 'force-dynamic';
import { useState, useEffect, useRef, useCallback, useMemo, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useRouteProtection, useAuth } from "../utils/auth";
import { isPlaygroundCompletedFromSettings, isWebsiteRequirementsPhaseSkippedForStudy } from "../utils/userSettings";
import {
  Shuffle,
  Search,
  Filter,
} from "lucide-react";
import { useSidebar } from "../components/AppLayout";
import TaskCardGrid from "../components/TaskCardGrid";
import LoadingSpinner from "../components/LoadingSpinner";
import {
  GAME_REQUIRED_TASKS,
  getRequiredTasksForMode,
  getStudyTaskMode,
  isWebsiteRequirementTask,
  TIMED_TASKS,
  WEBSITE_TUTORIAL_TASKS,
} from "../config/tasks";
import { ENV } from "../config/env";
import { isInternalReviewerUser } from "../config/internalReviewers";
import { isMultiSubmissionUser } from "../config/multiSubmissionAccounts";
import { useSubmissionGalleryCounts } from "../hooks/useSubmissionGalleryCounts";
import { useSnackbar } from "../components/SnackbarProvider";
import { PASSWORD_HASH, hashString } from "../utils/password";

function BrowseInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  
  // Use route protection hook
  const { isAuthenticated, isLoading } = useRouteProtection();
  const { user } = useAuth();
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
  
  // All hooks must be called before any conditional returns
  const [searchQuery, setSearchQuery] = useState("");
  const [filteredTasks, setFilteredTasks] = useState<any[]>([]);
  const submissionGalleryCounts = useSubmissionGalleryCounts(isInternalReviewer, filteredTasks);
  const { isSidebarOpen: sidebarOpen } = useSidebar();
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
  
  // Generate background circle data once on mount
  const backgroundStars = useMemo(() => {
    const colors = ['#3b82f6', '#8b5cf6', '#ec4899'];
    const animatedColors = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981'];
    
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
    
    // Animated dots moving across screen
    const animatedDots = Array.from({ length: 12 }, () => ({
      color: animatedColors[Math.floor(Math.random() * animatedColors.length)],
      size: Math.random() * 8 + 4,
      top: Math.random() * 100,
      duration: Math.random() * 30 + 40,
      delay: Math.random() * 5,
      direction: (Math.random() > 0.5 ? 'left-to-right' : 'right-to-left') as 'left-to-right' | 'right-to-left',
      opacity: Math.random() * 0.6 + 0.4,
    }));
    
    return { largeStars, mediumStars, smallDots, animatedDots };
  }, []);
  
  // Clear snackbars when leaving the Browse page
  const { clearAllSnackbars } = useSnackbar();
  useEffect(() => {
    return () => {
      clearAllSnackbars();
    };
  }, [clearAllSnackbars]);

  const [allTasks, setAllTasks] = useState<any[]>([]);
  const [isLoadingTasks, setIsLoadingTasks] = useState(true);
  const [allRequiredTasksCompleted, setAllRequiredTasksCompleted] = useState(false);
  const [lockedTaskIds, setLockedTaskIds] = useState<Set<string>>(new Set());
  const [noEditLockedTaskIds, setNoEditLockedTaskIds] = useState<Set<string>>(new Set());
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [isPlaygroundNotCompleted, setIsPlaygroundNotCompleted] = useState(false);
  const [timedTaskModalState, setTimedTaskModalState] = useState<{
    taskId: string;
    taskTitle: string;
    minutes: number | null;
  } | null>(null);
  const filterModalRef = useRef<HTMLDivElement | null>(null);
  const websiteRequirementsSkipped =
    isWebsiteRequirementsPhaseSkippedForStudy(user?.settings) || allowsMultipleSubmissions;
  const studyTaskMode = useMemo(() => {
    const otherTasks = allTasks.filter((task: any) => task.id !== 'playground');
    return getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
  }, [allTasks, websiteRequirementsSkipped]);
  const requiredGameTaskNames = useMemo(() => {
    if (studyEnded || allowsMultipleSubmissions) {
      return new Set<string>();
    }

    const otherTasks = allTasks.filter((task: any) => task.id !== 'playground');
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
    if (mode === 'website-requirements') {
      return new Set<string>();
    }

    return new Set(getRequiredTasksForMode(mode, otherTasks));
  }, [allTasks, studyEnded, websiteRequirementsSkipped, allowsMultipleSubmissions]);
  const timedRequiredTaskNames = useMemo(() => {
    if (studyEnded || allowsMultipleSubmissions) {
      return new Set<string>();
    }

    const otherTasks = allTasks.filter((task: any) => task.id !== 'playground');
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
    const requiredTaskNames = new Set(getRequiredTasksForMode(mode, otherTasks));

    return new Set(
      TIMED_TASKS.filter((taskName) => requiredTaskNames.has(taskName))
    );
  }, [allTasks, studyEnded, websiteRequirementsSkipped, allowsMultipleSubmissions]);
  const requiredTaskNamesForCurrentMode = useMemo(() => {
    if (studyEnded || allowsMultipleSubmissions) {
      return new Set<string>();
    }

    const otherTasks = allTasks.filter((task: any) => task.id !== 'playground');
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
    return new Set(getRequiredTasksForMode(mode, otherTasks));
  }, [allTasks, studyEnded, websiteRequirementsSkipped, allowsMultipleSubmissions]);
  const timedTaskLimitMinutesByName = useMemo<Record<string, number>>(
    () => ({
      zic_zac_zoe: Math.max(1, ENV.RECREATION_TASK_ONE_MINUTES),
      zic_zac_zoe_follow_up: Math.max(1, ENV.RECREATION_TASK_TWO_MINUTES),
      platformer: Math.max(1, ENV.GAME_TASK_ONE_MINUTES),
    }),
    []
  );
  const timedTaskNamesSet = useMemo(() => new Set<string>(TIMED_TASKS), []);
  const tutorialTaskNames = useMemo(() => {
    const tutorialNames = new Set<string>(WEBSITE_TUTORIAL_TASKS);
    allTasks.forEach((task: any) => {
      const taskName = (task?.name || '') as string;
      if (/warm[_-]?up/i.test(taskName)) {
        tutorialNames.add(taskName);
      }
    });
    return tutorialNames;
  }, [allTasks]);

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
      category: 'tutorial',
    };

    return [playgroundTask, ...tasks];
  }, [user?.settings, websiteRequirementsSkipped, allowsMultipleSubmissions]);
  
  // Load tasks
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
        }
      } catch (error) {
      }
    }
    
    // Fetch from API
    try {
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
          }
        }
        
        setAllTasks(buildTaskListForCurrentMode(tasks));
      } else {
        console.error('Failed to load tasks:', res.status, res.statusText);
        setAllTasks([]);
        setFilteredTasks([]);
      }
    } catch (error: any) {
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

  // Initial load of tasks
  useEffect(() => {
    const abortController = new AbortController();
    loadTasks(abortController.signal);
    
    return () => {
      abortController.abort();
    };
  }, [loadTasks]);

  // Helper function to create a seeded random number generator
  const createSeededRandom = useCallback((seed: string) => {
    // Convert seed string to a number
    let seedValue = 0;
    for (let i = 0; i < seed.length; i++) {
      const char = seed.charCodeAt(i);
      seedValue = ((seedValue << 5) - seedValue) + char;
      seedValue = seedValue & seedValue; // Convert to 32-bit integer
    }
    
    // Seeded PRNG (Linear Congruential Generator)
    let state = Math.abs(seedValue) || 1;
    return () => {
      state = (state * 9301 + 49297) % 233280;
      return state / 233280;
    };
  }, []);

  // Helper function to shuffle an array (Fisher-Yates shuffle) with user-specific seed
  const shuffleArray = useCallback(<T,>(array: T[], seed: string): T[] => {
    if (array.length <= 1) return array;
    
    const shuffled = [...array];
    const random = createSeededRandom(seed);
    
    for (let i = shuffled.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  }, [createSeededRandom]);

  const normalizeTaskLabel = useCallback((task: any) => {
    return (task.label || 'open-ended').toLowerCase().replace(/_/g, '-');
  }, []);

  const isGameOpenEndedTask = useCallback((task: any) => {
    return normalizeTaskLabel(task) === 'open-ended';
  }, [normalizeTaskLabel]);

  // Helper function to filter tasks by required status
  const filterTasksByRequiredStatus = useCallback((tasks: any[]): any[] => {
    const playgroundTask = tasks.find((task: any) => task.id === 'playground');
    const otherTasks = tasks.filter((task: any) => task.id !== 'playground');
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);

    if (studyEnded) {
      if (mode === 'website-requirements') {
        return otherTasks;
      }

      const orderedGameTasks = otherTasks
        .filter((task: any) => isGameOpenEndedTask(task))
        .sort((a: any, b: any) => {
        const aIsPlatformer = (a.name || '').toLowerCase() === 'platformer';
        const bIsPlatformer = (b.name || '').toLowerCase() === 'platformer';
        if (aIsPlatformer !== bIsPlatformer) {
          return aIsPlatformer ? -1 : 1;
        }

        return (a.name || '').localeCompare(b.name || '', undefined, {
          sensitivity: 'base',
          numeric: true,
        });
      });

      return playgroundTask ? [playgroundTask, ...orderedGameTasks] : orderedGameTasks;
    }

    const requiredTaskNames = getRequiredTasksForMode(mode, otherTasks);

    if (mode === 'website-requirements') {
      const requiredTaskNamesSet = new Set(requiredTaskNames);
      const filteredOtherTasks = otherTasks.filter((task: any) => requiredTaskNamesSet.has(task.name));
      const orderedRequiredTasks = [...filteredOtherTasks].sort((a: any, b: any) => {
        const aIndex = requiredTaskNames.indexOf(a.name);
        const bIndex = requiredTaskNames.indexOf(b.name);
        return aIndex - bIndex;
      });

      return orderedRequiredTasks;
    }

    const gameModeTasks = otherTasks.filter((task: any) => {
      return !isWebsiteRequirementTask(task) && isGameOpenEndedTask(task);
    });
    const orderedTasks = [...gameModeTasks].sort((a: any, b: any) => {
      const aIsPlatformer = (a.name || '').toLowerCase() === 'platformer';
      const bIsPlatformer = (b.name || '').toLowerCase() === 'platformer';
      if (aIsPlatformer !== bIsPlatformer) {
        return aIsPlatformer ? -1 : 1;
      }

      return (a.name || '').localeCompare(b.name || '', undefined, {
        sensitivity: 'base',
        numeric: true,
      });
    });

    return playgroundTask ? [playgroundTask, ...orderedTasks] : orderedTasks;
  }, [isGameOpenEndedTask, studyEnded, websiteRequirementsSkipped]);

  // Filter tasks based on required status, filters, and search query
  useEffect(() => {
    const playgroundCompleted = isPlaygroundCompletedFromSettings(user?.settings);
    const tasksWithUpdatedPlayground = allTasks.map((task: any) => {
      if (task.id === 'playground') {
        return { ...task, status: playgroundCompleted ? 'completed' : 'not-started' };
      }
      return task;
    });
    
    // Check if all required tasks are completed
    const otherTasks = tasksWithUpdatedPlayground.filter((task: any) => task.id !== 'playground');
    const completedTaskNames = new Set(
      otherTasks
        .filter((task: any) => task.status === 'completed')
        .map((task: any) => task.name)
    );
    const mode = getStudyTaskMode(otherTasks, websiteRequirementsSkipped);
    const requiredTaskNames = getRequiredTasksForMode(mode, otherTasks);
    const allRequiredCompleted = requiredTaskNames.every((taskName) => completedTaskNames.has(taskName));
    const effectiveRequiredCompleted = studyEnded || allowsMultipleSubmissions ? true : allRequiredCompleted;
    const isWebsiteRequirementsMode = mode === 'website-requirements';
    setAllRequiredTasksCompleted(effectiveRequiredCompleted);
    
    let tasksAfterRequiredFilter: any[];
    if (hasSecretPassword || isInternalReviewer) {
      const seenIds = new Set<string>();
      tasksAfterRequiredFilter = tasksWithUpdatedPlayground.filter((task: any) => {
        if (seenIds.has(task.id)) {
          return false;
        }
        if (task.id !== 'playground' && (task.category === 'tutorial' || task.tags?.includes('tutorial'))) {
          return false;
        }
        seenIds.add(task.id);
        return true;
      });

      // Internal reviewers see every non-tutorial task; others in game mode only see open-ended tasks here.
      if (mode !== 'website-requirements' && !isInternalReviewer) {
        tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
          if (task.id === 'playground') {
            return true;
          }
          return !isWebsiteRequirementTask(task) && isGameOpenEndedTask(task);
        });
      }
    } else {
      // Create user-specific seed (use username if available, otherwise user ID, fallback to 'default')
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

    if (isWebsiteRequirementsMode) {
      tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => task.id !== 'playground');
    }
    
    // Filter by status
    tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
      if (task.id === 'playground') {
        return true;
      }
      const taskStatus = task.status || 'not-started';
      return statusFilters[taskStatus as keyof typeof statusFilters];
    });
    
    // Filter by category
    tasksAfterRequiredFilter = tasksAfterRequiredFilter.filter((task: any) => {
      if (task.id === 'playground') {
        return true;
      }
      const rawTaskLabel = normalizeTaskLabel(task);
      const taskLabel = rawTaskLabel === 'website-requirements' ? 'replication' : rawTaskLabel;
      return categoryFilters[taskLabel as keyof typeof categoryFilters];
    });
    
    // Filter by search query
    let finalFilteredTasks: any[];
    if (searchQuery.trim() === "") {
      finalFilteredTasks = tasksAfterRequiredFilter;
    } else {
      finalFilteredTasks = tasksAfterRequiredFilter.filter((task: any) =>
        task.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        task.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (task.tags && task.tags.some((tag: string) => tag.toLowerCase().includes(searchQuery.toLowerCase())))
      );
    }
    
    // Calculate locked tasks and active task.
    // In website requirements mode, completed tasks remain locked.
    const completedRequiredGameTaskIds = new Set(
      finalFilteredTasks
        .filter((task: any) =>
          task.id !== 'playground' &&
          task.status === 'completed' &&
          GAME_REQUIRED_TASKS.includes(task.name as any)
        )
        .map((task: any) => task.id as string)
    );
    const shouldEnableLocking =
      !hasSecretPassword &&
      !isInternalReviewer &&
      !allowsMultipleSubmissions &&
      (isWebsiteRequirementsMode || !effectiveRequiredCompleted);
    if (shouldEnableLocking) {
      const lockedIds = new Set<string>();
      const noEditIds = new Set<string>();
      let activeId: string | null = null;
      
      // Check if playground is completed (game mode only)
      const playgroundTask = finalFilteredTasks.find((task: any) => task.id === 'playground');
      const hasPlaygroundTask = !!playgroundTask;
      const playgroundCompleted = playgroundTask?.status === 'completed';
      setIsPlaygroundNotCompleted(hasPlaygroundTask ? !playgroundCompleted : false);
      
      // Find the first uncompleted task (excluding playground)
      for (const task of finalFilteredTasks) {
        if (task.id === 'playground') continue; // Playground is always unlocked
        
        // In game mode, lock all non-playground tasks until playground is completed.
        if (!isWebsiteRequirementsMode && hasPlaygroundTask && !playgroundCompleted) {
          lockedIds.add(task.id);
          continue;
        }
        
        const isCompleted = task.status === 'completed';
        const isCompletedRequiredGameTask =
          !isWebsiteRequirementsMode &&
          isCompleted &&
          GAME_REQUIRED_TASKS.includes(task.name as any);
        if (isCompletedRequiredGameTask && !allowsMultipleSubmissions) {
          lockedIds.add(task.id);
          noEditIds.add(task.id);
          continue;
        }
        
        if (isWebsiteRequirementsMode && isCompleted && !allowsMultipleSubmissions) {
          // For website requirements tasks, completed tasks stay locked.
          lockedIds.add(task.id);
        } else if (!isCompleted && activeId === null) {
          // This is the active task (first uncompleted)
          activeId = task.id;
        } else if (!isCompleted && activeId !== null) {
          // This task comes after the active task, so it's locked
          lockedIds.add(task.id);
        }
      }
      
      setLockedTaskIds(lockedIds);
      setNoEditLockedTaskIds(noEditIds);
      setActiveTaskId(activeId);
    } else if (
      !hasSecretPassword &&
      !isInternalReviewer &&
      !allowsMultipleSubmissions &&
      !isWebsiteRequirementsMode &&
      completedRequiredGameTaskIds.size > 0
    ) {
      // After game requirement completion, unlock everything except completed required game tasks.
      setLockedTaskIds(new Set(completedRequiredGameTaskIds));
      setNoEditLockedTaskIds(new Set(completedRequiredGameTaskIds));
      setActiveTaskId(null);
      setIsPlaygroundNotCompleted(false);
    } else {
      // When all tasks are unlocked, clear locks
      setLockedTaskIds(new Set());
      setNoEditLockedTaskIds(new Set());
      setActiveTaskId(null);
      setIsPlaygroundNotCompleted(false);
    }
    
    setFilteredTasks(finalFilteredTasks);
  }, [
    searchQuery,
    allTasks,
    filterTasksByRequiredStatus,
    statusFilters,
    categoryFilters,
    hasSecretPassword,
    isInternalReviewer,
    allowsMultipleSubmissions,
    user,
    numericUserId,
    studyEnded,
    isGameOpenEndedTask,
    normalizeTaskLabel,
    websiteRequirementsSkipped,
  ]);

  // Close filter modal when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (
        filterModalRef.current && 
        !filterModalRef.current.contains(target) &&
        !target.closest('button')?.querySelector('svg[class*="lucide-filter"]')
      ) {
        setShowFilterModal(false);
      }
    };

    if (showFilterModal) {
      const timeoutId = setTimeout(() => {
        document.addEventListener('mousedown', handleClickOutside);
      }, 100);

      return () => {
        clearTimeout(timeoutId);
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [showFilterModal]);

  const handleRandomTask = async () => {
    const tasksWithoutPlayground = filteredTasks.filter(task => task.id !== 'playground');
    if (tasksWithoutPlayground.length === 0) return;
    const randomIndex = Math.floor(Math.random() * tasksWithoutPlayground.length);
    const randomTask = tasksWithoutPlayground[randomIndex];
    handleGetStarted(randomTask.id);
  };

  const handleViewSubmissions = (taskId: string) => {
    router.push(`/vibe?task=${taskId}&view=submissions`);
  };

  const handleGetStarted = (taskId: string) => {
    const selectedTask =
      filteredTasks.find((task: any) => task.id === taskId) ||
      allTasks.find((task: any) => task.id === taskId);
    const selectedTaskName = selectedTask?.name;

    if (selectedTaskName && timedTaskNamesSet.has(selectedTaskName) && !isInternalReviewer && !allowsMultipleSubmissions) {
      setTimedTaskModalState({
        taskId,
        taskTitle: selectedTask?.title || selectedTaskName,
        minutes: timedTaskLimitMinutesByName[selectedTaskName] ?? null,
      });
      return;
    }

    router.push(`/vibe?task=${taskId}`);
  };

  const handleConfirmTimedTaskStart = () => {
    if (!timedTaskModalState) return;
    router.push(`/vibe?task=${timedTaskModalState.taskId}`);
    setTimedTaskModalState(null);
  };

  // Show loading state while checking authentication or password
  if (isLoading || (!isAuthenticated && !passwordCheckComplete)) {
    return (
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <div className="text-center">
          <LoadingSpinner size="xl" color="white" className="mx-auto mb-4" />
          <p className="text-gray-400">Loading...</p>
        </div>
      </div>
    );
  }

  // Don't render anything if not authenticated and no valid password
  // Allow rendering if hasSecretPassword is true (password bypass)
  if (!isAuthenticated && !hasSecretPassword) {
    return null;
  }

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
        
        {/* Animated jam-like dots moving across screen */}
        {backgroundStars.animatedDots.map((dot, i) => (
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

      {/* Main Content */}
      <div className={`${sidebarOpen ? 'ml-60' : 'ml-12'} flex h-screen overflow-x-hidden relative z-10`}>
        {/* Left Side */}
        <div className="flex flex-col box-border h-full max-w-full w-full">
          {/* Header, Search Bar, and Content Container */}
          <div className="px-40 pt-16 pb-6 w-full max-w-full flex flex-col h-full">
            {/* Greeting */}
            <h1 className="text-4xl font-light mb-2 text-center">
              Hi <span className="font-medium">{user?.username}</span>,
            </h1>
            
            {/* Header */}
            <div className="text-center mb-16 w-full">
              <h1 className="text-4xl font-light mb-2 text-center">
                {studyTaskMode === 'website-requirements' ? (
                  <>
                    Work on the website creation tasks below with{" "}
                    <span className="animated-gradient font-semibold">
                      VibeJam
                    </span>!
                  </>
                ) : (
                  <>
                    What game to do you want to build today on{" "}
                    <span className="animated-gradient font-semibold">
                      VibeJam
                    </span>?
                  </>
                )}
              </h1>
            </div>

            {/* Search Bar */}
            {(allRequiredTasksCompleted || hasSecretPassword || allowsMultipleSubmissions) && (
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
                    onViewSubmissions={isInternalReviewer ? handleViewSubmissions : undefined}
                    lockedTaskIds={lockedTaskIds}
                    activeTaskId={activeTaskId}
                    isLockingEnabled={
                      !hasSecretPassword &&
                      !isInternalReviewer &&
                      !allowsMultipleSubmissions &&
                      (studyTaskMode === 'website-requirements' ||
                        !allRequiredTasksCompleted ||
                        noEditLockedTaskIds.size > 0)
                    }
                    noEditLockedTaskIds={noEditLockedTaskIds}
                    isPlaygroundNotCompleted={isPlaygroundNotCompleted}
                    showTaskTypeIcons={studyTaskMode !== 'website-requirements'}
                    showLockedTooltip={studyTaskMode !== 'website-requirements'}
                    requiredTaskNames={requiredGameTaskNames}
                    requiredTaskNamesForTime={requiredTaskNamesForCurrentMode}
                    timedTaskNames={timedRequiredTaskNames}
                    timedTaskLimitMinutesByName={timedTaskLimitMinutesByName}
                    tutorialTaskNames={tutorialTaskNames}
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
        </div>
      </div>

      {timedTaskModalState && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 2000,
            padding: '16px',
          }}
        >
          <div
            data-modal-content
            style={{
              backgroundColor: '#1f2937',
              borderRadius: '12px',
              width: '90%',
              maxWidth: '500px',
              display: 'flex',
              flexDirection: 'column',
              boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5), 0 10px 10px -5px rgba(0, 0, 0, 0.2)',
              border: '1px solid rgba(148, 163, 184, 0.25)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '24px 24px 16px 24px',
                borderBottom: '1px solid rgba(148, 163, 184, 0.2)',
              }}
            >
              <h2
                style={{
                  color: '#e2e8f0',
                  fontSize: '22px',
                  fontWeight: 600,
                  letterSpacing: '0.01em',
                  margin: 0,
                }}
              >
                Timed Task Confirmation
              </h2>
            </div>

            <div
              style={{
                padding: '24px',
                display: 'flex',
                flexDirection: 'column',
                gap: '14px',
              }}
            >
              <p
                style={{
                  color: '#e5e7eb',
                  fontSize: '16px',
                  lineHeight: '1.6',
                  margin: 0,
                }}
              >
                This task{' '}
                {timedTaskModalState.minutes
                  ? `has a time limit of ${timedTaskModalState.minutes} minutes.`
                  : 'has a time limit.'}
              </p>
              <p
                style={{
                  color: '#e5e7eb',
                  fontSize: '16px',
                  lineHeight: '1.6',
                  margin: 0,
                }}
              >
                Please make sure you have enough time to complete it in one session. The timer starts only after you click <strong>Start</strong> in the task workspace and does not pause.
              </p>
              <p
                style={{
                  color: '#e5e7eb',
                  fontSize: '16px',
                  lineHeight: '1.6',
                  margin: 0,
                }}
              >
                You will not be able to leave the page once you begin the task.
              </p>

              <div style={{ display: 'flex', gap: '12px', marginTop: '8px' }}>
                <button
                  onClick={() => setTimedTaskModalState(null)}
                  style={{
                    padding: '12px 24px',
                    backgroundColor: '#374151',
                    color: '#e5e7eb',
                    border: '1px solid rgba(148, 163, 184, 0.35)',
                    borderRadius: '6px',
                    fontSize: '15px',
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'background-color 0.2s ease',
                    flex: 1,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = '#4b5563';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = '#374151';
                  }}
                >
                  Cancel
                </button>
                <button
                  onClick={handleConfirmTimedTaskStart}
                  style={{
                    padding: '12px 24px',
                    backgroundColor: '#2563eb',
                    color: 'white',
                    border: 'none',
                    borderRadius: '6px',
                    fontSize: '15px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    transition: 'background-color 0.2s ease',
                    flex: 1,
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.backgroundColor = '#1d4ed8';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.backgroundColor = '#2563eb';
                  }}
                >
                  Start timed task
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function BrowsePage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
        <LoadingSpinner size="xl" color="white" />
      </div>
    }>
      <BrowseInner />
    </Suspense>
  );
}