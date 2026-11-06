"""M4: goals and operator requests (spec 02, 04, DOM-033; goal parts of AC-004..007 and AC-050..057).

The engine (M5) activates goals, calls `pursue()` every tick, completes them, beeps after each goal and
drives the reminder; here the tests play the engine's part by hand.
"""

from pathlib import Path

import pytest

from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.control import Progress, RunningTime
from brewberrypi.goals import (
    Goal,
    GoalContext,
    HeatUp,
    IodineTest,
    Lautering,
    MashIn,
    PourInBrewingWater,
    Rest,
    create_goal,
)
from brewberrypi.hal.mock import MockHal
from brewberrypi.operator_requests import (
    ConfirmationRequest,
    InvalidResponse,
    RequestReminder,
    YesNoDecisionRequest,
)
from brewberrypi.recipes import GoalDefinition, load_recipes

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
REMINDER_INTERVAL = CONFIG.ui.request_reminder_interval_seconds


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def hal(clock: ManualClock) -> MockHal:
    return MockHal(clock, temperature=20.0)


@pytest.fixture
def context(hal: MockHal, clock: ManualClock) -> GoalContext:
    return GoalContext(hal=hal, control=CONFIG.control, running_time=RunningTime(clock))


@pytest.fixture
def reminder(hal: MockHal, clock: ManualClock) -> RequestReminder:
    return RequestReminder(hal, clock, REMINDER_INTERVAL)


def activate(goal: Goal) -> Goal:
    goal.activate()
    return goal


# --- goal texts and creation (spec 4.1, DOM-011, GOAL-100) ---


@pytest.mark.parametrize(
    ("definition", "name", "description"),
    [
        (GoalDefinition("PourInBrewingWater"), "Add Water", "Pour in the brewing water."),
        (GoalDefinition("HeatUp", temperature=62), "Heat Up", "Heat up mash to 62.0 degrees (C)."),
        (GoalDefinition("MashIn"), "Mash In", "Now it's your job to mash in the malt."),
        (GoalDefinition("Rest", temperature=62, minutes=10), "Rest", "Rest at 62.0 degrees (C) for 10min."),
        (
            GoalDefinition("Rest", temperature=62, minutes=30, name="Verzuckerungsrast", description="V @62"),
            "Verzuckerungsrast",
            "V @62",
        ),
        (
            GoalDefinition("IodineTest", temperature=62, minutes=10),
            "Iodine Test",
            "Now it's your job to do the iodine test.",
        ),
        (GoalDefinition("Lautering"), "Lautering", "Now it's your job to lauter."),
        (GoalDefinition("HeatUpForHopsCooking"), "Heat Up", "Heat up mash for cooking"),
        (
            GoalDefinition(
                "CookWater", minutes=20, name="Cooking Water", description="Cooking water for 20min"
            ),
            "Cooking Water",
            "Cooking water for 20min",
        ),
    ],
)
def test_goal_names_and_descriptions(
    context: GoalContext, definition: GoalDefinition, name: str, description: str
) -> None:
    goal = create_goal(definition, context)
    assert (goal.name, goal.description) == (name, description)
    assert goal.status == "Waiting"
    assert goal.active_request is None
    assert goal.progress == Progress(0, "0%")


def test_every_catalogue_goal_can_be_created(context: GoalContext) -> None:
    catalogue = load_recipes(CONFIG.recipes)
    for recipe in catalogue:
        goals = [create_goal(definition, context) for definition in recipe.goals]
        assert len(goals) == len(recipe.goals)
    beniweiss = catalogue.get("BENIWEISS")
    assert beniweiss is not None
    rest = create_goal(beniweiss.goals[4], context)
    assert (rest.name, rest.description) == ("Eiweißrast", "Eiweißrast @52 for 30min")


def test_DOM_011_every_creation_is_a_fresh_instance(context: GoalContext) -> None:
    definition = GoalDefinition("Rest", temperature=62, minutes=10)
    assert create_goal(definition, context) is not create_goal(definition, context)


