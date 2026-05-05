import os
from dotenv import load_dotenv
from src.extractor import extract_physical_stats
from src.evaluator import analyze_with_stockfish
from src.commentator import generate_commentary

# Load biến môi trường từ file .env
load_dotenv()

def main():
    # 1. Khởi tạo PGN mẫu
    sample_pgn = """
    [Event "Casual Game"]
    [Result "1-0"]
    1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 7. Bd2 Bxd2+ 8. Nbxd2 d5 9. exd5 Nxd5 10. Qb3 Nce7 11. O-O O-O 12. Rfe1 c6 13. a4 Qb6 14. a5 Qxb3 15. Nxb3 Rd8 16. Nc5 Kf8 17. Ra3 Rb8 18. Ne5 b6 19. axb6 axb6 20. Ne4 f6 21. Nf3 Bg4 22. h3 Bh5 23. g4 Bf7 24. Ra7 Ra8 25. Rea1 Rxa7 26. Rxa7 b5 27. Bb3 h6 28. Nc5 Ke8 29. Kh2 Rc8 30. Nd2 Rc7 31. Ra8+ Rc8 32. Ra7 Rc7 33. Ra8+ Rc8 34. Ra6 Rc7 35. Nde4 Nc8 36. Ra8 Ke7 37. Ng3 g6 38. h4 Ndb6 39. Ra6 Bxb3 40. Nxb3 Nd7 41. h5 gxh5 42. gxh5 Kf7 43. Nf5 Ndb6 44. Nxh6+ Kg7 45. Nf5+ Kf7 46. Nc5 Ra7 47. Rxa7+ Nxa7 48. Kg3 Nd5 49. Kg4 Nc8 50. h6 Kg6 51. Ne6 Nce7 52. Nxe7+ Nxe7 53. b4 Kxh6 54. Nd8 Kg6 55. Kf4 Nd5+ 56. Ke4 Nxb4 57. f4 Kg7 58. Kf5 Nc2 59. Nxc6 b4 60. d5 b3 61. d6 b2 62. d7 b1=Q 63. d8=Q Nb4+ 64. Ke6 Qe4+ 65. Kd7 Qxc6+ 66. Ke7 Nd5+ 1-0
    """

    print("1. Đang trích xuất dữ liệu trận đấu...")
    stats, game = extract_physical_stats(sample_pgn)
    
    print("2. Đang phân tích chất lượng nước đi bằng Stockfish...")
    engine_path = os.getenv("STOCKFISH_PATH")
    eval_data = analyze_with_stockfish(game, engine_path, depth=15)
    
    # Giả lập kết quả trả về từ mô hình Predict ELO của bạn
    mock_predicted_elo = {
        "white": 1150,
        "black": 1200
    }
    
    print("3. Đang chờ bình luận...\n")
    # Truyền thêm tham số mock_predicted_elo vào hàm
    commentary = generate_commentary(stats, eval_data, predicted_elo=mock_predicted_elo, game_result="1-0")
    
    print("="*50)
    print("BÌNH LUẬN TRẬN ĐẤU:")
    print("="*50)
    print(commentary)

if __name__ == "__main__":
    main()