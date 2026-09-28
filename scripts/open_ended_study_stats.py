#!/usr/bin/env python3
"""
Compute open-ended game study stats from the production database.

Loads DATABASE_URL from repo-root `.env.prod` (gitignored) before any
database imports, so local `backend/.env` is not used.

Counts:
  - enrolled users (accounts, minus test/internal)
  - submissions on open-ended game tasks
  - AI prompts (assistant_logs) on those same tasks

Excludes Zic-Zac-Zoe / website-requirements tasks, playground, and
replication tasks.

Usage:
    python scripts/open_ended_study_stats.py
    conda run -n helpful-coding python scripts/open_ended_study_stats.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional, Set
from urllib.parse import urlparse

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
PROD_ENV = REPO_ROOT / ".env.prod"

if not PROD_ENV.exists():
    sys.exit(f"Missing {PROD_ENV}. Create it with DATABASE_URL pointing at prod.")

# Must load prod env before importing database.config, which otherwise reads backend/.env.
load_dotenv(PROD_ENV)

sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from database.config import DATABASE_URL, SessionLocal, is_supabase  # noqa: E402
from database.sqlalchemy_models import (  # noqa: E402
    AssistantLog,
    Project,
    Submission,
    User,
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


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _project_label(project: Project) -> str:
    return _norm(project.label or "open-ended").replace("_", "-")


def is_playground(project: Project) -> bool:
    return _norm(project.name) == "playground"


def is_website_requirement_project(project: Project) -> bool:
    if _project_label(project) == "website-requirements":
        return True
    return _norm(project.name) in WEBSITE_REQUIREMENT_TASK_NAMES


def is_open_ended_game_project(project: Project) -> bool:
    """Match browse/stats: open-ended game tasks only (not ZZZ, not replication)."""
    if is_playground(project) or is_website_requirement_project(project):
        return False
    return _project_label(project) == "open-ended"


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


def experiment_group(user: User) -> Optional[str]:
    settings = user.settings if isinstance(user.settings, dict) else {}
    group = settings.get("experiment_group")
    if isinstance(group, str):
        normalized = group.strip().lower()
        if normalized in {"chat", "agent"}:
            return normalized
    return None


def _ids(items: Iterable[Any]) -> Set[int]:
    return {item.id for item in items}


def _print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


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


def compute_stats(db: Session) -> None:
    users = db.query(User).all()
    projects = db.query(Project).all()
    projects_by_id = {p.id: p for p in projects}

    open_ended_projects = [p for p in projects if is_open_ended_game_project(p)]
    zzz_projects = [p for p in projects if is_website_requirement_project(p)]
    open_ended_ids = _ids(open_ended_projects)
    zzz_ids = _ids(zzz_projects)

    enrolled_users = [u for u in users if not is_excluded_account(u)]
    excluded_users = [u for u in users if is_excluded_account(u)]
    enrolled_ids = _ids(enrolled_users)

    def count_rows(model, project_ids: Set[int], user_ids: Optional[Set[int]] = None) -> int:
        q = db.query(func.count(model.id)).filter(model.project_id.in_(project_ids))
        if user_ids is not None:
            q = q.filter(model.user_id.in_(user_ids))
        return int(q.scalar() or 0)

    def distinct_users(model, project_ids: Set[int], user_ids: Optional[Set[int]] = None) -> int:
        q = db.query(func.count(func.distinct(model.user_id))).filter(model.project_id.in_(project_ids))
        if user_ids is not None:
            q = q.filter(model.user_id.in_(user_ids))
        return int(q.scalar() or 0)

    oe_submissions = count_rows(Submission, open_ended_ids, enrolled_ids)
    oe_submissions_all_users = count_rows(Submission, open_ended_ids)
    oe_submissions_not_dq = (
        db.query(func.count(Submission.id))
        .filter(
            Submission.project_id.in_(open_ended_ids),
            Submission.user_id.in_(enrolled_ids),
            Submission.is_disqualified.is_(False),
        )
        .scalar()
        or 0
    )
    oe_submitters = distinct_users(Submission, open_ended_ids, enrolled_ids)
    oe_prompts = count_rows(AssistantLog, open_ended_ids, enrolled_ids)
    oe_prompt_users = distinct_users(AssistantLog, open_ended_ids, enrolled_ids)

    engaged_user_ids = set(
        row[0]
        for row in db.query(Submission.user_id)
        .filter(Submission.project_id.in_(open_ended_ids), Submission.user_id.in_(enrolled_ids))
        .distinct()
        .all()
    ) | set(
        row[0]
        for row in db.query(AssistantLog.user_id)
        .filter(AssistantLog.project_id.in_(open_ended_ids), AssistantLog.user_id.in_(enrolled_ids))
        .distinct()
        .all()
    )

    group_counts = defaultdict(int)
    for user in enrolled_users:
        group_counts[experiment_group(user) or "(none)"] += 1

    _print_section("Open-ended game study (excluding Zic-Zac-Zoe)")
    print(f"Users enrolled:              {len(enrolled_users):>6}  (of {len(users)} accounts total)")
    print(f"  with an open-ended prompt: {oe_prompt_users:>6}")
    print(f"  with an open-ended submit: {oe_submitters:>6}")
    print(f"  with any open-ended work:  {len(engaged_user_ids):>6}")
    print(f"Open-ended submissions:      {oe_submissions:>6}  ({oe_submitters} users)")
    print(f"  excluding disqualified:    {int(oe_submissions_not_dq):>6}")
    print(f"Open-ended AI prompts:       {oe_prompts:>6}  ({oe_prompt_users} users)")

    _print_section("Experiment group (enrolled users)")
    for key in ("chat", "agent", "(none)"):
        if group_counts[key]:
            print(f"  {key:<10} {group_counts[key]:>6}")

    _print_section("Open-ended tasks")
    print(f"{'task':<32} {'label':<14} {'submissions':>12} {'prompts':>10}")
    task_rows = []
    for project in sorted(open_ended_projects, key=lambda p: _norm(p.name)):
        subs = count_rows(Submission, {project.id}, enrolled_ids)
        prompts = count_rows(AssistantLog, {project.id}, enrolled_ids)
        task_rows.append((project, subs, prompts))
        print(f"{(project.name or ''):<32} {(project.label or ''):<14} {subs:>12} {prompts:>10}")
    print(f"{'TOTAL':<32} {'':<14} {oe_submissions:>12} {oe_prompts:>10}")

    _print_section("Excluded (not counted above)")
    print(f"Test / internal / kiosk accounts: {len(excluded_users):>6}")
    print(f"Open-ended rows from those accounts: submissions={oe_submissions_all_users - oe_submissions}, prompts={count_rows(AssistantLog, open_ended_ids) - oe_prompts}")
    print(f"Zic-Zac-Zoe / website_requirements projects: {len(zzz_projects)}")
    print(f"  submissions: {count_rows(Submission, zzz_ids, enrolled_ids)}")
    print(f"  AI prompts:  {count_rows(AssistantLog, zzz_ids, enrolled_ids)}")
    unused_projects = [
        p for p in projects if p.id not in open_ended_ids and p.id not in zzz_ids and not is_playground(p)
    ]
    if unused_projects:
        print(f"Other non-open-ended projects (e.g. replication): {len(unused_projects)}")
        print(
            f"  submissions: {count_rows(Submission, _ids(unused_projects), enrolled_ids)}, "
            f"prompts: {count_rows(AssistantLog, _ids(unused_projects), enrolled_ids)}"
        )


def main() -> None:
    _confirm_prod_connection()
    db = SessionLocal()
    try:
        compute_stats(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
