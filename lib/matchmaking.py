"""Challenge other bots."""
import random
import logging
import datetime
import contextlib
import math
from dataclasses import dataclass, field
from lib import model
from lib.timer import Timer, days, seconds, minutes, years
from collections import defaultdict
from collections.abc import Sequence
from lib.lichess import Lichess, RateLimitedError
from lib.config import Configuration
from typing import cast, TypeAlias
from lib.blocklist import OnlineBlocklist
from lib.lichess_types import UserProfileType, PerfType, EventType, FilterType, ChallengeType
MULTIPROCESSING_LIST_TYPE: TypeAlias = Sequence[model.Challenge]

logger = logging.getLogger(__name__)

BOT_SHORT_SPEEDS = {"ultraBullet", "bullet", "blitz"}
BOT_LONG_SPEEDS = {"rapid", "classical"}
CORRESPONDENCE_SPEED = "correspondence"


def bot_lane_for_speed(speed: str) -> str:
    """Classify a bot game speed as either short or long."""
    return "short" if speed in BOT_SHORT_SPEEDS else "long"


def is_correspondence_speed(speed: str) -> bool:
    """Whether a speed belongs to the correspondence lane."""
    return speed == CORRESPONDENCE_SPEED


def configured_time_controls(match_config: Configuration,
                             allowed_bot_lanes: set[str] | None = None,
                             *,
                             include_correspondence: bool = True) -> list[tuple[int, int, int]]:
    """Get all configured time controls as (base_time, increment, days)."""
    challenge_initial_time: list[int | None] = list(match_config.challenge_initial_time or [None])
    challenge_increment: list[int | None] = list(match_config.challenge_increment or [None])
    challenge_days: list[int | None] = list(match_config.challenge_days or [])
    challenge_initial_time = challenge_initial_time or [None]
    challenge_increment = challenge_increment or [None]

    time_controls: list[tuple[int, int, int]] = []
    for base in challenge_initial_time:
        for increment in challenge_increment:
            base_time = int(base or 0)
            inc_time = int(increment or 0)
            if not (base_time or inc_time):
                continue
            speed = game_category("standard", base_time, inc_time, 0)
            if allowed_bot_lanes is None or bot_lane_for_speed(speed) in allowed_bot_lanes:
                time_controls.append((base_time, inc_time, 0))

    if include_correspondence:
        for num_days_cfg in challenge_days:
            num_days = int(num_days_cfg or 0)
            if not num_days:
                continue
            if allowed_bot_lanes is None or "long" in allowed_bot_lanes:
                time_controls.append((0, 0, num_days))

    return time_controls


