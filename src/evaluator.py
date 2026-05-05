import chess
import chess.engine

def analyze_with_stockfish(game, engine_path: str, depth: int = 15):
    """Đánh giá trận đấu bằng Stockfish, tính ACPL riêng cho Trắng và Đen."""
    engine = chess.engine.SimpleEngine.popen_uci(engine_path)
    board = game.board()
    
    # Bộ đếm riêng cho từng bên
    white = {"Blunder": 0, "Mistake": 0, "Inaccuracy": 0, "Brilliant": 0, "cpl_total": 0, "moves": 0}
    black = {"Blunder": 0, "Mistake": 0, "Inaccuracy": 0, "Brilliant": 0, "cpl_total": 0, "moves": 0}
    
    prev_info = engine.analyse(board, chess.engine.Limit(depth=depth))
    prev_eval = prev_info["score"].white().score(mate_score=10000)
    
    for move in game.mainline_moves():
        is_white_turn = board.turn == chess.WHITE
        board.push(move)
        
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
        current_eval = info["score"].white().score(mate_score=10000)
        
        if is_white_turn:
            cpl = prev_eval - current_eval
            stats = white
        else:
            cpl = current_eval - prev_eval
            stats = black
            
        cpl = max(0, cpl) # Bỏ qua CPL âm
        stats["cpl_total"] += cpl
        stats["moves"] += 1
        
        if cpl > 150: stats["Blunder"] += 1
        elif cpl > 50: stats["Mistake"] += 1
        elif cpl > 20: stats["Inaccuracy"] += 1
            
        prev_eval = current_eval
        
    engine.quit()
    
    # Tính CPL trung bình (ACPL)
    white_acpl = white["cpl_total"] // white["moves"] if white["moves"] > 0 else 0
    black_acpl = black["cpl_total"] // black["moves"] if black["moves"] > 0 else 0
    
    return {
        "white": white, 
        "black": black, 
        "white_acpl": white_acpl, 
        "black_acpl": black_acpl
    }