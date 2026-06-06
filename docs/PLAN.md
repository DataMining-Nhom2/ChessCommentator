# Pipeline LLM Commentary Cho Project Predict Elo

## Summary
- Phần cần làm chỉ là **fine-tune local Qwen2.5 7B để comment từng nước đi** từ `chess_commentary_cleaned_combined.json`; không làm Elo predictor.
- Chess_600k/Elo pipeline là ngữ cảnh hệ thống lớn hơn: module khác sẽ predict `WhiteElo/BlackElo` sau trận, còn Qwen commentary chạy realtime trong lúc trận diễn ra.
- Dataset commentary hiện tại có `27,512` dòng per-move; raw data bị lỗi lớn ở `MoveType/Captured`, nên phải rebuild facts trước khi train.
- Output mục tiêu giữ tiếng Anh theo dataset hiện tại: ngắn, casual, đúng dữ kiện, không hallucinate capture/checkmate/winner.

## Key Changes
- Giữ pipeline LLM tách riêng với Elo model:
  - Input train Qwen: `data/raw/chess_commentary_cleaned_combined.json`.
  - Không dùng Chess_600k để train Qwen commentary trong v1.
  - Elo predicted values chỉ là downstream context cho post-game UI, không đưa vào SFT per-move.
- Chuẩn hóa per-move records:
  - Parse `instruction/input/output` thành field rõ ràng.
  - Không tin `MoveType` và `Captured`; chỉ giữ làm raw audit.
  - Rebuild `is_capture`, `is_check`, `is_checkmate`, `is_castling`, `is_promotion` từ SAN.
  - Bắt buộc verify `python-chess`; nếu missing thì fail-fast hoặc báo rõ, không silently sinh artifact `san_only` rồi dùng để train.
- Replay bằng `python-chess` theo segment:
  - Suy ra `game_id` khi `MoveNumber` reset/decrease.
  - Lấy `uci`, `piece`, `from_square`, `to_square`, `captured_piece`, `fen_before`, `fen_after`.
  - Nếu replay fail, đưa segment lỗi vào report/quarantine, không dùng dòng lỗi cho SFT.
- Clean commentary:
  - Giữ raw output nếu pass validator.
  - Rewrite deterministic English nếu raw output nói sai capture/check/castling/promotion/checkmate.
  - Final SFT chỉ nhận dòng `final_commentary_errors=[]`.
- Build Qwen chat SFT:
  - `system`: Qwen là real-time chess commentator, factual, concise.
  - `user`: move facts đã clean, không có raw `MoveType/Captured`.
  - `assistant`: clean English commentary.
  - Split theo `game_id`, seed `42`, tỷ lệ `70/20/10`.

## Interfaces / Artifacts
- Processed artifacts:
  - `data/processed/data_audit_report.json`: audit lỗi raw data, replay status, rewrite status.
  - `data/processed/move_records.jsonl`: canonical per-move facts dùng làm nguồn SFT.
  - `data/processed/commentary_rewritten_en.jsonl`: labels đã clean.
- SFT artifacts:
  - `data/sft/qwen_sft_train.jsonl`
  - `data/sft/qwen_sft_valid.jsonl`
  - `data/sft/qwen_sft_test.jsonl`
- Training/inference scripts:
  - `src/training/qwen_lora.py`: fine-tune Qwen2.5 7B bằng LoRA/QLoRA.
  - `src/commentary/evaluate.py`: kiểm tra rule consistency trên labels hoặc predictions.
  - `src/commentary/inference.py`: tạo prompt từ PGN và gọi model/adaptor để comment từng nước.
- Inference contract:
  - Input: một PGN hoặc stream move.
  - Extract/replay từng move thành facts giống schema train.
  - Qwen sinh comment từng nước.
  - Sau trận, module Elo riêng trả `WhiteElo/BlackElo`; Qwen commentary không chịu trách nhiệm predict Elo.

## Test Plan
- Data audit phải xác nhận:
  - `27,512` samples.
  - `MoveType=capture` toàn bộ raw data.
  - SAN capture thật khoảng `6,391`.
  - Castling, checkmate, promotion được detect đúng.
- Replay tests:
  - `f3`, `Nf3`, `Kg6` là quiet move, không capture.
  - `O-O`, `O-O-O` là castling.
  - `Bxh7+`, `Qxf7#` là capture/check đúng.
  - `cxb8=Q` là capture + promotion.
  - `Nbd2`, `Rfe8` replay được disambiguation.
- Label validation:
  - `capture_hallucination_rate = 0` trên final SFT labels.
  - `checkmate_missing_rate = 0`.
  - `castling_capture_error = 0`.
  - Output không rỗng, khoảng `5-60` words.
- Training smoke test:
  - Load Qwen2.5 7B local.
  - Train thử small subset.
  - Generate comment cho 20 moves đại diện.
  - Kiểm tra model không nói capture cho nước không có `x`.

## Commands
- Rebuild cleaned SFT data:
  ```powershell
  python -m src.data.data_pipeline
  ```
- Validate SFT chat rows without loading model:
  ```powershell
  python -m src.training.qwen_lora --validate-data-only
  ```
- Label consistency check:
  ```powershell
  python -m src.commentary.evaluate --file data/sft/qwen_sft_test.jsonl
  ```
- Install training dependencies:
  ```powershell
  python -m pip install -r requirements-train.txt
  ```
- Small training smoke test:
  ```powershell
  python -m src.training.qwen_lora --max-train-samples 256 --max-eval-samples 64 --max-steps 20
  ```
- Full LoRA/QLoRA run:
  ```powershell
  python -m src.training.qwen_lora
  ```
- PGN prompt dry-run for inference:
  ```powershell
  python -m src.commentary.inference --pgn-file game.pgn --max-moves 20 --dry-run-prompts
  ```

## Assumptions
- Không xây Elo predictor trong scope này.
- Không tải/train Chess_600k trong pipeline Qwen commentary v1.
- Qwen2.5 7B chỉ học phong cách bình luận từng nước đi.
- Stockfish enrichment là optional; v1 có thể train bằng replay facts + raw classification đã clean.
- Nếu sau này muốn Qwen viết post-game narrative dựa trên Elo predicted, tạo dataset/prompt riêng, không trộn vào per-move SFT.
