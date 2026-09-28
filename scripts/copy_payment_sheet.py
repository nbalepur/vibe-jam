#!/usr/bin/env python3
"""
Copy a payment-sheet TSV to the clipboard.

Columns: #, Username, Email, Submissions, Post-Test?, Earned

Paste starting at the header cell in column A. Amount Paid / Notes should live
to the right of Earned so this paste does not overwrite them.

Includes anyone with at least one non-disqualified open-ended game submission
(Platformer + Browse open-ended games), even if earned is $0. Zic-Zac-Zoe /
website recreation, playground, replication, and voting bonuses are excluded.

Row order is frozen in scripts/payment_roster.txt (gitignored). New qualifiers
are appended at the bottom; existing usernames never move.

Earned matches the Compensation page: Stage 3 (⌊eligible tasks / 3⌋ × $15)
plus $10 if the post-test is completed.

Usage:
    ./scripts/pay.sh
    ./scripts/pay.sh --no-copy
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_ENV = REPO_ROOT / ".env.prod"
ROSTER_PATH = Path(__file__).resolve().parent / "payment_roster.txt"

if not PROD_ENV.exists():
    sys.exit(f"Missing {PROD_ENV}. Create it with DATABASE_URL pointing at prod.")

# Must load prod env before importing database.config, which otherwise reads backend/.env.
load_dotenv(PROD_ENV)

sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from database.config import DATABASE_URL, SessionLocal, is_supabase  # noqa: E402
from database.sqlalchemy_models import (  # noqa: E402
    CodeData,
    MCQAData,
    Project,
    SkillCheckAssignment,
    Submission,
    User,
    UserCodeSkillResponse,
    UserMCQASkillResponse,
)

WEBSITE_REQUIREMENT_TASK_NAMES = frozenset(
    {
        "website_tutorial_intro",
        "zic_zac_zoe",
        "website_tutorial_follow_up",
        "zic_zac_zoe_follow_up",
    }
)

INTERNAL_REVIEWER_IDENTIFIERS = frozenset(
    {
        "nbalepur",
        "baumler",
        "gpt-one-shot",
        "gpt-many-shot",
    }
)

MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS = frozenset(
    {
        "hcomp_2026_user",
    }
)

EXPLICIT_TEST_USERNAMES = frozenset(
    {
        "testtest",
        "test-example",
        "test",
        "eek",
        "swagswag",
        "afda@a.com",
        *{f"test{i}" for i in range(1, 101)},
    }
)

STAGE3_TASKS_PER_BLOCK = 3
STAGE3_DOLLARS_PER_BLOCK = 15
POST_TEST_DOLLARS = 10


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _tsv_cell(value: Any) -> str:
    return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _name_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def is_excluded_account(user: User) -> bool:
    tokens = {_norm(user.username), _norm(user.email), _norm(user.id)}
    tokens.discard("")
    if tokens & INTERNAL_REVIEWER_IDENTIFIERS:
        return True
    if tokens & MULTI_SUBMISSION_ACCOUNT_IDENTIFIERS:
        return True
    if tokens & EXPLICIT_TEST_USERNAMES:
        return True
    username = _norm(user.username)
    if username.startswith("test") and username[4:].isdigit():
        return True
    return False


def _project_is_open_ended_game_dev(project: Project) -> bool:
    name = (project.name or "").lower()
    if name == "playground":
        return False
    lab = (project.label or "").lower().replace("_", "-")
    if lab == "website-requirements":
        return False
    if name in WEBSITE_REQUIREMENT_TASK_NAMES:
        return False
    if name == "platformer":
        return True
    effective = (project.label or "open-ended").lower().replace("_", "-")
    return effective == "open-ended"


def _confirm_prod_connection() -> None:
    parsed = urlparse(DATABASE_URL)
    host = (parsed.hostname or "").lower()
    if not is_supabase and "supabase" not in host:
        sys.exit(
            "Refusing to run: DATABASE_URL from .env.prod does not look like Supabase. "
            "Check that .env.prod is loaded and points at prod."
        )
    host_kind = "supabase" if "supabase" in host else host
    print(f"Loaded {PROD_ENV.name}")
    print(f"Database host kind: {host_kind}")


def _load_roster() -> List[str]:
    if not ROSTER_PATH.exists():
        return []
    names: List[str] = []
    seen: Set[str] = set()
    for line in ROSTER_PATH.read_text(encoding="utf-8").splitlines():
        username = line.strip()
        if not username or username.startswith("#"):
            continue
        key = _norm(username)
        if key in seen:
            continue
        seen.add(key)
        names.append(username)
    return names


def _save_roster(usernames: Sequence[str]) -> None:
    body = "\n".join(usernames)
    if body:
        body += "\n"
    ROSTER_PATH.write_text(body, encoding="utf-8")


def _game_progress_by_user(db: Session) -> tuple[Dict[int, int], Dict[int, int]]:
    """Distinct open-ended game tasks and Stage 3 dollars per user.

    Submissions = distinct qualifying tasks with a non-disqualified first
    submission. Stage 3 dollars only count tasks whose first submission is
    inside the first N study-wide open-ended rows.
    """
    cap = int(os.getenv("STAGE3_MAX_SUBMISSIONS_FOR_COMPENSATION", "500"))
    qualifying_ids = [p.id for p in db.query(Project).all() if _project_is_open_ended_game_dev(p)]
    if not qualifying_ids:
        return {}, {}

    subs = (
        db.query(Submission)
        .filter(Submission.project_id.in_(qualifying_ids))
        .filter(Submission.is_disqualified.is_(False))
        .order_by(Submission.created_at.asc(), Submission.id.asc())
        .all()
    )

    first_rank: Dict[tuple, int] = {}
    for rank, submission in enumerate(subs, start=1):
        key = (submission.user_id, submission.project_id)
        if key not in first_rank:
            first_rank[key] = rank

    submissions: Dict[int, int] = defaultdict(int)
    tasks_in_pool: Dict[int, int] = defaultdict(int)
    for (user_id, _project_id), rank in first_rank.items():
        submissions[user_id] += 1
        if rank <= cap:
            tasks_in_pool[user_id] += 1

    stage3 = {
        user_id: (count // STAGE3_TASKS_PER_BLOCK) * STAGE3_DOLLARS_PER_BLOCK
        for user_id, count in tasks_in_pool.items()
    }
    return dict(submissions), stage3


def _post_test_completed_user_ids(db: Session) -> Set[int]:
    """Match backend/main.py post-test completion (no NASA TLI in the expected set)."""
    assignments = db.query(SkillCheckAssignment).all()
    if not assignments:
        return set()

    frontend_by_name = {
        q.name: q for q in db.query(MCQAData).filter(MCQAData.type == "frontend").all() if q.name
    }
    ux_by_name = {q.name: q for q in db.query(MCQAData).filter(MCQAData.type == "ux").all() if q.name}
    code_task_names = {q.task_name for q in db.query(CodeData).all() if q.task_name}
    has_sanity_frontend = "sanity_frontend" in frontend_by_name
    has_sanity_ux = "sanity_ux" in ux_by_name

    answered_by_user: Dict[int, Set[str]] = defaultdict(set)
    for row in (
        db.query(UserMCQASkillResponse.user_id, UserMCQASkillResponse.question_id)
        .filter(UserMCQASkillResponse.phase == "post-test")
        .all()
    ):
        answered_by_user[row[0]].add(row[1])
    for row in (
        db.query(UserCodeSkillResponse.user_id, UserCodeSkillResponse.question_id)
        .filter(UserCodeSkillResponse.phase == "post-test")
        .filter(UserCodeSkillResponse.state.in_(["passed", "reported"]))
        .all()
    ):
        answered_by_user[row[0]].add(row[1])

    completed: Set[int] = set()
    for assignment in assignments:
        expected: Set[str] = set()
        for name in _name_list(assignment.frontend_post_test):
            question = frontend_by_name.get(name)
            if question is None:
                continue
            expected.add(f"frontend_{question.name}" if question.name else f"frontend_{question.id}")
        if assignment.sanity_frontend_phase == "post-test" and has_sanity_frontend:
            expected.add("frontend_sanity_frontend")
        for name in _name_list(assignment.ux_post_test):
            question = ux_by_name.get(name)
            if question is None:
                continue
            expected.add(f"ux_{question.name}" if question.name else f"ux_{question.id}")
        if assignment.sanity_ux_phase == "post-test" and has_sanity_ux:
            expected.add("ux_sanity_ux")
        for name in _name_list(assignment.code_post_test):
            if name in code_task_names:
                expected.add(f"code_normal_{name}")
        for name in _name_list(assignment.debug_post_test):
            if name in code_task_names:
                expected.add(f"code_debug_{name}")
        if not expected:
            continue
        if expected.issubset(answered_by_user.get(assignment.user_id, set())):
            completed.add(assignment.user_id)
    return completed


def _copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Failed to copy to clipboard via pbcopy: {exc}")


def _print_preview(rows: Sequence[tuple]) -> None:
    print()
    print(f"{'#':<4} {'username':<24} {'email':<36} {'subs':>5} {'post':>4} {'earned':>7}")
    shown = list(rows[:8])
    tail = list(rows[-2:]) if len(rows) > 10 else []
    if tail:
        for n, username, email, submissions, post_test, earned in shown:
            print(
                f"{n:<4} {username:<24} {email:<36} {submissions:>5} {post_test or '-':>4} {earned:>7}"
            )
        print("...")
        for n, username, email, submissions, post_test, earned in tail:
            print(
                f"{n:<4} {username:<24} {email:<36} {submissions:>5} {post_test or '-':>4} {earned:>7}"
            )
    else:
        for n, username, email, submissions, post_test, earned in rows:
            print(
                f"{n:<4} {username:<24} {email:<36} {submissions:>5} {post_test or '-':>4} {earned:>7}"
            )
    total_dollars = sum(row[5] for row in rows)
    print()
    print(f"Rows (at least one game submission): {len(rows)}")
    print(f"People with earned > 0: {sum(1 for row in rows if row[5] > 0)}")
    print(f"Post-test completed: {sum(1 for row in rows if row[4] == 'X')}")
    print(f"Sum of earned: ${total_dollars}")


def _total_earned(user_id: int, stage3: Dict[int, int], post_test_done: Set[int]) -> int:
    earned = int(stage3.get(user_id, 0))
    if user_id in post_test_done:
        earned += POST_TEST_DOLLARS
    return earned


def build_sheet_rows(db: Session) -> tuple[List[tuple], int, int, bool, int]:
    users = db.query(User).all()
    enrolled = [user for user in users if not is_excluded_account(user)]
    enrolled.sort(key=lambda user: user.id)
    by_username = {_norm(user.username): user for user in enrolled}

    submissions_by_user, stage3 = _game_progress_by_user(db)
    post_test_done = _post_test_completed_user_ids(db)
    qualified = {
        _norm(user.username)
        for user in enrolled
        if submissions_by_user.get(user.id, 0) >= 1
    }

    existing = _load_roster()
    roster_was_new = not existing
    roster: List[str] = []
    seen: Set[str] = set()
    dropped = 0
    for username in existing:
        key = _norm(username)
        if key in seen:
            continue
        if key not in qualified:
            dropped += 1
            continue
        seen.add(key)
        roster.append(username)

    appended = 0
    for user in enrolled:
        key = _norm(user.username)
        if key in seen or key not in qualified:
            continue
        seen.add(key)
        roster.append(user.username)
        appended += 1

    _save_roster(roster)

    rows: List[tuple] = []
    missing = 0
    for username in roster:
        user = by_username.get(_norm(username))
        if user is None:
            missing += 1
            continue
        submissions = int(submissions_by_user.get(user.id, 0))
        if submissions < 1:
            continue
        post_test = "X" if user.id in post_test_done else ""
        earned = _total_earned(user.id, stage3, post_test_done)
        rows.append((len(rows) + 1, user.username, user.email or "", submissions, post_test, earned))

    return rows, appended, missing, roster_was_new, dropped


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy payment-sheet TSV to the clipboard.")
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Print rows but do not copy to the clipboard.",
    )
    args = parser.parse_args()

    _confirm_prod_connection()
    db = SessionLocal()
    try:
        rows, appended, missing, roster_was_new, dropped = build_sheet_rows(db)
    finally:
        db.close()

    tsv_lines = ["# [paste here]\tUsername\tEmail\tSubmissions\tPost-Test?\tEarned"]
    for n, username, email, submissions, post_test, earned in rows:
        tsv_lines.append(
            f"{n}\t{_tsv_cell(username)}\t{_tsv_cell(email)}\t{submissions}\t{post_test}\t{earned}"
        )
    tsv = "\n".join(tsv_lines) + "\n"

    _print_preview(rows)

    if roster_was_new:
        print(f"Created roster: {ROSTER_PATH} ({len(rows)} game submitters)")
    else:
        print(f"Roster: {ROSTER_PATH} ({len(rows)} game submitters, {appended} appended)")
    if dropped:
        print(
            f"Dropped {dropped} roster username(s) with no qualifying game submission "
            "so later submitters append instead of inserting."
        )
    if missing:
        print(f"Warning: {missing} roster username(s) are no longer in the enrolled set; skipped.")

    if args.no_copy:
        print("Skipped clipboard copy (--no-copy).")
        return

    _copy_to_clipboard(tsv)
    print()
    print("Copied #, Username, Email, Submissions, Post-Test?, Earned as TSV (with header).")
    print('In the sheet: click A1 ("# [paste here]"), then paste.')
    print("Put Amount Paid / Amount Owed / Notes in columns to the right of Earned.")
    print(f"The sheet needs a header row plus {len(rows)} data rows.")


if __name__ == "__main__":
    main()
