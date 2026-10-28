#!/usr/bin/env python3
"""Tests and a day-by-day simulation for the daily note schedule."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import schedule


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / "workflows" / "daily-ai-notes.yml"
)
LEGACY_HOURS = {0, 4, 8, 12, 16, 20}


class PlanTests(unittest.TestCase):
    def test_counts_stay_inside_one_to_ten_and_cover_every_value(self) -> None:
        histogram = {count: 0 for count in range(1, 11)}
        for offset in range(400):
            day = _day(offset)
            plan = schedule.plan_times(day, "seed")
            self.assertGreaterEqual(len(plan), 1)
            self.assertLessEqual(len(plan), 10)
            histogram[len(plan)] += 1
        self.assertEqual(set(histogram), set(range(1, 11)))
        self.assertTrue(all(histogram[count] > 0 for count in range(1, 11)))

    def test_same_day_and_seed_are_stable_and_seeds_diverge(self) -> None:
        first = schedule.plan_times("2026-10-28", "seed")
        second = schedule.plan_times("2026-10-28", "seed")
        other = schedule.plan_times("2026-10-28", "other-seed")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_slots_are_ordered_inside_the_window_with_the_minimum_gap(self) -> None:
        for offset in range(60):
            plan = schedule.plan_times(_day(offset), "seed", min_gap=45)
            self.assertEqual(plan, sorted(plan))
            self.assertEqual(len(plan), len(set(plan)))
            self.assertGreaterEqual(plan[0], 0)
            self.assertLessEqual(plan[-1], schedule.LATEST_MINUTE)
            for earlier, later in zip(plan, plan[1:]):
                self.assertGreaterEqual(later - earlier, 45)

    def test_times_are_not_the_old_four_hour_grid(self) -> None:
        outside = 0
        first_hours: set[int] = set()
        for offset in range(60):
            plan = schedule.plan_times(_day(offset), "seed")
            first_hours.add(plan[0] // 60)
            hours = {minute // 60 for minute in plan}
            if not hours <= LEGACY_HOURS:
                outside += 1
        self.assertGreaterEqual(outside, 50)
        self.assertGreaterEqual(len(first_hours), 8)

    def test_bounds_clamp_to_the_absolute_range(self) -> None:
        self.assertEqual(schedule.clamp_bounds(0, 50), (1, 10))
        self.assertEqual(schedule.clamp_bounds(4, 4), (4, 4))
        for offset in range(30):
            plan = schedule.plan_times(
                _day(offset), "seed", min_posts=4, max_posts=4
            )
            self.assertEqual(len(plan), 4)

    def test_oversized_gap_shrinks_so_the_full_count_still_fits(self) -> None:
        plan = schedule.plan_times(
            "2026-10-28",
            "seed",
            min_posts=10,
            max_posts=10,
            min_gap=10_000,
        )
        self.assertEqual(len(plan), 10)
        self.assertLessEqual(plan[-1], schedule.LATEST_MINUTE)
        self.assertEqual(len(set(plan)), 10)

    def test_one_post_days_still_move_around(self) -> None:
        slots = {
            schedule.plan_times(_day(offset), "seed", min_posts=1, max_posts=1)[0]
            for offset in range(40)
        }
        self.assertGreaterEqual(len(slots), 20)


class DecisionTests(unittest.TestCase):
    def test_nothing_is_due_before_the_first_slot(self) -> None:
        plan = [90, 400, 800]
        self.assertEqual(schedule.posts_to_make(plan, 89, 0), 0)

    def test_on_time_ticks_publish_one_per_slot_and_then_stop(self) -> None:
        for offset in range(40):
            plan = schedule.plan_times(_day(offset), "spread")
            posted = 0
            posts_per_tick: list[int] = []
            for minute in schedule.scheduled_minutes():
                count = schedule.posts_to_make(plan, minute, posted)
                if count:
                    posts_per_tick.append(count)
                posted += count
            self.assertEqual(posted, len(plan))
            self.assertEqual(posts_per_tick, [1] * len(plan))
            self.assertEqual(schedule.posts_to_make(plan, 23 * 60 + 59, posted), 0)

    def test_skipped_runs_are_made_up_without_exceeding_the_plan(self) -> None:
        for offset in range(40):
            plan = schedule.plan_times(_day(offset), "makeup")
            ticks = schedule.scheduled_minutes()
            # Drop every regular tick and keep the late makeup tick.
            posted = schedule.posts_to_make(plan, ticks[-1], 0)
            self.assertEqual(posted, len(plan))
            self.assertLessEqual(posted, 10)
            self.assertEqual(schedule.posts_to_make(plan, ticks[-1], posted), 0)

    def test_committed_slots_are_not_published_again(self) -> None:
        plan = [90, 400, 800, 1100]
        tick = next(minute for minute in schedule.scheduled_minutes() if minute >= 400)
        due = sum(1 for slot in plan if slot <= tick)
        self.assertGreaterEqual(due, 2)
        self.assertEqual(schedule.posts_to_make(plan, tick, due), 0)
        self.assertEqual(schedule.posts_to_make(plan, tick, due - 1), 1)

    def test_an_early_manual_post_consumes_one_slot_of_the_plan(self) -> None:
        plan = [100, 400, 800]
        self.assertEqual(schedule.posts_to_make(plan, 0, 0, manual=True), 1)
        self.assertEqual(schedule.posts_to_make(plan, 100, 1), 0)
        self.assertEqual(schedule.posts_to_make(plan, 400, 1), 1)

    def test_manual_run_posts_one_until_the_plan_is_full(self) -> None:
        plan = [30, 200, 600]
        self.assertEqual(schedule.posts_to_make(plan, 0, 0, manual=True), 1)
        self.assertEqual(schedule.posts_to_make(plan, 0, 2, manual=True), 1)
        self.assertEqual(schedule.posts_to_make(plan, 0, 3, manual=True), 0)
        self.assertEqual(schedule.posts_to_make(plan, 0, 10, manual=True), 0)

    def test_already_over_the_cap_never_posts(self) -> None:
        plan = schedule.plan_times("2026-10-28", "seed", min_posts=10, max_posts=10)
        self.assertEqual(schedule.posts_to_make(plan, 23 * 60, 10), 0)


class GitAndWorkflowTests(unittest.TestCase):
    def test_subject_counter_ignores_other_days_and_other_commits(self) -> None:
        subjects = [
            "Daily AI note - 2026-10-28 08:01 UTC",
            "Daily AI note - 2026-10-28 18:44 UTC",
            "Daily AI note - 2026-10-27 23:59 UTC",
            "README refresh",
            "Daily AI note - 2026-10-28",  # missing the trailing space + time
        ]
        self.assertEqual(schedule.count_subjects(subjects, "2026-10-28"), 2)

    def test_decide_cli_uses_the_environment_and_hides_the_seed(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "DAY": "2026-10-28",
                "NOW_MINUTE": "0",
                "ALREADY_POSTED": "0",
                "EVENT_NAME": "schedule",
                "SCHEDULE_SEED": "cli-seed",
                "MIN_POSTS_PER_DAY": "1",
                "MAX_POSTS_PER_DAY": "1",
                "MIN_GAP_MINUTES": "45",
            }
        )
        plan = schedule.plan_times("2026-10-28", "cli-seed", min_posts=1, max_posts=1)
        before = max(plan[0] - 1, 0)
        env["NOW_MINUTE"] = str(before)
        completed = subprocess.run(
            ["python3", str(Path(schedule.__file__)), "decide"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        output = _output(completed.stdout)
        self.assertNotIn("cli-seed", completed.stdout)
        self.assertNotIn("cli-seed", completed.stderr)
        self.assertEqual(output["planned_count"], "1")
        self.assertEqual(output["day"], "2026-10-28")
        if plan[0] == 0:
            self.assertEqual(output["post_count"], "1")
        else:
            self.assertEqual(output["run_job"], "false")
            self.assertEqual(output["post_count"], "0")

        env["NOW_MINUTE"] = str(plan[0])
        due = subprocess.run(
            ["python3", str(Path(schedule.__file__)), "decide"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        due_output = _output(due.stdout)
        self.assertEqual(due_output["run_job"], "true")
        self.assertEqual(due_output["post_count"], "1")

        env["ALREADY_POSTED"] = "1"
        again = subprocess.run(
            ["python3", str(Path(schedule.__file__)), "decide"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(_output(again.stdout)["post_count"], "0")

    def test_git_history_is_the_source_of_already_posted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test")
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            _git(repo, "add", "README.md")
            _commit(repo, "Daily AI note - 2026-10-27 09:00 UTC")
            _commit(repo, "Daily AI note - 2026-10-28 09:00 UTC")
            _commit(repo, "Daily AI note - 2026-10-28 15:00 UTC")
            env = os.environ.copy()
            env.pop("ALREADY_POSTED", None)
            env.update(
                {
                    "DAY": "2026-10-28",
                    "NOW_MINUTE": str(23 * 60),
                    "EVENT_NAME": "schedule",
                    "SCHEDULE_SEED": "git-seed",
                    "MIN_POSTS_PER_DAY": "10",
                    "MAX_POSTS_PER_DAY": "10",
                }
            )
            completed = subprocess.run(
                ["python3", str(Path(schedule.__file__)), "decide"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=repo,
            )
            output = dict(
                line.split("=", 1)
                for line in completed.stdout.splitlines()
                if "=" in line
            )
            # 10 planned, 2 already committed today, all slots are due at 23:00.
            self.assertEqual(output["planned_count"], "10")
            self.assertEqual(output["post_count"], "8")
            self.assertEqual(output["run_job"], "true")

    def test_workflow_crons_match_the_scheduler(self) -> None:
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        crons = re.findall(r"- cron:\s*'([^']+)'", text)
        self.assertEqual(tuple(crons), schedule.WORKFLOW_CRONS)
        self.assertIn("MIN_POSTS_PER_DAY", text)
        self.assertIn("MAX_POSTS_PER_DAY", text)
        self.assertIn(".github/scripts/schedule.py decide", text)
        self.assertIn('git fetch origin "${GITHUB_REF_NAME}"', text)
        self.assertNotIn("*/4 * * *", text)
        self.assertNotIn("CHANCE", text)


def _output(stdout: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in stdout.splitlines() if "=" in line)


def _day(offset: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=offset)).strftime(
        "%Y-%m-%d"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _commit(repo: Path, message: str) -> None:
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    unittest.main()