@dataclass
class MatchmakingSlots:
    """Track lane reservations for outgoing/incoming challenges and active games."""
    max_games: int
    enabled: bool = field(init=False)
    slot_by_game_id: dict[str, str] = field(init=False, default_factory=dict)
    pending_outgoing_challenges: set[str] = field(init=False, default_factory=set)
    pending_outgoing_correspondence: set[str] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self.enabled = self.max_games == 3

    def _slot_for_game(self, is_bot_game: bool, speed: str) -> str:
        if is_correspondence_speed(speed):
            return CORRESPONDENCE_SPEED
        if not self.enabled:
            return "any"
        if not is_bot_game:
            return "human"
        return f"bot_{bot_lane_for_speed(speed)}"

    def reserve_game(self, game_id: str, is_bot_game: bool, speed: str) -> None:
        """Reserve a lane for a game or accepted challenge."""
        if not self.enabled:
            return
        self.slot_by_game_id[game_id] = self._slot_for_game(is_bot_game, speed)
        self.pending_outgoing_challenges.discard(game_id)
        self.pending_outgoing_correspondence.discard(game_id)

    def reserve_outgoing_challenge(self, challenge_id: str, speed: str) -> None:
        """Reserve a lane for an outgoing challenge until it is accepted/declined/cancelled."""
        if not self.enabled:
            return
        self.slot_by_game_id[challenge_id] = self._slot_for_game(True, speed)
        if is_correspondence_speed(speed):
            self.pending_outgoing_correspondence.add(challenge_id)
        else:
            self.pending_outgoing_challenges.add(challenge_id)

    def confirm_game_start(self, game_id: str) -> None:
        """Convert a pending outgoing challenge reservation into an active game reservation."""
        if not self.enabled:
            return
        self.pending_outgoing_challenges.discard(game_id)
        self.pending_outgoing_correspondence.discard(game_id)

    def release(self, game_or_challenge_id: str) -> None:
        """Free a lane when a game ends or a challenge does not lead to a game."""
        if not self.enabled:
            return
        self.pending_outgoing_challenges.discard(game_or_challenge_id)
        self.pending_outgoing_correspondence.discard(game_or_challenge_id)
        self.slot_by_game_id.pop(game_or_challenge_id, None)

    def has_reservation(self, game_id: str) -> bool:
        """Whether a game/challenge currently has a tracked reservation."""
        if not self.enabled:
            return False
        return game_id in self.slot_by_game_id

    def used_slots(self, active_games: set[str]) -> int:
        """Count used slots, including outgoing challenges that are still pending."""
        if not self.enabled:
            return len(active_games)
        pending = sum(1 for challenge_id in self.pending_outgoing_challenges if challenge_id not in active_games)
        return len(active_games) + pending

    def has_correspondence_reservation(self) -> bool:
        """Whether we already have a correspondence game/challenge in progress."""
        if not self.enabled:
            return False
        return any(slot == CORRESPONDENCE_SPEED for slot in self.slot_by_game_id.values())

    def needs_correspondence_game(self) -> bool:
        """Whether matchmaking should look for a correspondence game."""
        return self.enabled and not self.has_correspondence_reservation()

    def correspondence_reservation_count(self) -> int:
        """Number of tracked correspondence games/challenges."""
        if not self.enabled:
            return 0
        return sum(1 for slot in self.slot_by_game_id.values() if slot == CORRESPONDENCE_SPEED)

    def is_correspondence(self, game_or_challenge_id: str) -> bool:
        """Whether a tracked id belongs to the correspondence lane."""
        if not self.enabled:
            return False
        return self.slot_by_game_id.get(game_or_challenge_id) == CORRESPONDENCE_SPEED

    def _bot_lane_counts(self) -> tuple[int, int]:
        if not self.enabled:
            return 0, 0
        short_count = sum(1 for slot in self.slot_by_game_id.values() if slot == "bot_short")
        long_count = sum(1 for slot in self.slot_by_game_id.values() if slot == "bot_long")
        return short_count, long_count

    def can_accept_human(self, active_games: set[str]) -> bool:
        """Whether a human challenge can be accepted now."""
        return self.used_slots(active_games) < self.max_games

    def can_accept_correspondence(self, active_games: set[str]) -> bool:
        """Whether a correspondence challenge can be accepted now."""
        if not self.enabled:
            return self.used_slots(active_games) < self.max_games
        return True

    def can_accept_bot_speed(self, speed: str, active_games: set[str]) -> bool:
        """Whether a bot challenge with this speed can be accepted now."""
        if is_correspondence_speed(speed):
            return self.can_accept_correspondence(active_games)
        if self.used_slots(active_games) >= self.max_games:
            return False
        if not self.enabled:
            return True
        short_count, long_count = self._bot_lane_counts()
        if short_count + long_count >= 2:
            return False
        lane = bot_lane_for_speed(speed)
        return (lane == "short" and short_count == 0) or (lane == "long" and long_count == 0)

    def can_accept_challenge(self, challenge: model.Challenge, active_games: set[str]) -> bool:
        """Whether an incoming challenge fits the slot policy."""
        if is_correspondence_speed(challenge.speed):
            return self.can_accept_correspondence(active_games)
        if challenge.challenger.is_bot:
            return self.can_accept_bot_speed(challenge.speed, active_games)
        return self.can_accept_human(active_games)

    def available_bot_lanes(self, active_games: set[str]) -> set[str]:
        """Return the bot lanes currently available for outgoing matchmaking."""
        if self.used_slots(active_games) >= self.max_games:
            return set()
        if not self.enabled:
            return {"short", "long"}
        short_count, long_count = self._bot_lane_counts()
        if short_count + long_count >= 2:
            return set()
        available_lanes: set[str] = set()
        if short_count == 0:
            available_lanes.add("short")
        if long_count == 0:
            available_lanes.add("long")
        return available_lanes

    def can_start_correspondence_move(self, active_games: set[str]) -> bool:
        """Whether a correspondence move may use a core right now."""
        if self.used_slots(active_games) >= self.max_games:
            return False
        if not self.enabled:
            return True
        return bool(self.available_bot_lanes(active_games))


