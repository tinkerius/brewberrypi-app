"""Brew process engine (spec 03: ENG-001..041).

The engine holds at most one brew process and runs its goals in order in a single execution loop. Everything
runs on one asyncio event loop: commands are plain methods called from that loop, so command handling, state
transitions and the execution loop are serialised without locks (ENG-040), and at most one execution loop
task exists (FIX-05).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal

from brewberrypi.clock import Clock
from brewberrypi.config import Config
from brewberrypi.control import RunningTime
from brewberrypi.goals import Goal, GoalContext, create_goal
from brewberrypi.hal import Hal
from brewberrypi.operator_requests import InvalidResponse, RequestReminder
from brewberrypi.recipes import RecipeSource

log = logging.getLogger(__name__)

ProcessState = Literal["Loaded", "Running", "Paused", "Stopped", "Finished"]
EngineState = Literal["NoProcess", "Loaded", "Running", "Paused", "Stopped", "Finished"]

# Rejection texts (the matrix in spec 3.2 only suggests them).
NO_PROCESS = "No process loaded."
NOT_RUNNING = "Process is not running."
STOP_FIRST = "Stop the process first."
ALREADY_RUNNING = "Process is already running."
ALREADY_PAUSED = "Process is already paused."
LOAD_AGAIN = "Load the process again."
NO_ACTIVE_REQUEST = "There is no active request to respond to."
PAUSED_RESUME = "Process is paused. Resume it."
NOT_PAUSED = "Process is not paused."
REQUEST_NOT_OPEN = "This request is no longer open."

ErrorKind = Literal["invalid", "not_found", "conflict"]
"""Why a command was rejected: the outcome of IF-052 (HTTP status 422, 404 or 409)."""

STALLED_AFTER_TICKS = 10
"""A running execution loop that has not ticked for this many periods counts as hung (watchdog)."""


@dataclass(frozen=True)
class Reply:
    """The answer to a command (FIX-15)."""

    success: bool
    message: str
    error: ErrorKind | None = field(default=None, compare=False)
    """Set when `success` is false."""


class BrewProcess:
    """A runtime instance of a recipe with fresh goal instances (DOM-010, DOM-011)."""

    def __init__(
        self, process_id: str, process_name: str, goals: list[Goal], running_time: RunningTime
    ) -> None:
        self.process_id = process_id
        self.process_name = process_name
        self.goals = goals
        self.state: ProcessState = "Loaded"
        self.running_time = running_time

    def active_goal(self) -> Goal | None:
        """The first `Active` goal (DOM-012)."""
        return next((goal for goal in self.goals if goal.status == "Active"), None)


class Engine:
    def __init__(self, hal: Hal, clock: Clock, recipes: RecipeSource, config: Config) -> None:
        self._hal = hal
        self._clock = clock
        self._recipes = recipes
        self._config = config
        self._tick = config.engine.tick_milliseconds / 1000
        self._reminder = RequestReminder(hal, clock, config.ui.request_reminder_interval_seconds)
        self._process: BrewProcess | None = None
        self._loop_task: asyncio.Task[None] | None = None
        self._last_tick = clock.monotonic()
        self._resumed = asyncio.Event()

    @property
    def state(self) -> EngineState:
        """`NoProcess` or the state of the loaded process (ENG-001)."""
        return "NoProcess" if self._process is None else self._process.state

    @property
    def process(self) -> BrewProcess | None:
        return self._process

    # --- commands (spec 3.2) ---

    def load(self, recipe_id: str | None) -> Reply:
        """ENG-010."""
        if self.state in ("Running", "Paused"):
            return self._reject("load", STOP_FIRST)
        if not recipe_id:
            return self._reject("load", "parameter 'recipe' not set.", "invalid")
        recipe = self._recipes.get(recipe_id)
        if recipe is None:
            return self._reject("load", f"Unknown recipe '{recipe_id}'.", "not_found")
        if not recipe.goals:  # Q-34: listed, but nothing to brew
            return self._reject("load", f"Recipe {recipe.name} ({recipe.id}) has no goals.")
        running_time = RunningTime(self._clock)
        context = GoalContext(self._hal, self._config.control, running_time)
        goals = [create_goal(definition, context) for definition in recipe.goals]
        self._process = BrewProcess(recipe.id, recipe.name, goals, running_time)
        return self._accept("load", f"Process {recipe.name} ({recipe.id}) loaded.")

    def unload(self) -> Reply:
        """ENG-011."""
        if self._process is None:
            return self._reject("unload", NO_PROCESS)
        if self._process.state in ("Running", "Paused"):
            return self._reject("unload", STOP_FIRST)
        self._process = None
        self._reminder.stop()
        self._hal.switch_everything_off()
        return self._accept("unload", "Process unloaded.")

    def start(self) -> Reply:
        """ENG-012: start from `Loaded`; a paused process is resumed with `resume` (ENG-016)."""
        process = self._process
        if process is None:
            return self._reject("start", NO_PROCESS)
        if process.state == "Running":
            return self._reject("start", ALREADY_RUNNING)
        if process.state == "Paused":
            return self._reject("start", PAUSED_RESUME)
        if process.state in ("Stopped", "Finished"):
            return self._reject("start", LOAD_AGAIN)
        process.state = "Running"
        self._resumed.set()
        self._last_tick = self._clock.monotonic()
        self._loop_task = asyncio.get_running_loop().create_task(self._execute(process))
        self._loop_task.add_done_callback(self._execution_ended)
        return self._accept("start", "Process started.")

    def pause(self) -> Reply:
        """ENG-013 (FIX-07): heater and pump off, reminders silenced, timers frozen; the request stays."""
        process = self._process
        if process is None:
            return self._reject("pause", NO_PROCESS)
        if process.state == "Paused":
            return self._reject("pause", ALREADY_PAUSED)
        if process.state != "Running":
            return self._reject("pause", NOT_RUNNING)
        process.state = "Paused"
        self._resumed.clear()
        process.running_time.pause()
        self._reminder.stop()
        self._hal.switch_everything_off()
        return self._accept("pause", "Process paused.")

    def resume(self) -> Reply:
        """ENG-016: continue a paused process with the goal that was active; timers continue (ENG-031)."""
        process = self._process
        if process is None:
            return self._reject("resume", NO_PROCESS)
        if process.state != "Paused":
            return self._reject("resume", NOT_PAUSED)
        process.running_time.resume()
        process.state = "Running"
        self._last_tick = self._clock.monotonic()
        self._resumed.set()
        return self._accept("resume", "Process resumed.")

    def stop(self) -> Reply:
        """ENG-014: the loop ends at once; all off; the request is removed; goals keep their state."""
        process = self._process
        if process is None:
            return self._reject("stop", NO_PROCESS)
        if process.state not in ("Running", "Paused"):
            return self._reject("stop", NOT_RUNNING)
        process.state = "Stopped"
        self._cancel_execution()
        self._reminder.stop()
        self._hal.switch_everything_off()
        active = process.active_goal()
        if active is not None:
            active.active_request = None
        return self._accept("stop", "Process stopped.")

    def respond(self, option: str, request_id: str | None = None) -> Reply:
        """ENG-015: answer the active request of the active goal; with `request_id` only that request."""
        goal = None if self._process is None else self._process.active_goal()
        request = None if goal is None else goal.active_request
        if request is None:
            return self._reject("respond", NO_ACTIVE_REQUEST)
        if request_id is not None and request_id != request.id:
            return self._reject("respond", REQUEST_NOT_OPEN)  # a late answer to an earlier question
        try:
            request.respond(option)
        except InvalidResponse as error:
            return self._reject("respond", str(error), "invalid")
        self._follow_request()
        return self._accept("respond", f"Response '{option}' accepted.")

    def alive(self) -> bool:
        """`False` when a running execution loop has ended or stopped ticking (watchdog, contract §4)."""
        process = self._process
        if process is None or process.state != "Running":
            return True
        task = self._loop_task
        if task is None or task.done():
            return False
        return self._clock.monotonic() - self._last_tick <= STALLED_AFTER_TICKS * self._tick

    async def shutdown(self) -> None:
        """End the execution loop and the reminder and switch everything off (application shutdown)."""
        task = self._cancel_execution()
        self._reminder.stop()
        self._hal.switch_everything_off()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    # --- execution loop (spec 3.3) ---

    async def _execute(self, process: BrewProcess) -> None:
        for goal in process.goals:
            goal.activate()
            log.info("starting goal %s", goal.name)
            while True:
                if process.state == "Paused":
                    await self._resumed.wait()
                    continue
                self._last_tick = self._clock.monotonic()
                achieved = self._pursue(goal)
                self._follow_request()
                if achieved:
                    break
                await self._clock.sleep(self._tick)
            goal.complete()
            self._follow_request()
            log.info("goal %s finished", goal.name)
            self._hal.short_beep()
        self._hal.switch_everything_off()
        process.state = "Finished"
        log.info("process %s (%s) finished", process.process_name, process.process_id)

    def _pursue(self, goal: Goal) -> bool:
        try:
            return goal.pursue()
        except Exception:  # ENG-023: retried on the next tick
            log.exception("goal %s failed; heater off, retrying on the next tick", goal.name)
            self._hal.set_heating(0)  # FIX-22
            return False

    def _follow_request(self) -> None:
        """Reminders run only for the active goal's unanswered request while running (DOM-033)."""
        process = self._process
        goal = process.active_goal() if process is not None and process.state == "Running" else None
        self._reminder.follow(None if goal is None else goal.active_request)

    def _cancel_execution(self) -> asyncio.Task[None] | None:
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
        return task

    def _execution_ended(self, task: asyncio.Task[None]) -> None:
        if self._loop_task is task:
            self._loop_task = None
        if not task.cancelled() and task.exception() is not None:
            log.error("execution loop failed; everything off, process stopped", exc_info=task.exception())
            self._reminder.stop()
            self._hal.switch_everything_off()
            if self._process is not None and self._process.state in ("Running", "Paused"):
                self._process.state = "Stopped"

    # --- replies ---

    def _accept(self, command: str, message: str) -> Reply:
        log.info("%s: %s", command, message)
        return Reply(True, message)

    def _reject(self, command: str, message: str, error: ErrorKind = "conflict") -> Reply:
        log.warning("%s rejected: %s", command, message)
        return Reply(False, message, error)
