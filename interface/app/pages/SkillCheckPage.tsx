import { Clock } from "lucide-react";
import { useState, useEffect } from "react";
import Link from "next/link";
import SkillCheckFlow from "../components/SkillCheckFlow";
import { useAuth } from "../utils/auth";
import LoadingSpinner from "../components/LoadingSpinner";
import { useUserStudyPopup } from "../components/UserStudyPopup";
import { PRE_TEST_SKIPPED_KEY } from "../components/UserStudyPopupProvider";
import { ENV } from "../config/env";

interface SkillCheckPageProps {
  skillCheckMode:
    | "pre-test"
    | "post-test"
    | "locked-pre-test"
    | "locked-post-test"
    | "locked-post-test-cap";
  isCalculating?: boolean;
}

const isSkillCheckLockedMode = (mode: SkillCheckPageProps["skillCheckMode"]) =>
  mode === "locked-pre-test" ||
  mode === "locked-post-test" ||
  mode === "locked-post-test-cap";

export default function SkillCheckPage({ skillCheckMode, isCalculating = false }: SkillCheckPageProps) {
  const { user } = useAuth();
  const { recalculateState } = useUserStudyPopup();

  const handleSkipPreTest = () => {
    if (typeof window !== 'undefined') {
      sessionStorage.setItem(PRE_TEST_SKIPPED_KEY, 'true');
    }
    setCompletionStatus(prev => ({ ...prev, completed: true, loading: false }));
    recalculateState?.();
  };
  const userId = user?.id && !Number.isNaN(Number(user.id)) ? Number(user.id) : null;
  const [isStarted, setIsStarted] = useState(false);
  const [currentQuestionType, setCurrentQuestionType] = useState<string | null>(null);
  const [currentCodeType, setCurrentCodeType] = useState<string | null>(null);
  const [completionStatus, setCompletionStatus] = useState<{
    completed: boolean;
    has_responses: boolean;
    loading: boolean;
    current_question_index: number;
  }>({ completed: false, has_responses: false, loading: true, current_question_index: 0 });

  // Load confetti script dynamically
  useEffect(() => {
    const checkAndLoadConfetti = () => {
      if (typeof window !== 'undefined') {
        // Check if already loaded
        if ((window as any).confetti) {
          return;
        }
        
        // Try to load canvas-confetti which is simpler and more reliable
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/canvas-confetti@1.9.2/dist/confetti.browser.min.js';
        script.async = true;
        script.onload = () => {
          setTimeout(() => {
            if ((window as any).confetti) {
              // Confetti loaded successfully
            }
          }, 100);
        };
        document.head.appendChild(script);
      }
    };
    
    checkAndLoadConfetti();
  }, []);

  // Function to trigger confetti animation (same as project submission)
  const triggerConfetti = () => {
    const confettiLib = (window as any).confetti;
    
    if (confettiLib) {
      const duration = 3 * 1000; // 3 seconds
      const animationEnd = Date.now() + duration;
      const defaults = { startVelocity: 30, spread: 360, ticks: 60, zIndex: 99999 };

      const randomInRange = (min: number, max: number) => {
        return Math.random() * (max - min) + min;
      };

      const interval = setInterval(function() {
        const timeLeft = animationEnd - Date.now();

        if (timeLeft <= 0) {
          return clearInterval(interval);
        }

        const particleCount = 50 * (timeLeft / duration);

        // since particles fall down, start a bit higher than random
        confettiLib(
          Object.assign({}, defaults, {
            particleCount,
            origin: { x: randomInRange(0.1, 0.3), y: Math.random() - 0.2 },
          })
        );
        confettiLib(
          Object.assign({}, defaults, {
            particleCount,
            origin: { x: randomInRange(0.7, 0.9), y: Math.random() - 0.2 },
          })
        );
      }, 250);
    }
  };

  // Check completion status on mount (for both pre-test and post-test)
  useEffect(() => {
    const checkCompletionStatus = async () => {
      if ((skillCheckMode !== 'pre-test' && skillCheckMode !== 'post-test') || !userId) {
        setCompletionStatus({ completed: false, has_responses: false, loading: false, current_question_index: 0 });
        return;
      }

      try {
        const phase = skillCheckMode === 'pre-test' ? 'pre-test' : 'post-test';
        // Add timestamp to prevent caching
        const timestamp = Date.now();
        const response = await fetch(
          `/api/skill-check/completion-status?user_id=${encodeURIComponent(userId)}&phase=${encodeURIComponent(phase)}&_t=${timestamp}`,
          {
            cache: 'no-store', // Prevent browser caching
            headers: {
              'Cache-Control': 'no-cache, no-store, must-revalidate',
              'Pragma': 'no-cache',
            }
          }
        );
        if (response.ok) {
          const data = await response.json();
          setCompletionStatus({
            completed: data.completed || false,
            has_responses: data.has_responses || false,
            loading: false,
            current_question_index: data.current_question_index || 0,
          });
        } else {
          setCompletionStatus({ completed: false, has_responses: false, loading: false, current_question_index: 0 });
        }
      } catch (error) {
        console.error('Error checking completion status:', error);
        setCompletionStatus({ completed: false, has_responses: false, loading: false, current_question_index: 0 });
      }
    };

    checkCompletionStatus();
  }, [skillCheckMode, userId]);
  
  const getDescription = () => {
    if (!isStarted || !currentQuestionType) {
      return skillCheckMode === 'pre-test' 
        ? "Please complete the skill check before starting the website building tasks."
        : "Thank you for completing the website building tasks! Please complete the skill check below to finish the research study.";
    }
    
    if (currentQuestionType === 'coding') {
      return currentCodeType === 'debug'
        ? "Please correct the given code to pass all test cases"
        : "Please implement this function to pass all test cases";
    }
    
    // For frontend, ux, experience, nasa_tli
    return "Please answer the following question";
  };
  
  // Show loading state while calculating to avoid flickering
  if (isCalculating) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-2 mx-auto w-full min-h-[calc(100vh-3rem)]">
        <div className="flex items-center justify-center space-x-3">
          <LoadingSpinner size="lg" color="blue" />
          <p className="text-gray-400 text-lg">Loading Skill Check Progress</p>
        </div>
      </div>
    );
  }

  // Show loading state when verifying completion after finishing skill check
  if (!isStarted && completionStatus.loading && (skillCheckMode === 'pre-test' || skillCheckMode === 'post-test')) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center px-2 mx-auto w-full min-h-[calc(100vh-3rem)]">
        <div className="flex items-center justify-center space-x-3">
          <LoadingSpinner size="lg" color="blue" />
          <p className="text-gray-400 text-lg">Loading Skill Check Progress</p>
        </div>
      </div>
    );
  }
  
  return (
    <div className="flex-1 flex flex-col items-start justify-start pt-2 px-2 mx-auto w-full h-full">
      {!isStarted && (
        <h1 className="text-3xl font-semibold text-white mb-2 mt-4">
          {skillCheckMode === 'pre-test' 
            ? 'Pre-Test Skill Check'
            : skillCheckMode === 'post-test'
            ? 'Post-Test Skill Check'
            : 'Skill Check'}
        </h1>
      )}
      {/* Completion Message - Show if skill check is completed */}
      {!isStarted && (skillCheckMode === 'pre-test' || skillCheckMode === 'post-test') && completionStatus.completed && (
        <div className="bg-blue-900/20 rounded-lg border border-blue-700/50 p-6 mb-4 w-full mt-4">
          <p className="text-gray-300 text-lg">
            Thanks for completing the skill check! You have no other skill check tasks at this time. {skillCheckMode === 'pre-test' && (
              <>Head over to the{" "}
              <Link href="/" className="text-blue-400 hover:text-blue-300 underline font-semibold">
                browse page
              </Link>{" "}
              to start building websites in VibeJam 🚀</>
            )}
            {skillCheckMode === 'post-test' && (
              <>
                You have completed the skill check! If you want to keep building games, you can head over to the{" "}
                <Link href="/browse" className="text-blue-400 hover:text-blue-300 underline font-semibold">
                  browse page
                </Link>{" "}
              and view all website tasks. Thanks again! 🎉
              </>
            )}
          </p>
        </div>
      )}
      {/* Mode Message as Subheader - Only show when not started and not completed */}
      {!isStarted && !((skillCheckMode === 'pre-test' || skillCheckMode === 'post-test') && completionStatus.completed) && (
        skillCheckMode === 'locked-pre-test' ? (
          <div className="bg-blue-900/20 rounded-lg border border-blue-700/50 p-6 mb-4 w-full mt-4">
            <p className="text-gray-300 text-lg">
              Thanks for completing the skill check! You have no other skill check tasks at this time. Head over to the{" "}
              <Link href="/browse" className="text-blue-400 hover:text-blue-300 underline font-semibold">
                tasks page
              </Link>{" "}
              to start building websites in VibeJam 🚀
            </p>
          </div>
        ) : skillCheckMode === "locked-post-test-cap" ? (
          <div className="bg-amber-900/20 rounded-lg border border-amber-700/50 p-6 mb-4 w-full mt-4">
            <p className="text-gray-300 text-lg">
              The post-test is limited to the first{" "}
              {ENV.POST_TEST_PARTICIPANT_CAP.toLocaleString()} participants who met the task requirements (by
              time). That cutoff has been reached. You can keep building on the{" "}
              <Link href="/browse" className="text-amber-300 hover:text-amber-200 underline font-semibold">
                browse page
              </Link>
              .
            </p>
          </div>
        ) : skillCheckMode === 'locked-post-test' ? (
          <div className="bg-blue-900/20 rounded-lg border border-blue-700/50 p-6 mb-4 w-full mt-4">
            <p className="text-gray-300 text-lg">
              <>You have completed the skill check!  If you want to keep building games, you can head over to the{" "}
                <Link href="/browse" className="text-blue-400 hover:text-blue-300 underline font-semibold">
                  browse page
                </Link>{" "}
                and view all website tasks. Thanks again! 🎉
              </>
            </p>
          </div>
        ) : (
          <p className="text-gray-400 text-sm mb-4">
            {getDescription()}
          </p>
        )
      )}
      <div className="flex flex-col gap-4 w-full flex-1 min-h-0">
        {/* Skill Check Flow - Show when started */}
        {isStarted && !isSkillCheckLockedMode(skillCheckMode) ? (
          <div className="flex-1 min-h-0 flex flex-col w-full">
            <SkillCheckFlow
              mode={skillCheckMode === 'pre-test' || skillCheckMode === 'post-test' ? skillCheckMode : 'pre-test'}
              initialIndex={(() => {
                const idx = completionStatus.has_responses && !completionStatus.completed ? completionStatus.current_question_index : 0;
                return idx;
              })()}
              onComplete={() => {
                setIsStarted(false);
                setCurrentQuestionType(null);
                setCurrentCodeType(null);
                
                // Always trigger confetti animation when skill check completes
                triggerConfetti();
                // Show loading spinner while we verify completion status
                setCompletionStatus(prev => ({
                  ...prev,
                  completed: false,
                  loading: true,
                }));
                // Refresh completion status and popup state
                const phase = skillCheckMode === 'pre-test' ? 'pre-test' : 'post-test';
                fetch(`/api/skill-check/completion-status?user_id=${encodeURIComponent(userId!)}&phase=${encodeURIComponent(phase)}`)
                  .then(res => res.json())
                  .then(data => {
                    // Update status based on API response
                    setCompletionStatus({
                      completed: data.completed || false,
                      has_responses: data.has_responses || false,
                      loading: false,
                      current_question_index: data.current_question_index || 0,
                    });
                    // Refresh popup state so it knows skill check is done (this prevents the popup from showing)
                    recalculateState?.();
                  })
                  .catch(err => {
                    console.error('Error refreshing completion status:', err);
                    // If API call fails, assume it's completed (user just finished it)
                    setCompletionStatus(prev => ({
                      ...prev,
                      completed: true,
                      loading: false,
                    }));
                    // Still refresh popup state
                    recalculateState?.();
                  });
              }}
              onCancel={() => {
                setIsStarted(false);
                setCurrentQuestionType(null);
                setCurrentCodeType(null);
              }}
              onQuestionChange={(questionType, codeType) => {
                setCurrentQuestionType(questionType);
                setCurrentCodeType(codeType || null);
              }}
            />
          </div>
        ) : (
          <>
            {/* Instructions - Only show if not locked and not completed */}
            {!isSkillCheckLockedMode(skillCheckMode) && !((skillCheckMode === 'pre-test' || skillCheckMode === 'post-test') && completionStatus.completed) && (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
            <h2 className="text-xl font-semibold text-white mb-3">What You'll Do</h2>
            <div className="text-gray-300 space-y-3 leading-relaxed text-sm">
              <p>
                {'As part of our research study on AI coding assistants, we are running "skill checks" to measure your general coding abilities and knowledge. This check is broken down into two parts:'}
              </p>
              <div className="mt-4 space-y-3">
                <div className="flex items-start space-x-2">
                  <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-600 flex items-center justify-center mt-0.5">
                    <span className="text-white text-xs font-semibold">1</span>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-white mb-1">Multiple-Choice Knowledge Questions ({"<"}15 minutes)</h3>
                    <p className="text-gray-300 text-sm">
                      You&apos;ll answer MCQAs that cover:
                    </p>
                    <ul className="list-disc list-inside mt-1.5 text-gray-300 ml-3 text-sm">
                      <li>
                        {"Your knowledge of frontend syntax and programming (HTML, CSS, JavaScript)"}
                      </li>
                      <li>{"Your knowledge of user experience (UX) design principles"}</li>
                    </ul>
                  </div>
                </div>
                <div className="flex items-start space-x-2">
                  <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-600 flex items-center justify-center mt-0.5">
                    <span className="text-white text-xs font-semibold">2</span>
                  </div>
                  <div>
                    <h3 className="text-base font-semibold text-white mb-1">Coding Tasks ({"<"}30 minutes)</h3>
                    <p className="text-gray-300 text-sm mb-1.5">
                      You'll then complete a series of coding tasks <strong className="text-white">without AI assistance</strong>, where you must implement a function to pass a set of test cases. You will either start from:
                    </p>
                    <ul className="list-disc list-inside mt-1.5 text-gray-300 ml-3 text-sm">
                      <li>A blank implementation</li>
                      <li>An existing, faulty implementation</li>
                    </ul>
                  </div>
                </div>
              </div>
              <p className="text-gray-300 text-sm mt-4">
                If you get stuck on any question, you can hit the "Report" button at the top right of the screen to skip it. This button appears after 30 seconds to ensure good-faith attempts.
              </p>
            </div>
          </div>
        )}

        {/* Time Estimate - Only show if not locked and not completed */}
        {!isSkillCheckLockedMode(skillCheckMode) && !((skillCheckMode === 'pre-test' || skillCheckMode === 'post-test') && completionStatus.completed) && (
          <div className="bg-blue-900/20 rounded-lg border border-blue-700/50 p-4">
            <div className="flex items-start space-x-2">
              <Clock className="text-blue-400 mt-0.5 flex-shrink-0" size={18} />
              <div>
                <h3 className="text-base font-semibold text-white mb-1">Time Commitment</h3>
                <p className="text-gray-300 text-sm">
                  We expect the Skill Check assessment to take a maximum of <strong className="text-white">45 minutes</strong> to complete.
                  Please set aside enough time to finish the assessment in one session.
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Start Button - Only show if not locked, not completed, and loading is complete */}
        {!isSkillCheckLockedMode(skillCheckMode) && !((skillCheckMode === 'pre-test' || skillCheckMode === 'post-test') && completionStatus.completed) && !completionStatus.loading && (
          <div className="flex flex-col items-center gap-3 pt-2">
            <button
              onClick={() => {
                setIsStarted(true);
              }}
              className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-lg transition-colors duration-200 text-sm"
            >
              {completionStatus.has_responses && !completionStatus.completed
                  ? (skillCheckMode === 'pre-test' ? 'Resume Skill Check (Pre-Test)' : 'Resume Skill Check (Post-Test)')
                  : (skillCheckMode === 'pre-test' ? 'Start Skill Check (Pre-Test)' : 'Start Skill Check (Post-Test)')}
            </button>
            {skillCheckMode === 'pre-test' && (
              <button
                type="button"
                onClick={handleSkipPreTest}
                className="text-gray-400 hover:text-gray-300 text-sm underline"
              >
                Skip pre-test (do NOT click this if you are in CMSC848Q)
              </button>
            )}
          </div>
        )}
          </>
        )}
      </div>
    </div>
  );
}