def test_cook_water_and_hops_cooking_have_fixed_targets(context: GoalContext) -> None:
    cook = create_goal(GoalDefinition("CookWater", minutes=20, name="C", description="D"), context)
    hops = create_goal(GoalDefinition("HeatUpForHopsCooking"), context)
    assert isinstance(cook, Rest)
    assert cook.temperature == 100.0
    assert isinstance(hops, HeatUp)
    assert hops.temperature == 97.0


# --- goal state and default progress (DOM-021..023) ---


def test_DOM_022_default_progress() -> None:
    goal = PourInBrewingWater()
    assert goal.progress == Progress(0, "0%")
    goal.activate()
    assert (goal.status, goal.status_percent, goal.status_message) == ("Active", 0, "0%")
    goal.complete()
    assert (goal.status, goal.status_percent, goal.status_message) == ("Done", 100, "100%")


def test_DOM_023_completing_a_goal_removes_its_request() -> None:
    goal = activate(PourInBrewingWater())
    goal.pursue()
    assert goal.active_request is not None
    goal.complete()
    assert goal.active_request is None


# --- requests (DOM-030..032) ---


def test_DOM_031_request_options() -> None:
    assert ConfirmationRequest("x").options == ("Confirm",)
    assert YesNoDecisionRequest("x").options == ("Yes", "No")


def test_AC_051_answers_are_case_sensitive() -> None:
    request = ConfirmationRequest("Pour in brewing water")
    with pytest.raises(InvalidResponse) as error:
        request.respond("confirm")
    assert str(error.value) == "Response option 'confirm' is not valid."
    assert not request.answered
    request.respond("Confirm")
    assert request.confirmed


def test_yes_no_answers() -> None:
    request = YesNoDecisionRequest("Iodine test successfull?")
    request.respond("No")
    assert (request.yes, request.no) == (False, True)
    request.respond("Yes")  # a later answer replaces an unprocessed one
    assert (request.yes, request.no) == (True, False)
    with pytest.raises(InvalidResponse):
        request.respond("Maybe")


# --- confirmation goals (GOAL-001, GOAL-003, GOAL-006) and reminders (DOM-033) ---


async def test_AC_050_pour_in_water_request_reminds_until_confirmed(
    hal: MockHal, clock: ManualClock, reminder: RequestReminder
) -> None:
    goal = activate(PourInBrewingWater())
    assert goal.pursue() is False
    request = goal.active_request
    assert isinstance(request, ConfirmationRequest)
    assert (request.message, request.options) == ("Pour in brewing water", ("Confirm",))

    reminder.follow(request)
    assert hal.beeps() == ["long_beep"]  # immediately
    await clock.advance(10)
    await clock.advance(10)
    assert hal.beeps() == ["long_beep"] * 3  # and every 10 s

    request.respond("Confirm")
    reminder.follow(goal.active_request)
    assert goal.pursue() is True  # Done on the next tick
    await clock.advance(60)
    assert hal.beeps() == ["long_beep"] * 3  # reminders stopped
    assert not reminder.running


def test_GOAL_001_confirmation_goals_do_not_touch_the_actuators(hal: MockHal) -> None:
    hal.switch_on_pump()
    hal.set_heating(0.5)
    hal.clear_events()
    for goal in (activate(PourInBrewingWater()), activate(MashIn())):
        goal.pursue()
        goal.pursue()
    assert hal.events == []


def test_AC_055_lautering_switches_everything_off_on_every_tick(hal: MockHal) -> None:
    goal = activate(Lautering(hal))
    for _ in range(3):
        hal.switch_on_pump()
        hal.set_heating(1.0)
        assert goal.pursue() is False
        assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)
    assert goal.active_request is not None
    assert goal.active_request.message == "Lauter now"
    goal.active_request.respond("Confirm")
    assert goal.pursue() is True
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)


def test_AC_056_mash_in_after_heat_up_holds_no_temperature(hal: MockHal, context: GoalContext) -> None:
    heat_up = activate(HeatUp(context, 40.0))
    hal.set_temperature(40.0)
    assert heat_up.pursue() is True
    heat_up.complete()
    mash_in = activate(MashIn())
    hal.set_temperature(30.0)  # cold malt goes in
    for _ in range(5):
        assert mash_in.pursue() is False
    assert mash_in.active_request is not None
    assert mash_in.active_request.message == "Mash in now"
    assert hal.get_heating_status() == 0.0  # no temperature control
    assert hal.get_pump_status() is True  # pump stays on from HeatUp


