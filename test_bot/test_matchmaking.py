"""Test functions for matchmaking module."""
from unittest.mock import Mock
from lib.matchmaking import game_category, Matchmaking, MatchmakingSlots, configured_time_controls
from lib.config import Configuration
from lib.lichess_types import UserProfileType


def test_game_category_standard_bullet() -> None:
    """Test bullet time control with config values."""
    # challenge_initial_time: 60 (1 min), challenge_increment: 1
    # 60 + 1*40 = 100 seconds < 179 = bullet
    assert game_category("standard", 60, 1, 0) == "bullet"

    # challenge_initial_time: 60, challenge_increment: 2
    # 60 + 2*40 = 140 seconds < 179 = bullet
    assert game_category("standard", 60, 2, 0) == "bullet"


def test_game_category_standard_blitz() -> None:
    """Test blitz time control with config values."""
    # challenge_initial_time: 180 (3 min), challenge_increment: 1
    # 180 + 1*40 = 220 seconds, 179 <= 220 < 479 = blitz
    assert game_category("standard", 180, 1, 0) == "blitz"

    # challenge_initial_time: 180, challenge_increment: 2
    # 180 + 2*40 = 260 seconds, 179 <= 260 < 479 = blitz
    assert game_category("standard", 180, 2, 0) == "blitz"


def test_game_category_standard_rapid() -> None:
    """Test rapid time control."""
    # 10 minutes + 5 seconds increment
    # 600 + 5*40 = 800 seconds, 479 <= 800 < 1499 = rapid
    assert game_category("standard", 600, 5, 0) == "rapid"

    # 15 minutes no increment
    # 900 + 0*40 = 900 seconds, 479 <= 900 < 1499 = rapid
    assert game_category("standard", 900, 0, 0) == "rapid"


def test_game_category_standard_classical() -> None:
    """Test classical time control with max config values."""
    # max_base: 1800 (30 min), max_increment: 20
    # 1800 + 20*40 = 2600 seconds >= 1499 = classical
    assert game_category("standard", 1800, 20, 0) == "classical"

    # 25 minutes no increment
    # 1500 + 0*40 = 1500 seconds >= 1499 = classical
    assert game_category("standard", 1500, 0, 0) == "classical"


def test_game_category_correspondence() -> None:
    """Test correspondence games with config values."""
    # min_days: 1
    assert game_category("standard", 0, 0, 1) == "correspondence"

    # challenge_days: 2
    assert game_category("standard", 0, 0, 2) == "correspondence"

    # max_days: 14
    assert game_category("standard", 0, 0, 14) == "correspondence"


def test_game_category_variants() -> None:
    """Test chess variants from config."""
    assert game_category("atomic", 60, 1, 0) == "atomic"
    assert game_category("chess960", 180, 2, 0) == "chess960"
    assert game_category("crazyhouse", 600, 5, 0) == "crazyhouse"
    assert game_category("horde", 60, 0, 0) == "horde"
    assert game_category("kingOfTheHill", 180, 1, 0) == "kingOfTheHill"
    assert game_category("racingKings", 600, 0, 0) == "racingKings"
    assert game_category("threeCheck", 60, 1, 0) == "threeCheck"
    assert game_category("antichess", 180, 2, 0) == "antichess"


def test_game_category_time_boundaries() -> None:
    """Test edge cases at time control boundaries."""
    # Exactly at bullet/blitz boundary
    # 179 seconds should be blitz (179 < 179 is False)
    assert game_category("standard", 179, 0, 0) == "blitz"

    # Just below boundary
    assert game_category("standard", 178, 0, 0) == "bullet"

    # Exactly at blitz/rapid boundary
    assert game_category("standard", 479, 0, 0) == "rapid"

    # Just below
    assert game_category("standard", 478, 0, 0) == "blitz"

    # Exactly at rapid/classical boundary
    assert game_category("standard", 1499, 0, 0) == "classical"

    # Just below
    assert game_category("standard", 1498, 0, 0) == "rapid"


def test_game_category_min_config_values() -> None:
    """Test minimum config values."""
    # min_base: 0, min_increment: 0
    # This is an edge case: 0 + 0*40 = 0 < 179 = bullet
    assert game_category("standard", 0, 0, 0) == "bullet"

    # min_base: 0, min_increment: 0, min_days: 1
    assert game_category("standard", 0, 0, 1) == "correspondence"


def test_game_category_correspondence_overrides_time() -> None:
    """Test that correspondence takes precedence over time controls."""
    # If both days and time controls are set, days takes precedence
    assert game_category("standard", 1800, 20, 1) == "correspondence"
    assert game_category("standard", 60, 1, 2) == "correspondence"


