import ollama

import ollama

def generate_commentary(stats, eval_data, predicted_elo, game_result="Hòa"):
    """Sử dụng Qwen Local bình luận dựa trên ELO đã được hệ thống ML dự đoán."""
    
    model_name = 'qwen2.5:7b'
    
    prompt = f"""
    Bạn là một chuyên gia phân tích cờ vua. Một hệ thống AI chuyên biệt đã dự đoán mức ELO của hai kỳ thủ trong trận đấu này như sau:
    - ELO dự đoán Phe Trắng: {predicted_elo['white']}
    - ELO dự đoán Phe Đen: {predicted_elo['black']}

    [Dữ liệu diễn biến trận đấu]
    - Kết quả: {game_result}
    - PHE TRẮNG: Mất điểm trung bình mỗi nước (ACPL) = {eval_data['white_acpl']}. Mắc {eval_data['white']['Blunder']} Blunders, {eval_data['white']['Mistake']} Mistakes.
    - PHE ĐEN: Mất điểm trung bình mỗi nước (ACPL) = {eval_data['black_acpl']}. Mắc {eval_data['black']['Blunder']} Blunders, {eval_data['black']['Mistake']} Mistakes.

    [Yêu cầu phân tích]
    Dựa vào mức ELO đã cho và các chỉ số ACPL, Blunder/Mistake ở trên, hãy viết một bài nhận xét sắc bén (khoảng 3-4 câu tiếng Việt) để:
    1. Đánh giá xem màn trình diễn thực tế (thông qua ACPL và lỗi) có nhất quán với trình độ ELO dự đoán này không.
    2. Giải thích ngắn gọn tại sao các chỉ số trên lại mang đặc trưng của mức ELO này (ví dụ: ELO thấp thường ACPL cao và hay quăng game bằng Blunder, ELO cao thì ACPL thấp, thế trận chặt chẽ...).
    
    Giọng văn chuyên nghiệp, uyển chuyển, đi thẳng vào phân tích kỹ thuật, không cần chào hỏi hay kết luận thừa thãi.
    """
    
    try:
        response = ollama.generate(model=model_name, prompt=prompt)
        return response['response']
        
    except Exception as e:
        return f"Lỗi kết nối tới Ollama: {str(e)}"