async def test_AC_057_reminder_stops_when_the_engine_stops_following(
    hal: MockHal, clock: ManualClock, reminder: RequestReminder
) -> None:
    goal = activate(MashIn())
    goal.pursue()
    reminder.follow(goal.active_request)
    await clock.advance(25)
    reminder.follow(None)  # stop, pause or unload
    beeps = len(hal.beeps())
    await clock.advance(120)
    assert len(hal.beeps()) == beeps == 3
    assert not reminder.running
    assert clock.sleepers == 0


async def test_DOM_033_there_is_never_more_than_one_reminder_loop(
    hal: MockHal, clock: ManualClock, reminder: RequestReminder
) -> None:
    request = ConfirmationRequest("Mash in now")
    for _ in range(5):
        reminder.follow(request)  # following the same request again changes nothing
    assert clock.sleepers <= 1
    await clock.advance(10)
    assert hal.beeps() == ["long_beep"] * 2
    reminder.follow(ConfirmationRequest("Lauter now"))  # a new request restarts with an immediate beep
    await clock.advance(5)
    assert hal.beeps() == ["long_beep"] * 3
    await clock.advance(5)
    assert hal.beeps() == ["long_beep"] * 4
    assert clock.sleepers == 1
    reminder.stop()


# --- temperature goals: goal-level parts of AC-004..007 ---


def test_AC_004_heat_up_for_hops_cooking_is_done_at_97_with_the_heater_on(
    hal: MockHal, context: GoalContext
) -> None:
    goal = activate(create_goal(GoalDefinition("HeatUpForHopsCooking"), context))
    hal.set_temperature(96.9)
    assert goal.pursue() is False
    assert hal.get_heating_status() == 1.0
    hal.set_temperature(97.0)
    assert goal.pursue() is True
    assert hal.get_heating_status() == 1.0


@pytest.mark.parametrize(
    "definition",
    [
        GoalDefinition("HeatUp", temperature=62),
        GoalDefinition("Rest", temperature=62, minutes=10),
        GoalDefinition("IodineTest", temperature=62, minutes=10),
        GoalDefinition("HeatUpForHopsCooking"),
        GoalDefinition("CookWater", minutes=20, name="Cooking Water", description="Cooking water for 20min"),
    ],
)
def test_AC_005_temperature_goals_switch_off_and_show_sensor_error(
    hal: MockHal, context: GoalContext, definition: GoalDefinition
) -> None:
    goal = activate(create_goal(definition, context))
    hal.set_temperature(50.0)
    goal.pursue()
    assert hal.get_heating_status() == 1.0
    hal.set_temperature(None)
    goal.pursue()
    assert hal.get_heating_status() == 0.0
    assert goal.status_message == "Sensor error"
    hal.set_temperature(50.0)
    goal.pursue()
    assert hal.get_heating_status() == 1.0
    assert goal.status_message != "Sensor error"


def test_AC_006_heat_up_progress(hal: MockHal, context: GoalContext) -> None:
    goal = activate(HeatUp(context, 62.0))
    goal.pursue()
    hal.set_temperature(41.0)
    goal.pursue()
    assert (goal.status_percent, goal.status_message) == (50, "50%")


def test_AC_006_heat_up_started_above_the_target_is_done_on_the_first_tick(
    hal: MockHal, context: GoalContext
) -> None:
    goal = activate(HeatUp(context, 62.0))
    hal.set_temperature(62.5)
    assert goal.pursue() is True
    assert goal.status_percent == 100
    goal.complete()
    assert goal.progress == Progress(100, "100%")


async def test_AC_007_rest_progress_and_done(hal: MockHal, clock: ManualClock, context: GoalContext) -> None:
    goal = activate(Rest(context, 62.0, 10))
    hal.set_temperature(62.0)
    assert goal.pursue() is False
    await clock.advance(4 * 60)
    assert goal.pursue() is False
    assert (goal.status_percent, goal.status_message) == (40, "06:00 left")
    await clock.advance(6 * 60)
    assert goal.pursue() is True
    assert goal.status_message == "00:00 left"