class Matchmaking:
    """Challenge other bots."""

    def __init__(self, li: Lichess, config: Configuration, user_profile: UserProfileType) -> None:
        """Initialize values needed for matchmaking."""
        self.li = li
        self.variants = list(filter(lambda variant: variant != "fromPosition", config.challenge.variants))
        self.matchmaking_cfg = config.matchmaking
        self.user_profile = user_profile
        self.last_challenge_created_delay = Timer(seconds(25))  # Challenges expire after 20 seconds.
        self.last_game_ended_delay = Timer(minutes(self.matchmaking_cfg.challenge_timeout))
        self.last_user_profile_update_time = Timer(minutes(5))
        self.min_wait_time = seconds(60)  # Wait before new challenge to avoid api rate limits.
        self.rate_limit_timer = Timer()

        # Maximum time between challenges, even if there are active games
        self.max_wait_time = minutes(10) if self.matchmaking_cfg.allow_during_games else years(10)
        self.challenge_id = ""
        self.force_immediate_challenge = False
        self.max_background_correspondence_games = self._parse_max_background_correspondence_games()

        # (opponent name, game aspect) --> other bot is likely to accept challenge
        # game aspect is the one the challenged bot objects to and is one of:
        #   - game speed (bullet, blitz, etc.)
        #   - variant (standard, horde, etc.)
        #   - casual/rated
        #   - empty string (if no other reason is given or self.filter_type is COARSE)
        self.challenge_type_acceptable: defaultdict[tuple[str, str], Timer] = defaultdict(Timer)
        self.challenge_filter = self.matchmaking_cfg.challenge_filter

        for name in self.matchmaking_cfg.block_list:
            self.add_to_block_list(name)

        self.online_block_list = OnlineBlocklist(self.matchmaking_cfg.online_block_list)
        self.slots: MatchmakingSlots | None = None

    def set_slots(self, slots: MatchmakingSlots) -> None:
        """Attach the shared slot tracker used by matchmaking and challenge acceptance."""
        self.slots = slots

    def _parse_max_background_correspondence_games(self) -> int | float:
        """Read and normalize the outgoing correspondence target."""
        value = self.matchmaking_cfg.lookup("max_background_correspondence_games")
        if value is None:
            return 1
        if value == math.inf:
            return math.inf
        with contextlib.suppress(Exception):
            parsed_value = int(value)
            return max(0, parsed_value)
        return 1

    def should_create_challenge(self, *, ignore_postgame_timeout: bool = False,
                                ignore_min_wait: bool = False) -> bool:
        """Whether we should create a challenge."""
        matchmaking_enabled = self.matchmaking_cfg.allow_matchmaking
        postgame_ok = ignore_postgame_timeout or self.last_game_ended_delay.is_expired()
        time_has_passed = postgame_ok and self.rate_limit_timer.is_expired()
        challenge_expired = self.last_challenge_created_delay.is_expired() and self.challenge_id
        min_wait_time_passed = ignore_min_wait or self.last_challenge_created_delay.time_since_reset() > self.min_wait_time
        if challenge_expired:
            challenge_id = self.challenge_id
            self.li.cancel(challenge_id)
            logger.info(f"Challenge id {challenge_id} cancelled.")
            self.discard_challenge(challenge_id)
            if self.slots:
                self.slots.release(challenge_id)
            self.show_earliest_challenge_time()
        return bool(matchmaking_enabled and (time_has_passed or challenge_expired) and min_wait_time_passed)

    def create_challenge(self, username: str, base_time: int, increment: int, days: int, variant: str,
                         mode: str) -> str:
        """Create a challenge."""
        params: dict[str, str | int | bool] = {"rated": mode == "rated", "variant": variant}

        if days:
            params["days"] = days
        elif base_time or increment:
            params["clock.limit"] = base_time
            params["clock.increment"] = increment
        else:
            logger.error("At least one of challenge_days, challenge_initial_time, or challenge_increment "
                         "must be greater than zero in the matchmaking section of your config file.")
            return ""

        try:
            self.last_challenge_created_delay.reset()
            response = self.li.challenge(username, params)
            challenge_id = response.get("id", "")
            if not challenge_id:
                self.handle_challenge_error_response(response, username)
            return challenge_id
        except RateLimitedError as e:
            logger.warning(e)
            self.rate_limit_timer = Timer(e.timeout)
        except Exception as e:
            logger.debug(e, exc_info=e)

        logger.warning("Could not create challenge")
        self.show_earliest_challenge_time()
        return ""

    def handle_challenge_error_response(self, response: ChallengeType, username: str) -> None:
        """If a challenge fails, print the error and adjust the challenge requirements in response."""
        logger.error(response)
        if response.get("bot_is_rate_limited"):
            timeout = cast(datetime.timedelta, response.get("rate_limit_timeout"))
            self.rate_limit_timer = Timer(timeout)
        elif response.get("opponent_is_rate_limited"):
            self.add_challenge_filter(username, "", response.get("rate_limit_timeout"))
        else:
            self.add_challenge_filter(username, "")
        self.show_earliest_challenge_time()

    def perf(self) -> dict[str, PerfType]:
        """Get the bot's rating in every variant. Bullet, blitz, rapid etc. are considered different variants."""
        user_perf: dict[str, PerfType] = self.user_profile["perfs"]
        return user_perf

    def username(self) -> str:
        """Our username."""
        username: str = self.user_profile["username"]
        return username

    def update_user_profile(self) -> None:
        """Update our user profile data, to get our latest rating."""
        if self.last_user_profile_update_time.is_expired():
            self.last_user_profile_update_time.reset()
            with contextlib.suppress(Exception):
                self.user_profile = self.li.get_profile()

    def get_weights(self, online_bots: list[UserProfileType], rating_preference: str, min_rating: int, max_rating: int,
                    game_type: str) -> list[int]:
        """Get the weight for each bot. A higher weights means the bot is more likely to get challenged."""
        def rating(bot: UserProfileType) -> int:
            perfs: dict[str, PerfType] = bot.get("perfs", {})
            perf: PerfType = perfs.get(game_type, {})
            return perf.get("rating", 0)

        if rating_preference == "high":
            # A bot with max_rating rating will be twice as likely to get picked than a bot with min_rating rating.
            reduce_ratings_by = min(min_rating - (max_rating - min_rating), min_rating - 1)
            weights = [rating(bot) - reduce_ratings_by for bot in online_bots]
        elif rating_preference == "low":
            # A bot with min_rating rating will be twice as likely to get picked than a bot with max_rating rating.
            reduce_ratings_by = max(max_rating - (min_rating - max_rating), max_rating + 1)
            weights = [reduce_ratings_by - rating(bot) for bot in online_bots]
        else:
            weights = [1] * len(online_bots)
        return weights

    def choose_opponent(self, allowed_bot_lanes: set[str] | None = None,
                        *, correspondence_only: bool = False) -> tuple[str | None, int, int, int, str, str]:
        """Choose an opponent."""
        override_choice = random.choice(self.matchmaking_cfg.overrides.keys() + [None])
        logger.info(f"Using the {override_choice or 'default'} matchmaking configuration.")
        override = {} if override_choice is None else self.matchmaking_cfg.overrides.lookup(override_choice)
        match_config = self.matchmaking_cfg | override

        variant = self.get_random_config_value(match_config, "challenge_variant", self.variants)
        mode = self.get_random_config_value(match_config, "challenge_mode", ["casual", "rated"])
        rating_preference = match_config.rating_preference

        candidate_time_controls = configured_time_controls(match_config,
                                                           allowed_bot_lanes,
                                                           include_correspondence=correspondence_only)
        if correspondence_only:
            candidate_time_controls = [control for control in candidate_time_controls if control[2] > 0]
        else:
            candidate_time_controls = [control for control in candidate_time_controls if control[2] == 0]
        if not candidate_time_controls:
            logger.error("No valid time controls are available for matchmaking with the current settings.")
            return None, 0, 0, 0, variant, mode

        base_time, increment, num_days = random.choice(candidate_time_controls)

        game_type = game_category(variant, base_time, increment, num_days)

        min_rating = match_config.opponent_min_rating
        max_rating = match_config.opponent_max_rating
        rating_diff = match_config.opponent_rating_difference
        bot_rating = self.perf().get(game_type, {}).get("rating", 0)
        if rating_diff is not None and bot_rating > 0:
            min_rating = bot_rating - rating_diff
            max_rating = bot_rating + rating_diff
        logger.info(f"Seeking {game_type} game with opponent rating in [{min_rating}, {max_rating}] ...")

        def is_suitable_opponent(bot: UserProfileType) -> bool:
            perf = bot.get("perfs", {}).get(game_type, {})
            return (bot["username"] != self.username()
                    and not self.in_block_list(bot["username"])
                    and perf.get("games", 0) > 0
                    and min_rating <= perf.get("rating", 0) <= max_rating)

        self.online_block_list.refresh()
        online_bots = self.li.get_online_bots()
        online_bots = list(filter(is_suitable_opponent, online_bots))

        def ready_for_challenge(bot: UserProfileType) -> bool:
            aspects = [variant, game_type, mode] if self.challenge_filter == FilterType.FINE else []
            return all(self.should_accept_challenge(bot["username"], aspect) for aspect in aspects)

        ready_bots = list(filter(ready_for_challenge, online_bots))
        online_bots = ready_bots or online_bots
        bot_username = None
        weights = self.get_weights(online_bots, rating_preference, min_rating, max_rating, game_type)

        try:
            bot = random.choices(online_bots, weights=weights)[0]
            bot_profile = self.li.get_public_data(bot["username"])
            if bot_profile.get("blocking"):
                self.add_to_block_list(bot["username"])
            else:
                bot_username = bot["username"]
        except Exception:
            if online_bots:
                logger.exception("Error:")
            else:
                logger.error("No suitable bots found to challenge.")

        return bot_username, base_time, increment, num_days, variant, mode

    def get_random_config_value(self, config: Configuration, parameter: str, choices: list[str]) -> str:
        """Choose a random value from `choices` if the parameter value in the config is `random`."""
        value: str = config.lookup(parameter)
        return value if value != "random" else random.choice(choices)

    def challenge(self, active_games: set[str], challenge_queue: MULTIPROCESSING_LIST_TYPE, max_games: int) -> None:
        """
        Challenge an opponent.

        :param active_games: The games that the bot is playing.
        :param challenge_queue: The queue containing the challenges.
        :param max_games: The maximum allowed number of simultaneous games.
        """
        if challenge_queue:
            return

        if self._challenge_for_background_correspondence(active_games):
            return

        max_games_for_matchmaking = max_games if self.matchmaking_cfg.allow_during_games else min(1, max_games)
        game_count = len(active_games)
        if game_count >= max_games_for_matchmaking:
            return

        allowed_bot_lanes = self.slots.available_bot_lanes(active_games) if self.slots else {"short", "long"}
        if not allowed_bot_lanes:
            return

        cooldown_while_games_active = self.max_wait_time
        if self.slots and self.slots.enabled:
            # In slot mode (concurrency=3), fill the missing bot lane quickly.
            cooldown_while_games_active = self.min_wait_time

        if game_count > 0 and self.last_challenge_created_delay.time_since_reset() < cooldown_while_games_active:
            return

        if not self.should_create_challenge():
            return

        self.create_matchmaking_challenge(active_games, allowed_bot_lanes=allowed_bot_lanes)

    def _challenge_for_background_correspondence(self, active_games: set[str]) -> bool:
        """Ensure there is always one correspondence game in slot mode."""
        if not (self.slots and self.slots.enabled):
            return False

        current_correspondence = self.slots.correspondence_reservation_count()
        if current_correspondence >= self.max_background_correspondence_games:
            return False
        ignore_min_wait = self.force_immediate_challenge
        self.force_immediate_challenge = False
        if not self.should_create_challenge(ignore_postgame_timeout=True, ignore_min_wait=ignore_min_wait):
            return False
        return self.create_matchmaking_challenge(active_games, correspondence_only=True)

    def create_matchmaking_challenge(self, active_games: set[str],
                                     allowed_bot_lanes: set[str] | None = None,
                                     *,
                                     correspondence_only: bool = False) -> bool:
        """Create one outgoing matchmaking challenge."""
        logger.info("Challenging a random bot")
        self.update_user_profile()
        bot_username, base_time, increment, days, variant, mode = self.choose_opponent(allowed_bot_lanes,
                                                                                        correspondence_only=correspondence_only)
        if not bot_username:
            return False

        challenge_speed = game_category("standard", base_time, increment, days)
        if self.slots and not self.slots.can_accept_bot_speed(challenge_speed, active_games):
            return False

        logger.info(f"Will challenge {bot_username} for a {variant} game.")
        challenge_id = self.create_challenge(bot_username, base_time, increment, days, variant, mode)
        logger.info(f"Challenge id is {challenge_id or 'None'}.")
        self.challenge_id = challenge_id
        if challenge_id and self.slots:
            self.slots.reserve_outgoing_challenge(challenge_id, challenge_speed)
        return bool(challenge_id)

    def discard_challenge(self, challenge_id: str) -> None:
        """
        Clear the ID of the most recent challenge if it is no longer needed.

        :param challenge_id: The ID of the challenge that is expired, accepted, or declined.
        """
        if self.challenge_id == challenge_id:
            self.challenge_id = ""

    def game_done(self) -> None:
        """Reset the timer for when the last game ended, and prints the earliest that the next challenge will be created."""
        self.last_game_ended_delay.reset()
        self.show_earliest_challenge_time()

    def correspondence_game_done(self) -> None:
        """Request that matchmaking immediately replaces a finished correspondence background game."""
        self.force_immediate_challenge = True

    def show_earliest_challenge_time(self) -> None:
        """Show the earliest that the next challenge will be created."""
        if self.matchmaking_cfg.allow_matchmaking:
            postgame_timeout = self.last_game_ended_delay.time_until_expiration()
            time_to_next_challenge = self.min_wait_time - self.last_challenge_created_delay.time_since_reset()
            rate_limit_delay = self.rate_limit_timer.time_until_expiration()
            time_left = max(postgame_timeout, time_to_next_challenge, rate_limit_delay)
            earliest_challenge_time = datetime.datetime.now() + time_left
            logger.info(f"Next challenge will be created after {earliest_challenge_time.strftime('%c')}")

    def add_to_block_list(self, username: str) -> None:
        """Add a bot to the blocklist."""
        self.add_challenge_filter(username, "", years(10))

    def in_block_list(self, username: str) -> bool:
        """Check if an opponent is in the block list to prevent future challenges."""
        return (not self.should_accept_challenge(username, "")) or username in self.online_block_list

    def add_challenge_filter(self, username: str, game_aspect: str, timeout: datetime.timedelta | None = None) -> None:
        """
        Prevent creating another challenge for a timeout when an opponent has declined a challenge.

        :param username: The name of the opponent.
        :param game_aspect: The aspect of a game (time control, chess variant, etc.) that caused the opponent to decline a
        challenge. If the parameter is empty, that is equivalent to adding the opponent to the block list.
        :param timeout: The amount of time to not challenge an opponent. If None, the default is a day.
        """
        self.challenge_type_acceptable[(username, game_aspect)] = Timer(timeout or days(1))

    def should_accept_challenge(self, username: str, game_aspect: str) -> bool:
        """
        Whether a bot is likely to accept a challenge to a game.

        :param username: The name of the opponent.
        :param game_aspect: A category of the challenge type (time control, chess variant, etc.) to test for acceptance.
        If game_aspect is empty, this is equivalent to checking if the opponent is in the block list.
        """
        return self.challenge_type_acceptable[(username, game_aspect)].is_expired()

    def accepted_challenge(self, event: EventType) -> None:
        """
        Set the challenge id to an empty string, if the challenge was accepted.

        Otherwise, we would attempt to cancel the challenge later.
        """
        game_id = event["game"]["id"]
        self.discard_challenge(game_id)
        if self.slots:
            self.slots.confirm_game_start(game_id)

    def declined_challenge(self, event: EventType) -> None:
        """
        Handle a challenge that was declined by the opponent.

        Depends on whether `FilterType` is `NONE`, `COARSE`, or `FINE`.
        """
        challenge = model.Challenge(event["challenge"], self.user_profile)
        opponent = challenge.challenge_target
        reason = event["challenge"]["declineReason"]
        logger.info(f"{opponent} declined {challenge}: {reason}")
        self.discard_challenge(challenge.id)
        if challenge.from_self and self.slots:
            self.slots.release(challenge.id)
        if not challenge.from_self or self.challenge_filter == FilterType.NONE:
            return

        mode = "rated" if challenge.rated else "casual"
        decline_details: dict[str, str] = {"generic": "",
                                           "later": "",
                                           "nobot": "",
                                           "toofast": challenge.speed,
                                           "tooslow": challenge.speed,
                                           "timecontrol": challenge.speed,
                                           "rated": mode,
                                           "casual": mode,
                                           "standard": challenge.variant,
                                           "variant": challenge.variant}

        reason_key = event["challenge"]["declineReasonKey"].lower()
        if reason_key not in decline_details:
            logger.warning(f"Unknown decline reason received: {reason_key}")
        game_problem = decline_details.get(reason_key, "") if self.challenge_filter == FilterType.FINE else ""
        self.add_challenge_filter(opponent.name, game_problem)
        logger.info(f"Will not challenge {opponent} to another {game_problem}".strip() + " game today.")

        self.show_earliest_challenge_time()


def game_category(variant: str, base_time: int, increment: int, num_days: int) -> str:
    """
    Get the game type (e.g. bullet, atomic, classical). Lichess has one rating for every variant regardless of time control.

    :param variant: The game's variant.
    :param base_time: The base time in seconds.
    :param increment: The increment in seconds.
    :param num_days: If the game is correspondence, we have some days to play the move.
    :return: The game category.
    """
    game_duration = base_time + increment * 40
    if variant != "standard":
        return variant
    if num_days:
        return "correspondence"
    if game_duration < 179:
        return "bullet"
    if game_duration < 479:
        return "blitz"
    if game_duration < 1499:
        return "rapid"
    return "classical"
