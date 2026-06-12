lấy 10 ván ở data test và test, đưa ra các chỉ số đánh giá....



Friday 10:48 PM






Worked for 2h 7m 16s



Đã chạy xong 10 ván trong test split. Mình chọn 10 ván test ngắn nhất để chạy nhanh hơn: tổng 247 nước.
Output chính:
reports/qwen_test10_games_stockfish_final_report.json
reports/qwen_test10_games_stockfish_final_predictions.jsonl
reports/qwen_test10_games_stockfish_final_predictions.csv
reports/qwen_test10_games_stockfish_final_review.md
Kết quả sau sanitizer:
Rows: 247
Rule errors: 0
Capture: precision 1.00, recall 0.892, F1 0.943
Check: precision/recall/F1 1.00
Checkmate: precision/recall/F1 1.00
Castling: precision/recall/F1 1.00
Promotion: precision/recall/F1 1.00
Engine/CPL/classification mention: 247/247 = 100%
Avg words: 27.2
Avg latency: 7.40s/move, p95 13.50s/move
Điểm rất quan trọng: raw model trước sanitizer vẫn còn lỗi:
advantage_contradiction: 71/247
captured_piece_mismatch: 24/247
wrong_move_mention: 5/247
Raw engine exact CPL mention: 0%
Nghĩa là pipeline cuối đang ổn nhờ sanitizer + deterministic engine summary, nhưng bản thân model chưa thật sự học chắc phần Stockfish/CPL. Vấn đề lớn còn lại là style: 141/247 câu bị warning generic_template_phrase, tức là câu đúng nhưng hơi lặp khuôn.

Edited test_predictions.py+346-18Review changesUndoReview




12:56 AM






Giải thích các chỉ số trên, tại sao lại cần dùng đến chỉ số đấy, và các chỉ số mang lại giá trị gì



1:06 AM






Các chỉ số này dùng để trả lời 3 câu hỏi khác nhau:
Model có nói đúng dữ kiện cờ vua không?
Model có nhắc được những thông tin quan trọng không?
Pipeline chạy realtime có đủ nhanh và ổn định không?
1. Rule errors
Ví dụ:
Rule errors: 0
capture_hallucination: 0
wrong_move_mention: 0
captured_piece_mismatch: 0
checkmate_missing: 0
Đây là nhóm chỉ số quan trọng nhất cho project của bạn.
Nó kiểm tra các lỗi kiểu:
nước không bắt quân nhưng model nói “captures”
nước là Nxf6+ nhưng model nói nhầm nước khác
bắt pawn nhưng model nói bắt knight
có checkmate mà model không nói checkmate
không có lợi thế nhưng model bịa “White has an edge”
Giá trị của nó: đảm bảo commentary không sai luật, không hallucinate. Với chess commentary, sai dữ kiện nguy hiểm hơn văn phong xấu.
2. Precision
Ví dụ capture precision = 1.00.
Precision trả lời câu hỏi:
Khi model nói có capture/check/castling/promotion, nó nói đúng bao nhiêu phần trăm?

Công thức đơn giản:
precision = đúng khi có nói / tổng số lần model nói
Ví dụ capture precision 1.00 nghĩa là: mỗi lần output nói capture, đúng là nước đó có bắt quân. Đây là chỉ số chống hallucination.
Giá trị: nếu precision thấp, model đang bịa dữ kiện.
3. Recall
Ví dụ capture recall = 0.892.
Recall trả lời câu hỏi:
Trong các nước thật sự có capture/check/castling, model có nhắc được bao nhiêu phần trăm?

recall = số lần nhắc đúng / tổng số case thật sự có
Capture recall 0.892 nghĩa là trong 65 nước bắt quân, output nhắc rõ capture ở 58 nước, còn 7 nước có thể chỉ nói kiểu “White plays Bxd4” mà không nói rõ “captures”.
Giá trị: nếu recall thấp, model không sai, nhưng thiếu thông tin quan trọng.
4. F1
F1 là điểm cân bằng giữa precision và recall.
F1 cao = vừa ít bịa, vừa ít bỏ sót
Ví dụ:
capture precision = 1.00
capture recall = 0.892
capture F1 = 0.943
Nghĩa là capture đang khá ổn: không bịa capture, nhưng đôi lúc chưa diễn giải rõ capture.
5. Engine/CPL/classification metrics
Ví dụ:
engine_mentioned: 247/247
cpl_exact_mentioned: 247/247
classification_word_mentioned: 247/247
Nhóm này kiểm tra phần Stockfish:
output có nhắc engine không?
có nhắc đúng CPL không?
có nhắc đúng loại nước đi: excellent, inaccuracy, mistake, blunder không?
Giá trị: vì bạn muốn thêm đánh giá kiểu “nước này là blunder/good/excellent dựa trên Stockfish”, nên phải đo xem thông tin Stockfish có thật sự đi vào câu comment không.
Nhưng cần chú ý: kết quả 100% hiện tại là sau sanitizer. Raw model trước sanitizer chỉ nhắc engine khoảng 56%, và CPL exact là 0%. Tức là model chưa tự học chắc phần này, pipeline đang dựa nhiều vào sanitizer để chèn câu engine.
6. Generic template warning
generic_template_phrase: 141/247 = 57.1%
Đây không phải lỗi factual. Nó báo câu bị lặp khuôn, ví dụ:
a quiet pawn move that improves the position
keeps the position developing
Giá trị: chỉ số này đo chất lượng văn phong. Factual đúng rồi, nhưng nếu warning này cao thì commentary nghe máy móc, thiếu tự nhiên.
7. Raw prediction vs final prediction
Đây là phần rất quan trọng.
Raw prediction là model tự sinh trước sanitizer.
Final prediction là output sau khi sanitizer sửa/lọc/gắn Stockfish summary.
Kết quả hiện tại:
Final errors: 0
Raw advantage_contradiction: 71
Raw captured_piece_mismatch: 24
Raw wrong_move_mention: 5
Ý nghĩa: pipeline cuối dùng được, nhưng model gốc vẫn còn lỗi ngữ nghĩa. Nếu bỏ sanitizer, output chưa đủ an toàn.
8. Latency
avg latency: 7.40s/move
p95 latency: 13.50s/move
Avg là thời gian trung bình sinh một comment.
P95 nghĩa là 95% số nước chạy dưới khoảng 13.5s.
Giá trị: dùng để biết có realtime được không. Với realtime chess, 7.4s/move hơi chậm nếu muốn bình luận ngay lập tức, nhưng có thể chấp nhận nếu demo/offline. Muốn realtime mượt hơn thì cần giảm max tokens, dùng model nhỏ hơn, hoặc cache/streaming.
Tóm lại: hiện tại factual pipeline đã ổn sau sanitizer, Stockfish facts vào output tốt, nhưng model raw chưa đủ tin cậy và văn phong còn lặp. Bước tiếp theo nên là cải thiện style và nếu muốn model tự hiểu Stockfish hơn thì cần tạo SFT data có Stockfish labels rồi fine-tune tiếp.