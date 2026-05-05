import chess
import chess.pgn
import io

def extract_physical_stats(pgn_string: str):
    """Đọc PGN và trích xuất các thông số vật lý của trận đấu."""
    pgn = io.StringIO(pgn_string)
    game = chess.pgn.read_game(pgn)
    board = game.board()
    
    stats = {
        "white_captures": 0, "black_captures": 0,
        "white_checks": 0, "black_checks": 0
    }
    
    for move in game.mainline_moves():
        if board.is_capture(move):
            if board.turn == chess.WHITE: 
                stats["white_captures"] += 1
            else: 
                stats["black_captures"] += 1
            
        board.push(move)
        
        if board.is_check():
            if board.turn == chess.BLACK: 
                stats["white_checks"] += 1
            else: 
                stats["black_checks"] += 1

    return stats, game