def test_game_category_variant_overrides_time() -> None:
    """Test that variants override time control categorization."""
    # Variants are returned regardless of time control
    # Even if time would be "classical", variant name is returned
    assert game_category("atomic", 1800, 20, 0) == "atomic"
    assert game_category("horde", 60, 1, 0) == "horde"

    # Variants override correspondence too
    assert game_category("chess960", 0, 0, 14) == "chess960"


def test_game_category_negative_values() -> None:
    """Test edge case with negative values (should not happen in practice)."""
    # Negative base time
    assert game_category("standard", -100, 5, 0) == "bullet"

    # Negative increment results in negative duration
    result = game_category("standard", 100, -10, 0)
    # 100 + (-10)*40 = -300, which is < 179, so bullet
    assert result == "bullet"


def test_game_category_realistic_scenarios() -> None:
    """Test realistic game scenarios from actual lichess games."""
    # 1+0 bullet
    assert game_category("standard", 60, 0, 0) == "bullet"

    # 2+1 bullet
    assert game_category("standard", 120, 1, 0) == "bullet"

    # 3+0 blitz
    assert game_category("standard", 180, 0, 0) == "blitz"

    # 3+2 blitz
    assert game_category("standard", 180, 2, 0) == "blitz"

    # 5+0 blitz
    assert game_category("standard", 300, 0, 0) == "blitz"

    # 5+3 blitz
    assert game_category("standard", 300, 3, 0) == "blitz"

    # 10+0 rapid
    assert game_category("standard", 600, 0, 0) == "rapid"

    # 15+5 rapid
    assert game_category("standard", 900, 5, 0) == "rapid"

    # 15+10 rapid
    assert game_category("standard", 900, 10, 0) == "rapid"

    # 30+0 classical
    assert game_category("standard", 1800, 0, 0) == "classical"

    # 30+20 classical
    assert game_category("standard", 1800, 20, 0) == "classical"


def test_get_random_config_value__returns_specific_value() -> None:
    """Test that get_random_config_value returns the config value when it's not 'random'."""
    # Create mock objects
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "challenge_timeout": 30
        }
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}

    # Create matchmaking instance
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)

    # Create config with a specific value
    test_config = Configuration({"challenge_variant": "atomic"})

    # Test that it returns the specific value, not a random choice
    choices = ["standard", "chess960", "atomic", "horde"]
    result = matchmaking.get_random_config_value(test_config, "challenge_variant", choices)

    assert result == "atomic", f"Expected 'atomic' but got '{result}'"


def test_get_random_config_value__returns_from_choices_when_random() -> None:
    """Test that get_random_config_value returns a value from choices when config value is 'random'."""
    # Create mock objects
    mock_li = Mock()
    mock_config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": False,
            "block_list": [],
            "online_block_list": [],
            "challenge_timeout": 30
        }
    })
    mock_user_profile: UserProfileType = {"username": "testbot", "perfs": {}}

    # Create matchmaking instance
    matchmaking = Matchmaking(mock_li, mock_config, mock_user_profile)

    # Create config with "random" value
    test_config = Configuration({"challenge_mode": "random"})

    # Test that it returns one of the choices
    choices = ["casual", "rated"]
    result = matchmaking.get_random_config_value(test_config, "challenge_mode", choices)

    assert result in choices, f"Expected result to be in {choices} but got '{result}'"


def test_configured_time_controls__treats_correspondence_as_a_normal_control() -> None:
    """Test that correspondence is sampled from the same time-control pool as real-time controls."""
    match_config = Configuration({
        "challenge_initial_time": [180, 300],
        "challenge_increment": [2, 0],
        "challenge_days": [1]
    })

    controls = configured_time_controls(match_config)
    expected = {(180, 2, 0), (180, 0, 0), (300, 2, 0), (300, 0, 0), (0, 0, 1)}

    assert len(controls) == 5
    assert set(controls) == expected


def test_configured_time_controls__filters_by_slot_lane() -> None:
    """Test filtering controls for short-vs-long matchmaking slots."""
    match_config = Configuration({
        "challenge_initial_time": [60, 600],
        "challenge_increment": [0],
        "challenge_days": [1]
    })

    short_controls = configured_time_controls(match_config, {"short"})
    long_controls = configured_time_controls(match_config, {"long"})

    assert short_controls == [(60, 0, 0)]
    assert set(long_controls) == {(600, 0, 0), (0, 0, 1)}