async def test_GOAL_004_rest_countdown_starts_on_the_first_tick_regardless_of_temperature(
    hal: MockHal, clock: ManualClock, context: GoalContext
) -> None:
    goal = activate(Rest(context, 62.0, 1))
    hal.set_temperature(50.0)
    goal.pursue()
    assert hal.get_heating_status() == 1.0
    await clock.advance(60)
    assert goal.pursue() is True


# --- iodine test (GOAL-005, AC-053, AC-054, Q-23) ---


async def test_AC_053_iodine_no_rests_again_and_asks_again(
    hal: MockHal, clock: ManualClock, context: GoalContext, reminder: RequestReminder
) -> None:
    goal = activate(IodineTest(context, 25.0, 1))
    hal.set_temperature(25.0)
    goal.pursue()
    first = goal.active_request
    assert isinstance(first, YesNoDecisionRequest)
    assert (first.message, first.options) == ("Iodine test successfull?", ("Yes", "No"))
    reminder.follow(first)
    assert goal.progress == Progress(0, "Waiting for answer")

    first.respond("No")
    reminder.follow(goal.active_request)
    assert goal.pursue() is False
    assert goal.active_request is None
    assert goal.description == "Now it's your job to do the iodine test.\nRest at 25.0 degrees (C) for 1min."
    beeps = len(hal.beeps())

    goal.pursue()  # first tick of the additional rest starts the countdown
    await clock.advance(30)
    hal.set_temperature(24.8)
    goal.pursue()
    assert hal.get_heating_status() == 0.5  # temperature held at 25
    assert goal.progress == Progress(50, "00:30 left")
    await clock.advance(30)
    assert goal.pursue() is False  # rest over, back to asking
    assert len(hal.beeps()) == beeps  # no reminders during the additional rest

    goal.pursue()
    second = goal.active_request
    assert isinstance(second, YesNoDecisionRequest)
    assert second is not first
    reminder.follow(second)
    assert len(hal.beeps()) == beeps + 1  # new reminders
    assert goal.progress == Progress(0, "Waiting for answer")

    second.respond("Yes")
    assert goal.pursue() is True
    goal.complete()
    reminder.follow(goal.active_request)
    assert goal.progress == Progress(100, "100%")  # Q-23
    assert not reminder.running


async def test_GOAL_005_every_no_appends_the_rest_line_again(
    hal: MockHal, clock: ManualClock, context: GoalContext
) -> None:
    goal = activate(IodineTest(context, 25.0, 0))
    hal.set_temperature(25.0)
    for _ in range(2):
        goal.pursue()
        assert goal.active_request is not None
        goal.active_request.respond("No")
        goal.pursue()  # No: start the additional rest
        goal.pursue()  # 0 min: over at once, back to asking
    line = "\nRest at 25.0 degrees (C) for 0min."
    assert goal.description == "Now it's your job to do the iodine test." + line + line


def test_AC_054_iodine_question_holds_the_temperature(hal: MockHal, context: GoalContext) -> None:
    goal = activate(IodineTest(context, 25.0, 1))
    hal.set_heating(1.0)  # left on by the previous rest (FIX-13)
    hal.set_temperature(25.0)
    goal.pursue()
    assert hal.get_heating_status() == 0.0
    hal.set_temperature(24.95)
    goal.pursue()
    assert hal.get_heating_status() == 0.0  # switch point 24.9
    hal.set_temperature(24.85)
    goal.pursue()
    assert hal.get_heating_status() == 0.5
    hal.set_temperature(25.0)
    goal.pursue()
    assert hal.get_heating_status() == 0.0
    assert goal.active_request is not None


def test_Q23_iodine_progress_when_not_active(context: GoalContext) -> None:
    goal = IodineTest(context, 62.0, 10)
    assert goal.progress == Progress(0, "0%")
    goal.activate()
    assert goal.progress == Progress(0, "Waiting for answer")
    goal.complete()
    assert goal.progress == Progress(100, "100%")
