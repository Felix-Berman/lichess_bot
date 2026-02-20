"""Tests for slot-aware challenge acceptance."""
from unittest.mock import Mock
from lib import lichess_bot
from lib.matchmaking import MatchmakingSlots


class FakeChallenger:
    """Minimal challenger object for testing."""

    def __init__(self, is_bot: bool) -> None:
        self.is_bot = is_bot


class FakeChallenge:
    """Minimal challenge object for testing accept_challenges()."""

    def __init__(self, challenge_id: str, is_bot: bool, speed: str) -> None:
        self.id = challenge_id
        self.from_self = False
        self.speed = speed
        self.challenger = FakeChallenger(is_bot)

    def __str__(self) -> str:
        return self.id


def test_accept_challenges__prioritises_humans() -> None:
    """Test that a human challenge is accepted before bot challenges."""
    li = Mock()
    challenge_queue = [
        FakeChallenge("bot_long", is_bot=True, speed="rapid"),
        FakeChallenge("human", is_bot=False, speed="blitz"),
        FakeChallenge("bot_short", is_bot=True, speed="blitz"),
    ]
    active_games: set[str] = set()
    slots = MatchmakingSlots(3)

    lichess_bot.accept_challenges(li, challenge_queue, active_games, 3, slots)

    accepted_ids = [call.args[0] for call in li.accept_challenge.call_args_list]
    assert accepted_ids[0] == "human"


def test_accept_challenges__keeps_one_short_and_one_long_bot_slot() -> None:
    """Test that only one short and one long bot challenge are accepted."""
    li = Mock()
    challenge_queue = [
        FakeChallenge("bot_long_one", is_bot=True, speed="rapid"),
        FakeChallenge("bot_long_two", is_bot=True, speed="classical"),
        FakeChallenge("bot_short", is_bot=True, speed="bullet"),
    ]
    active_games: set[str] = set()
    slots = MatchmakingSlots(3)

    lichess_bot.accept_challenges(li, challenge_queue, active_games, 3, slots)

    accepted_ids = [call.args[0] for call in li.accept_challenge.call_args_list]
    assert accepted_ids == ["bot_long_one", "bot_short"]
    assert [challenge.id for challenge in challenge_queue] == ["bot_long_two"]