def test_matchmaking_slots__enforces_two_bot_lanes_when_concurrency_is_three() -> None:
    """Test that with concurrency=3 we only allow one short and one long bot lane."""
    slots = MatchmakingSlots(3)
    active_games: set[str] = set()

    assert slots.available_bot_lanes(active_games) == {"short", "long"}

    slots.reserve_outgoing_challenge("short_pending", "blitz")
    assert slots.can_accept_bot_speed("blitz", active_games) is False
    assert slots.can_accept_bot_speed("rapid", active_games) is True

    slots.reserve_outgoing_challenge("long_pending", "rapid")
    assert slots.can_accept_bot_speed("rapid", active_games) is False
    assert slots.can_accept_bot_speed("correspondence", active_games) is True

    slots.reserve_outgoing_challenge("corr_pending", "correspondence")
    assert slots.can_accept_bot_speed("correspondence", active_games) is True
    assert slots.can_accept_human(active_games) is True

    active_games.add("human_game")
    assert slots.can_accept_human(active_games) is False


def test_matchmaking_slots__correspondence_move_uses_bot_lane_capacity() -> None:
    """Test that correspondence moves only run when a short/long bot lane is available."""
    slots = MatchmakingSlots(3)
    active_games: set[str] = {"bot_short_game", "bot_long_game"}
    slots.reserve_game("bot_short_game", is_bot_game=True, speed="bullet")
    slots.reserve_game("bot_long_game", is_bot_game=True, speed="rapid")

    # Human slot is still free (2/3 cores used), but both bot lanes are occupied.
    assert slots.can_start_correspondence_move(active_games) is False

    active_games.remove("bot_long_game")
    slots.release("bot_long_game")
    assert slots.can_start_correspondence_move(active_games) is True


def test_matchmaking_challenge__fills_open_lane_quickly_in_slot_mode() -> None:
    """Test that slot mode fills the missing short/long bot lane without the long active-game cooldown."""
    li = Mock()
    config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": True,
            "allow_during_games": True,
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "challenge_timeout": 1,
            "challenge_initial_time": [60, 600],
            "challenge_increment": [0],
            "challenge_days": [1],
            "opponent_min_rating": 600,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
            "rating_preference": "none",
            "challenge_filter": "fine",
            "block_list": [],
            "online_block_list": [],
            "overrides": {}
        }
    })
    user_profile: UserProfileType = {"username": "test_bot", "perfs": {}}
    matchmaking = Matchmaking(li, config, user_profile)
    slots = MatchmakingSlots(3)
    matchmaking.set_slots(slots)

    active_games = {"long_game"}
    slots.reserve_game("long_game", is_bot_game=True, speed="rapid")
    slots.reserve_game("corr_game", is_bot_game=True, speed="correspondence")

    matchmaking.should_create_challenge = Mock(return_value=True)  # type: ignore[method-assign]
    matchmaking.update_user_profile = Mock()  # type: ignore[method-assign]
    matchmaking.choose_opponent = Mock(return_value=("other_bot", 60, 0, 0, "standard", "rated"))  # type: ignore[method-assign]
    matchmaking.create_challenge = Mock(return_value="new_short_lane")  # type: ignore[method-assign]

    matchmaking.last_challenge_created_delay.reset()
    matchmaking.last_challenge_created_delay.starting_time -= 120

    matchmaking.challenge(active_games, [], 3)

    matchmaking.choose_opponent.assert_called_once_with({"short"}, correspondence_only=False)
    matchmaking.create_challenge.assert_called_once_with("other_bot", 60, 0, 0, "standard", "rated")


def test_matchmaking_challenge__creates_correspondence_even_when_realtime_is_full() -> None:
    """Test that slot mode still creates a correspondence challenge while all realtime cores are occupied."""
    li = Mock()
    config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": True,
            "allow_during_games": True,
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "challenge_timeout": 1,
            "challenge_initial_time": [60, 600],
            "challenge_increment": [0],
            "challenge_days": [1],
            "opponent_min_rating": 600,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
            "rating_preference": "none",
            "challenge_filter": "fine",
            "block_list": [],
            "online_block_list": [],
            "overrides": {}
        }
    })
    user_profile: UserProfileType = {"username": "test_bot", "perfs": {}}
    matchmaking = Matchmaking(li, config, user_profile)
    slots = MatchmakingSlots(3)
    matchmaking.set_slots(slots)

    active_games = {"bot_short", "bot_long", "human"}
    slots.reserve_game("bot_short", is_bot_game=True, speed="blitz")
    slots.reserve_game("bot_long", is_bot_game=True, speed="rapid")
    slots.reserve_game("human", is_bot_game=False, speed="blitz")

    matchmaking.should_create_challenge = Mock(return_value=True)  # type: ignore[method-assign]
    matchmaking.update_user_profile = Mock()  # type: ignore[method-assign]
    matchmaking.choose_opponent = Mock(return_value=("other_bot", 0, 0, 1, "standard", "rated"))  # type: ignore[method-assign]
    matchmaking.create_challenge = Mock(return_value="new_corr_lane")  # type: ignore[method-assign]

    matchmaking.last_challenge_created_delay.reset()
    matchmaking.last_challenge_created_delay.starting_time -= 120

    matchmaking.challenge(active_games, [], 3)

    matchmaking.choose_opponent.assert_called_once_with(None, correspondence_only=True)
    matchmaking.create_challenge.assert_called_once_with("other_bot", 0, 0, 1, "standard", "rated")


