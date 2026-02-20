"""Tests for engine_wrapper module."""
import chess
import chess.engine
from lib import engine_wrapper


def test_patch_python_chess_uci_score_parser__accepts_lowerbound_before_cp() -> None:
    """Test score token order 'score lowerbound cp ...' is accepted."""
    engine_wrapper.patch_python_chess_uci_score_parser()
    board = chess.Board()
    info = chess.engine._parse_uci_info("depth 11 score lowerbound cp 1855 pv c2d3", board, chess.engine.INFO_ALL)

    assert info["lowerbound"] is True
    assert info["score"].relative.score() == 1855


def test_patch_python_chess_uci_score_parser__accepts_upperbound_before_cp() -> None:
    """Test score token order 'score upperbound cp ...' is accepted."""
    engine_wrapper.patch_python_chess_uci_score_parser()
    board = chess.Board()
    info = chess.engine._parse_uci_info("depth 11 score upperbound cp -31 pv c2d3", board, chess.engine.INFO_ALL)

    assert info["upperbound"] is True
    assert info["score"].relative.score() == -31
