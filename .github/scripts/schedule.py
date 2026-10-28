#!/usr/bin/env python3
"""Decide how many daily AI notes to publish on a workflow run.

Each UTC day has one plan: a count between MIN_POSTS_PER_DAY and
MAX_POSTS_PER_DAY (clamped to 1..10) and that many minute-of-day slots.
The plan is a pure function of the UTC date and a seed, so it does not
need a state file. Commits whose subject is ``Daily AI note - <day> ...``
are the record of what has already been published.

A workflow run posts only the slots that are already due and not yet
covered by those commits. A delayed or repeated run therefore cannot
double-post, and a skipped run is made up by the next run that does
start — including the late runs — so the day's count still lands as long
as one run starts after the planned slots are due.

Tune the range with the MIN_POSTS_PER_DAY and MAX_POSTS_PER_DAY
environment variables (see the daily-ai-notes workflow). Set the
SCHEDULE_SEED secret to change the sequence; an empty secret uses the
built-in default.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

ABSOLUTE_MIN_POSTS = 1
ABSOLUTE_MAX_POSTS = 10
DEFAULT_MIN_POSTS = 1
DEFAULT_MAX_POSTS = 10
DEFAULT_MIN_GAP_MINUTES = 45
# Last minute a slot may occupy. Later cron ticks (22:07 onward) are the
# makeup window, so a delayed tick still commits on the same UTC day.
LATEST_MINUTE = 22 * 60
DEFAULT_SEED = "ai-daily-journal-schedule-v1"
NOTE_SUBJECT_PREFIX = "Daily AI note - "

# Must match the `schedule` crons in daily-ai-notes.yml.
# Regular ticks are 30 minutes apart (`7,37`). The default 45-minute gap is
# wider than that, so an on-time run publishes at most one note.
WORKFLOW_CRONS = ("7,37 * * * *", "50 22 * * *")


class Entropy:
    """SHA-256 counter stream. Stable across Python versions, unlike random.Random."""

    def __init__(self, material: bytes) -> None:
        self._material = material
        self._counter = 0

    def randbelow(self, span: int) -> int:
        if span <= 0:
            raise ValueError(f"span must be positive, got {span}")
        # Largest multiple of span that fits in uint64, so the modulo is unbiased.
        limit = (1 << 64) // span * span
        while True:
            digest = hashlib.sha256(
                self._material + self._counter.to_bytes(8, "big")
            ).digest()
            self._counter += 1
            value = int.from_bytes(digest[:8], "big")
            if value < limit:
                return value % span


def clamp_bounds(min_posts: int, max_posts: int) -> tuple[int, int]:
    """Force the requested range into the inclusive absolute bounds."""
    low = min(max(min_posts, ABSOLUTE_MIN_POSTS), ABSOLUTE_MAX_POSTS)
    high = min(max(max_posts, ABSOLUTE_MIN_POSTS), ABSOLUTE_MAX_POSTS)
    if high < low:
        high = low
    return low, high


def scheduled_minutes() -> list[int]:
    """Minute-of-day values at which the workflow cron can fire."""
    minutes = {hour * 60 + minute for hour in range(24) for minute in (7, 37)}
    minutes.add(22 * 60 + 50)
    return sorted(minutes)


def plan_times(
    day: str,
    seed: str,
    min_posts: int = DEFAULT_MIN_POSTS,
    max_posts: int = DEFAULT_MAX_POSTS,
    min_gap: int = DEFAULT_MIN_GAP_MINUTES,
    latest_minute: int = LATEST_MINUTE,
) -> list[int]:
    """Return sorted UTC minute-of-day slots for ``day`` (``YYYY-MM-DD``)."""
    if latest_minute < 0:
        raise ValueError("latest_minute must be >= 0")
    low, high = clamp_bounds(min_posts, max_posts)
    entropy = Entropy(f"{seed}|{day}".encode())
    count = low + entropy.randbelow(high - low + 1)
    gap = _feasible_gap(count, min_gap, latest_minute)
    if count == 1:
        return [entropy.randbelow(latest_minute + 1)]

    # Distinct offsets in 0..usable, then spread by ``gap`` minutes.
    usable = latest_minute - (count - 1) * gap
    offsets = _sample_sorted(entropy, usable + 1, count)
    return [offsets[index] + index * gap for index in range(count)]


def posts_to_make(
    plan: Sequence[int],
    now_minute: int,
    already_posted: int,
    *,
    manual: bool = False,
) -> int:
    """How many notes this run should publish.

    Scheduled runs publish every slot that is due and not yet committed.
    That is usually one note. After skipped runs it is the whole deficit,
    which is what keeps the daily count exact. Manual runs publish one
    note early, and stop once today's plan (and the hard cap) is full.
    """
    target = min(len(plan), ABSOLUTE_MAX_POSTS)
    already = max(0, already_posted)
    if already >= target or already >= ABSOLUTE_MAX_POSTS:
        return 0
    if manual:
        return 1
    due = sum(1 for slot in plan if slot <= now_minute)
    deficit = due - already
    remaining = min(target, ABSOLUTE_MAX_POSTS) - already
    return max(0, min(deficit, remaining))


def count_subjects(subjects: Iterable[str], day: str) -> int:
    """Count note commits for ``day`` from commit subjects."""
    prefix = f"{NOTE_SUBJECT_PREFIX}{day} "
    return sum(1 for subject in subjects if subject.startswith(prefix))


def resolve_seed(raw: str | None) -> str:
    if raw is None or raw.strip() == "":
        return DEFAULT_SEED
    return raw


def format_minute(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def decide(
    *,
    day: str,
    now_minute: int,
    already_posted: int,
    seed: str,
    manual: bool,
    min_posts: int,
    max_posts: int,
    min_gap: int,
) -> dict[str, int | str | bool]:
    plan = plan_times(day, seed, min_posts, max_posts, min_gap, LATEST_MINUTE)
    count = posts_to_make(plan, now_minute, already_posted, manual=manual)
    due = sum(1 for slot in plan if slot <= now_minute)
    return {
        "day": day,
        "planned_count": len(plan),
        "already_posted": already_posted,
        "due": due,
        "post_count": count,
        "run_job": count > 0,
        "manual": manual,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "decide"
    if command == "decide":
        return _cmd_decide()
    if command == "simulate":
        return _cmd_simulate(args[1:])
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


def _cmd_decide() -> int:
    now = datetime.now(timezone.utc)
    day = os.environ.get("DAY") or now.strftime("%Y-%m-%d")
    if "NOW_MINUTE" in os.environ:
        now_minute = int(os.environ["NOW_MINUTE"])
    else:
        now_minute = now.hour * 60 + now.minute
    if "ALREADY_POSTED" in os.environ:
        already = int(os.environ["ALREADY_POSTED"])
    else:
        already = count_subjects(_git_subjects(), day)
    decision = decide(
        day=day,
        now_minute=now_minute,
        already_posted=already,
        seed=resolve_seed(os.environ.get("SCHEDULE_SEED")),
        manual=os.environ.get("EVENT_NAME") == "workflow_dispatch",
        min_posts=int(os.environ.get("MIN_POSTS_PER_DAY", DEFAULT_MIN_POSTS)),
        max_posts=int(os.environ.get("MAX_POSTS_PER_DAY", DEFAULT_MAX_POSTS)),
        min_gap=int(os.environ.get("MIN_GAP_MINUTES", DEFAULT_MIN_GAP_MINUTES)),
    )
    print(
        "day={day} planned={planned_count} already={already_posted} "
        "due={due} posting={post_count} manual={manual}".format(**decision),
        file=sys.stderr,
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(
                f"UTC {decision['day']}: plan is {decision['planned_count']} "
                f"posts, {decision['already_posted']} already committed, "
                f"posting {decision['post_count']} on this run.\n"
            )
    # stdout is appended to $GITHUB_OUTPUT. Keep it to simple key=value lines.
    print(f"run_job={'true' if decision['run_job'] else 'false'}")
    print(f"post_count={decision['post_count']}")
    print(f"planned_count={decision['planned_count']}")
    print(f"day={decision['day']}")
    return 0


def _cmd_simulate(args: Sequence[str]) -> int:
    days = 21
    seed = DEFAULT_SEED
    index = 0
    while index < len(args):
        flag = args[index]
        if flag == "--days":
            index += 1
            days = int(args[index])
        elif flag == "--seed":
            index += 1
            seed = args[index]
        else:
            print(f"unknown simulate argument: {flag}", file=sys.stderr)
            return 2
        index += 1

    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    histogram = {count: 0 for count in range(ABSOLUTE_MIN_POSTS, ABSOLUTE_MAX_POSTS + 1)}
    on_time_ok = 0
    makeup_ok = 0
    repeat_extra = 0
    first_hours: set[int] = set()
    print(f"seed={seed} days={days} gap={DEFAULT_MIN_GAP_MINUTES}")
    print("day         count  slots")
    for offset in range(days):
        day = (start + timedelta(days=offset)).strftime("%Y-%m-%d")
        plan = plan_times(day, seed)
        histogram[len(plan)] += 1
        first_hours.add(plan[0] // 60)
        slots = ", ".join(format_minute(minute) for minute in plan)
        print(f"{day}  {len(plan):5d}  {slots}")
        if _simulate_ticks(plan, scheduled_minutes(), skip=set()) == len(plan):
            on_time_ok += 1
        last_only = set(scheduled_minutes()[:-1])
        if _simulate_ticks(plan, scheduled_minutes(), skip=last_only) == len(plan):
            makeup_ok += 1
        posted = _simulate_ticks(plan, scheduled_minutes(), skip=set())
        extra = posts_to_make(plan, 23 * 60 + 59, posted, manual=False)
        repeat_extra += extra

    print("histogram:")
    for count in range(ABSOLUTE_MIN_POSTS, ABSOLUTE_MAX_POSTS + 1):
        print(f"  {count:2d}  {histogram[count]}")
    print(f"on-time days hitting the plan: {on_time_ok}/{days}")
    print(f"only-last-run days hitting the plan: {makeup_ok}/{days}")
    print(f"extra posts on an immediate second pass: {repeat_extra}")
    print(f"distinct first-post hours: {len(first_hours)}")
    return 0


def _simulate_ticks(plan: Sequence[int], ticks: Sequence[int], skip: set[int]) -> int:
    posted = 0
    for minute in ticks:
        if minute in skip:
            continue
        posted += posts_to_make(plan, minute, posted, manual=False)
    return posted


def _git_subjects() -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%s"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _feasible_gap(count: int, min_gap: int, latest_minute: int) -> int:
    gap = max(0, min_gap)
    if count <= 1:
        return gap
    max_gap = (latest_minute - count + 1) // (count - 1)
    if max_gap < 0:
        raise ValueError(
            f"cannot place {count} posts before minute {latest_minute}"
        )
    return min(gap, max_gap)


def _sample_sorted(entropy: Entropy, population: int, count: int) -> list[int]:
    if count > population:
        raise ValueError(f"cannot sample {count} values from {population}")
    chosen: set[int] = set()
    while len(chosen) < count:
        chosen.add(entropy.randbelow(population))
    return sorted(chosen)


if __name__ == "__main__":
    sys.exit(main())
