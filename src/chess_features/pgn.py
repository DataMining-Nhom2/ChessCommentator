import io
from typing import Any, Dict, List, Optional

import chess
import chess.pgn

from src.data.data_pipeline import PIECE_NAMES


def piece_name(piece: Any) -> Optional[str]:
    if piece is None:
        return None
    return PIECE_NAMES.get(piece.symbol().upper())


def phase_for_ply(ply: int, board: chess.Board) -> str:
    if ply <= 16:
        return "Opening"
    if len(board.piece_map()) <= 14:
        return "Endgame"
    return "Middlegame"


def castle_side(board: chess.Board, move: chess.Move) -> Optional[str]:
    if board.is_kingside_castling(move):
        return "kingside"
    if board.is_queenside_castling(move):
        return "queenside"
    return None


def promotion_piece(move: chess.Move) -> Optional[str]:
    if move.promotion is None:
        return None
    return PIECE_NAMES.get(chess.piece_symbol(move.promotion).upper())


def captured_piece(board: chess.Board, move: chess.Move) -> Optional[str]:
    if not board.is_capture(move):
        return None
    if board.is_en_passant(move):
        return "Pawn"
    return piece_name(board.piece_at(move.to_square))


def winner_after_move(board: chess.Board) -> str:
    if not board.is_game_over():
        return "None"

    outcome = board.outcome()
    if outcome is None or outcome.winner is None:
        return "Draw"
    return "White" if outcome.winner is chess.WHITE else "Black"


def record_move_and_push(board: chess.Board, move: chess.Move, ply: int) -> Dict[str, Any]:
    fen_before = board.fen()
    record: Dict[str, Any] = {
        "fullmove_number": (ply + 1) // 2,
        "current_player": "White" if board.turn == chess.WHITE else "Black",
        "san": board.san(move),
        "uci": move.uci(),
        "piece": piece_name(board.piece_at(move.from_square)),
        "from_square": chess.square_name(move.from_square),
        "to_square": chess.square_name(move.to_square),
        "feature_source": "replay",
        "replay_status": "success",
        "fen_before": fen_before,
        "fen_after": None,
        "phase_raw": phase_for_ply(ply, board),
        "classification_raw": "Unknown",
        "classification_stockfish": None,
        "is_capture": board.is_capture(move),
        "captured_piece": captured_piece(board, move),
        "is_check": False,
        "is_checkmate": False,
        "is_castling": board.is_castling(move),
        "castle_side": castle_side(board, move),
        "is_promotion": move.promotion is not None,
        "promotion_piece": promotion_piece(move),
        "game_over_raw": "No",
        "winner_raw": "None",
        "advantage_after": None,
        "cpl": None,
    }

    board.push(move)
    record["fen_after"] = board.fen()
    record["is_check"] = board.is_check()
    record["is_checkmate"] = board.is_checkmate()
    if board.is_game_over():
        record["game_over_raw"] = "Yes"
        record["winner_raw"] = winner_after_move(board)
    return record


def records_from_pgn(pgn_text: str, max_moves: Optional[int] = None) -> List[Dict[str, Any]]:
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("No PGN game found.")

    board = game.board()
    records: List[Dict[str, Any]] = []
    for ply, move in enumerate(game.mainline_moves(), start=1):
        records.append(record_move_and_push(board, move, ply))
        if max_moves is not None and len(records) >= max_moves:
            break
    return records