def test_matchmaking_challenge__respects_max_background_correspondence_games() -> None:
    """Test that outgoing correspondence challenges stop at the configured target."""
    li = Mock()
    config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": True,
            "allow_during_games": True,
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "challenge_timeout": 1,
            "challenge_initial_time": [60],
            "challenge_increment": [0],
            "challenge_days": [1],
            "max_background_correspondence_games": 2,
            "opponent_min_rating": 600,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
            "rating_preference": "none",
            "challenge_filter": "fine",
            "block_list": [],
            "online_block_list": [],
            "overrides": {}
        }
    })
    user_profile: UserProfileType = {"username": "test_bot", "perfs": {}}
    matchmaking = Matchmaking(li, config, user_profile)
    slots = MatchmakingSlots(3)
    matchmaking.set_slots(slots)

    active_games = {"bot_short", "bot_long", "human"}
    slots.reserve_game("bot_short", is_bot_game=True, speed="blitz")
    slots.reserve_game("bot_long", is_bot_game=True, speed="rapid")
    slots.reserve_game("human", is_bot_game=False, speed="blitz")
    slots.reserve_game("corr_one", is_bot_game=True, speed="correspondence")
    slots.reserve_game("corr_two", is_bot_game=True, speed="correspondence")

    matchmaking.should_create_challenge = Mock(return_value=True)  # type: ignore[method-assign]
    matchmaking.update_user_profile = Mock()  # type: ignore[method-assign]
    matchmaking.choose_opponent = Mock(return_value=("other_bot", 0, 0, 1, "standard", "rated"))  # type: ignore[method-assign]

    matchmaking.challenge(active_games, [], 3)

    matchmaking.choose_opponent.assert_not_called()


def test_matchmaking_challenge__can_force_immediate_correspondence_replacement() -> None:
    """Test that a finished correspondence game can trigger an immediate replacement challenge."""
    li = Mock()
    config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": True,
            "allow_during_games": True,
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "challenge_timeout": 1,
            "challenge_initial_time": [60],
            "challenge_increment": [0],
            "challenge_days": [1],
            "opponent_min_rating": 600,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
            "rating_preference": "none",
            "challenge_filter": "fine",
            "block_list": [],
            "online_block_list": [],
            "overrides": {}
        }
    })
    user_profile: UserProfileType = {"username": "test_bot", "perfs": {}}
    matchmaking = Matchmaking(li, config, user_profile)
    slots = MatchmakingSlots(3)
    matchmaking.set_slots(slots)

    matchmaking.should_create_challenge = Mock(return_value=True)  # type: ignore[method-assign]
    matchmaking.create_matchmaking_challenge = Mock(return_value=True)  # type: ignore[method-assign]

    matchmaking.correspondence_game_done()
    matchmaking.challenge({"bot_short", "bot_long", "human"}, [], 3)

    matchmaking.should_create_challenge.assert_called_once_with(ignore_postgame_timeout=True, ignore_min_wait=True)


def test_matchmaking_challenge__keeps_long_cooldown_without_slot_mode() -> None:
    """Test that non-slot mode still waits up to max_wait_time between active-game challenges."""
    li = Mock()
    config = Configuration({
        "challenge": {"variants": ["standard"]},
        "matchmaking": {
            "allow_matchmaking": True,
            "allow_during_games": True,
            "challenge_variant": "standard",
            "challenge_mode": "rated",
            "challenge_timeout": 1,
            "challenge_initial_time": [60, 600],
            "challenge_increment": [0],
            "challenge_days": [],
            "opponent_min_rating": 600,
            "opponent_max_rating": 4000,
            "opponent_rating_difference": None,
            "rating_preference": "none",
            "challenge_filter": "fine",
            "block_list": [],
            "online_block_list": [],
            "overrides": {}
        }
    })
    user_profile: UserProfileType = {"username": "test_bot", "perfs": {}}
    matchmaking = Matchmaking(li, config, user_profile)

    matchmaking.should_create_challenge = Mock(return_value=True)  # type: ignore[method-assign]
    matchmaking.update_user_profile = Mock()  # type: ignore[method-assign]
    matchmaking.choose_opponent = Mock(return_value=("other_bot", 60, 0, 0, "standard", "rated"))  # type: ignore[method-assign]

    matchmaking.last_challenge_created_delay.reset()
    matchmaking.last_challenge_created_delay.starting_time -= 120

    matchmaking.challenge({"active_game"}, [], 3)

    matchmaking.choose_opponent.assert_not_called()
