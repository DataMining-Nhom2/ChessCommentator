# Báo cáo kế hoạch chuẩn hóa dataset và fine-tune Qwen 2.5 7B cho bình luận cờ vua theo từng nước đi

## 1. Mục tiêu

Mục tiêu của dự án là xây dựng một mô hình ngôn ngữ có khả năng **bình luận cờ vua theo từng lượt đi** với phong cách giống bình luận viên realtime: ngắn gọn, tự nhiên, có cảm xúc vừa phải, nhưng phải bám đúng dữ kiện của nước đi.

Mô hình đích được đề xuất là **Qwen 2.5 7B Instruct**, fine-tune bằng phương pháp **LoRA/QLoRA** trên dataset per-move đã có.

Bài toán không phải là tạo engine đánh cờ, mà là tạo mô hình sinh bình luận. Vì vậy, các thông tin như nước đi, loại nước đi, chiếu, chiếu hết, bắt quân, nhập thành, phong cấp, bên đang lợi thế, độ tốt/xấu của nước đi... cần được chuẩn hóa trước khi đưa vào fine-tune.

---

## 2. Hiện trạng dataset

Dataset hiện tại có dạng JSON array, mỗi phần tử gồm ba trường chính:

```json
{
  "instruction": "...",
  "input": "...",
  "output": "..."
}
```

Trong đó:

- `instruction`: yêu cầu mô hình bình luận cờ vua realtime.
- `input`: chứa thông tin của một nước đi.
- `output`: bình luận tương ứng cho nước đi đó.

Ví dụ một sample:

```json
{
  "instruction": "Provide real-time chess commentary. Be concise and casual. Mention checks, mistakes, and who is winning.",
  "input": "MoveNumber: 21 | Move: Bxh7+ | CurrentPlayer: White | Phase: Middlegame | Classification: Good move | MateThreat: No immediate mate threat detected | Captured: B | MoveType: capture | Check: Yes | Checkmate: No | GameOver: No | GameOverReason: Game continues | Winner: None",
  "output": "Oh, white's going for it! Bishop takes on H7, check! Black's king is in trouble now. White's looking good, up a pawn and now attacking the king, nice one."
}
```

Dataset này đã là **per-move dataset**, tức là:

```text
1 sample = 1 nước đi = 1 câu/đoạn bình luận
```

Tuy nhiên, dataset hiện tại chưa đủ sạch để fine-tune trực tiếp.

---

## 3. Vấn đề chính của dataset hiện tại

### 3.1. `MoveType` bị sai nghiêm trọng

Trong dataset hiện tại, phần lớn hoặc toàn bộ dòng đang bị gán:

```text
MoveType: capture
```

Ngay cả các nước không bắt quân như:

```text
f3
Nf3
h3
O-O
O-O-O
Kg8
```

cũng bị ghi là `capture`.

Điều này rất nguy hiểm khi fine-tune, vì model sẽ học sai rằng gần như mọi nước đi đều là ăn quân. Hậu quả là model dễ sinh các câu như:

```text
White takes the pawn
Black captures the knight
White grabbed the queen
```

ngay cả khi nước đi thực tế chỉ là di chuyển bình thường hoặc nhập thành.

### 3.2. `Captured` cũng không đáng tin

Dataset có nhiều dòng dạng:

```text
Move: O-O | Captured: K | MoveType: capture
```

Nhưng `O-O` là nhập thành, không phải ăn vua.

Một số dòng có thể đang nhầm giữa:

```text
quân đang di chuyển
```

và:

```text
quân bị bắt
```

Ví dụ:

```text
Move: Nf3 | Captured: N
```

Trong trường hợp này, `N` có thể là quân mã đang đi, không phải quân bị bắt.

### 3.3. Output có thể bị nhiễu theo input sai

Do input sai, output cũng có nhiều câu dễ bị sai logic, ví dụ:

```text
White takes the pawn
Black captures the knight
White snatches the queen
```

trong khi nước đi không có ký hiệu bắt quân `x`.

Vì vậy không nên chỉ sửa input, mà cần kiểm tra cả output.

### 3.4. Output hiện tại là tiếng Anh

Dataset hiện tại đang bình luận bằng tiếng Anh. Nếu mục tiêu cuối là model trả lời tiếng Việt, cần tạo lại hoặc rewrite output sang tiếng Việt.

Có hai hướng:

1. Fine-tune tiếng Anh trước để có baseline nhanh.
2. Rewrite sang tiếng Việt rồi fine-tune model bình luận tiếng Việt.

Với project hiện tại, nên ưu tiên hướng 2 nếu sản phẩm cuối dùng tiếng Việt.

---

## 4. Các dạng nước đi cần nhận diện

Trong cờ vua, một nước đi có thể thuộc nhiều dạng cùng lúc. Không nên dùng duy nhất một trường `MoveType`, vì một nước có thể vừa là bắt quân, vừa là phong cấp, vừa là chiếu.

Ví dụ:

```text
exf8=Q+
```

Nước này đồng thời là:

```text
capture + promotion + check
```

Vì vậy nên biểu diễn bằng nhiều flag.

### 4.1. Di chuyển thường

Ví dụ:

```text
e4
Nf3
Bb5
Re1
Kh2
```

Đặc điểm:

```json
{
  "is_quiet": true,
  "is_capture": false,
  "is_check": false,
  "is_checkmate": false,
  "is_castling": false,
  "is_promotion": false
}
```

### 4.2. Ăn quân

Ví dụ:

```text
exd5
Nxe5
Bxh7+
Qxf7#
```

Đặc điểm:

```json
{
  "is_capture": true,
  "captured_piece": "Pawn"
}
```

### 4.3. Chiếu

Ví dụ:

```text
Qh5+
Bxh7+
Ng5+
```

Đặc điểm:

```json
{
  "is_check": true,
  "is_checkmate": false
}
```

### 4.4. Chiếu hết

Ví dụ:

```text
Qh4#
Qxf7#
Qh7#
```

Đặc điểm:

```json
{
  "is_check": true,
  "is_checkmate": true,
  "game_over": true
}
```

### 4.5. Nhập thành

Ví dụ:

```text
O-O
O-O-O
```

Đặc điểm:

```json
{
  "is_castling": true,
  "castle_side": "kingside",
  "is_capture": false,
  "captured_piece": null
}
```

hoặc:

```json
{
  "is_castling": true,
  "castle_side": "queenside",
  "is_capture": false,
  "captured_piece": null
}
```

### 4.6. Phong cấp

Ví dụ:

```text
e8=Q
exf8=Q+
bxa1=R#
```

Đặc điểm:

```json
{
  "is_promotion": true,
  "promotion_piece": "Queen"
}
```

### 4.7. En passant

Ví dụ SAN có thể giống một nước bắt quân bình thường:

```text
exd6
```

Cần board state mới biết đây có phải en passant hay không.

Đặc điểm:

```json
{
  "is_en_passant": true,
  "is_capture": true
}
```

---

## 5. Schema dataset chuẩn hóa đề xuất

Sau khi clean, mỗi nước đi nên được biểu diễn như sau:

```json
{
  "game_id": "game_000001",
  "ply": 25,
  "move_number": 13,
  "current_player": "White",

  "san": "O-O",
  "uci": "e1g1",
  "piece": "King",
  "from_square": "e1",
  "to_square": "g1",

  "is_quiet": false,
  "is_capture": false,
  "captured_piece": null,
  "is_check": false,
  "is_checkmate": false,
  "is_castling": true,
  "castle_side": "kingside",
  "is_promotion": false,
  "promotion_piece": null,
  "is_en_passant": false,

  "phase": "Middlegame",

  "eval_before_cp": 20,
  "eval_after_cp": 35,
  "eval_before_mate": null,
  "eval_after_mate": null,
  "best_move_uci": "e1g1",
  "best_move_san": "O-O",
  "cpl": 0,
  "eval_swing": 15,
  "classification": "excellent",
  "advantage_before": "Equal",
  "advantage_after": "White slight advantage",

  "commentary": "Trắng nhập thành cánh vua, một nước chắc chắn để đưa vua vào an toàn. Trắng vẫn nhỉnh hơn nhẹ và có thể tiếp tục phát triển thế trận."
}
```

---

## 6. Nguồn sinh từng nhóm field

### 6.1. Field lấy từ dataset gốc

Các field có thể lấy trực tiếp hoặc parse từ dataset hiện tại:

```text
MoveNumber
Move
CurrentPlayer
Phase
Classification
MateThreat
Check
Checkmate
GameOver
GameOverReason
Winner
output
```

Tuy nhiên:

```text
Captured
MoveType
```

không nên dùng trực tiếp vì đang sai.

### 6.2. Field lấy bằng python-chess

Các field luật cờ nên lấy bằng `python-chess`:

```text
san
uci
piece
from_square
to_square
is_capture
captured_piece
is_check
is_checkmate
is_castling
castle_side
is_promotion
promotion_piece
is_en_passant
fen_before
fen_after
```

### 6.3. Field lấy bằng Stockfish

Các field đánh giá thế cờ nên lấy bằng Stockfish:

```text
eval_before_cp
eval_after_cp
eval_before_mate
eval_after_mate
best_move_uci
best_move_san
cpl
eval_swing
classification
advantage_before
advantage_after
mate_in
mate_for
```

---

## 7. Pipeline xử lý dữ liệu

### Bước 1: Parse dataset gốc

Input:

```text
chess_commentary_cleaned_combined.json
```

Output:

```text
parsed_raw_moves.csv
```

Việc cần làm:

1. Đọc JSON array.
2. Tách trường `input` thành các cột riêng.
3. Giữ lại `instruction` và `output`.
4. Thêm `raw_index`.
5. Tạo `game_id`.

Do dataset không có `game_id`, có thể tạm suy ra bằng cách:

```text
Khi MoveNumber quay lại 1 → bắt đầu game mới
```

Ví dụ:

```python
game_id = 0
prev_move_number = None

for row in rows:
    move_number = int(row["MoveNumber"])
    if prev_move_number is not None and move_number <= prev_move_number:
        game_id += 1
    row["game_id"] = f"game_{game_id:06d}"
    prev_move_number = move_number
```

---

### Bước 2: Rebuild move flags

Input:

```text
parsed_raw_moves.csv
```

Output:

```text
cleaned_move_flags.jsonl
```

Không dùng:

```text
MoveType cũ
Captured cũ
```

Tạo lại các flag bằng SAN hoặc tốt hơn là bằng `python-chess`.

Logic đơn giản từ SAN:

```python
def infer_flags_from_san(san: str):
    is_castling = san.startswith("O-O")
    castle_side = None

    if san.startswith("O-O-O"):
        castle_side = "queenside"
    elif san.startswith("O-O"):
        castle_side = "kingside"

    is_capture = "x" in san
    is_checkmate = "#" in san
    is_check = "+" in san or "#" in san
    is_promotion = "=" in san

    if is_castling:
        primary_move_type = f"castle_{castle_side}"
    elif is_promotion and is_capture:
        primary_move_type = "promotion_capture"
    elif is_promotion:
        primary_move_type = "promotion"
    elif is_capture:
        primary_move_type = "capture"
    else:
        primary_move_type = "quiet"

    return {
        "primary_move_type": primary_move_type,
        "is_capture": is_capture,
        "is_check": is_check,
        "is_checkmate": is_checkmate,
        "is_castling": is_castling,
        "castle_side": castle_side,
        "is_promotion": is_promotion
    }
```

Tuy nhiên, cách này không detect được chính xác:

```text
from_square
to_square
captured_piece
en_passant
uci
```

Vì vậy sau này nên replay game bằng `python-chess`.

---

### Bước 3: Replay game bằng python-chess

Input:

```text
cleaned_move_flags.jsonl
```

Output:

```text
move_features.jsonl
```

Mục tiêu:

1. Group theo `game_id`.
2. Khởi tạo board từ vị trí ban đầu.
3. Với mỗi SAN move:
   - lấy `fen_before`
   - parse SAN thành move object
   - lấy `uci`
   - lấy `piece`
   - lấy `from_square`
   - lấy `to_square`
   - kiểm tra capture/castling/promotion/en_passant
   - push move
   - lấy `fen_after`
   - kiểm tra check/checkmate/game_over

Pseudo-code:

```python
import chess

for game_id, game_rows in grouped_games:
    board = chess.Board()

    for row in game_rows:
        fen_before = board.fen()
        move = board.parse_san(row["Move"])

        piece = board.piece_at(move.from_square)
        captured_piece = None

        if board.is_capture(move):
            if board.is_en_passant(move):
                captured_piece = "Pawn"
            else:
                captured_piece_obj = board.piece_at(move.to_square)
                captured_piece = captured_piece_obj.symbol() if captured_piece_obj else None

        is_castling = board.is_castling(move)
        is_en_passant = board.is_en_passant(move)
        is_capture = board.is_capture(move)

        board.push(move)

        fen_after = board.fen()
        is_check = board.is_check()
        is_checkmate = board.is_checkmate()
```

---

### Bước 4: Enrich bằng Stockfish

Input:

```text
move_features.jsonl
```

Output:

```text
stockfish_enriched_moves.jsonl
```

Với mỗi move:

1. Đánh giá `fen_before`.
2. Lấy best move từ Stockfish.
3. Đánh giá `fen_after`.
4. Tính CPL.
5. Tạo classification.
6. Tạo advantage label.

CPL cần tính theo góc nhìn người vừa đi:

```python
def compute_cpl(eval_before_cp, eval_after_cp, current_player):
    if current_player == "White":
        player_before = eval_before_cp
        player_after = eval_after_cp
    else:
        player_before = -eval_before_cp
        player_after = -eval_after_cp

    return max(0, player_before - player_after)
```

Ngưỡng classification đề xuất:

```python
def classify_move(cpl):
    if cpl <= 10:
        return "excellent"
    if cpl <= 20:
        return "good"
    if cpl <= 50:
        return "inaccuracy"
    if cpl <= 150:
        return "mistake"
    return "blunder"
```

Advantage label:

```python
def advantage_label(eval_cp):
    if eval_cp >= 600:
        return "White winning"
    if eval_cp >= 250:
        return "White clear advantage"
    if eval_cp >= 80:
        return "White slight advantage"
    if eval_cp <= -600:
        return "Black winning"
    if eval_cp <= -250:
        return "Black clear advantage"
    if eval_cp <= -80:
        return "Black slight advantage"
    return "Equal"
```

---

### Bước 5: Làm sạch commentary

Input:

```text
stockfish_enriched_moves.jsonl
```

Output:

```text
commentary_cleaned.jsonl
```

Cần kiểm tra output có mâu thuẫn với flag mới không.

Các rule nên dùng:

#### Rule 1: Không capture nhưng output nói capture

Nếu:

```json
"is_capture": false
```

mà output chứa:

```text
takes
captures
grabs
snatches
took
captured
```

thì đánh dấu sample cần sửa hoặc loại.

#### Rule 2: Nhập thành nhưng output nói bắt quân

Nếu:

```json
"is_castling": true
```

output nên có ý:

```text
castle
king safety
nhập thành
đưa vua an toàn
```

không nên có:

```text
takes
captures
grabs
```

#### Rule 3: Checkmate phải nói kết thúc

Nếu:

```json
"is_checkmate": true
```

output nên nhắc:

```text
checkmate
game over
wins
chiếu hết
ván đấu kết thúc
```

#### Rule 4: Blunder phải thể hiện lỗi

Nếu:

```json
"classification": "blunder"
```

output nên nhắc:

```text
blunder
huge mistake
lỗi nặng
sai lầm nghiêm trọng
```

#### Rule 5: Winner không được sai

Nếu:

```json
"winner": "White"
```

output không được nói Black wins.

Nếu:

```json
"winner": "Black"
```

output không được nói White wins.

---

## 8. Có nên rewrite output sang tiếng Việt không?

Nếu mục tiêu cuối cùng là mô hình trả lời tiếng Việt, nên rewrite output sang tiếng Việt.

Style đề xuất:

```text
1–3 câu
ngắn gọn
giọng bình luận viên
tự nhiên
không quá học thuật
không bịa dữ kiện
```

Ví dụ tiếng Việt:

```text
Trắng Bxh7+, một nước bắt quân kèm chiếu khá chủ động. Vua Đen bắt đầu chịu áp lực, và Trắng vẫn giữ được thế trận dễ chơi hơn.
```

Ví dụ với nhập thành:

```text
Trắng nhập thành cánh vua, một nước chắc chắn để đưa vua vào an toàn. Thế trận vẫn ổn cho Trắng và các quân có thể tiếp tục phối hợp tốt hơn.
```

Ví dụ với blunder:

```text
Đen vừa mắc một sai lầm lớn với nước này. Đánh giá tụt mạnh, và Trắng giờ có cơ hội rõ ràng để nắm thế chủ động.
```

Ví dụ với checkmate:

```text
Qh4# là chiếu hết! Đen kết thúc ván đấu ngay lập tức, một đòn rất gọn gàng và dứt khoát.
```

---

## 9. Tạo format fine-tune cho Qwen

Qwen Instruct nên được fine-tune ở dạng chat format:

```json
{
  "messages": [
    {
      "role": "system",
      "content": "Bạn là bình luận viên cờ vua realtime. Bình luận ngắn gọn, tự nhiên, đúng dữ kiện. Không bịa bắt quân, không bịa chiếu hết, không nói sai bên đang lợi thế."
    },
    {
      "role": "user",
      "content": "MoveNumber: 21 | Player: White | Move: Bxh7+ | Piece: Bishop | From: d3 | To: h7 | Phase: Middlegame | MoveType: capture | Capture: Yes | CapturedPiece: Pawn | Check: Yes | Checkmate: No | Classification: good | CPL: 12 | AdvantageAfter: White slight advantage | GameOver: No | Winner: None"
    },
    {
      "role": "assistant",
      "content": "Trắng Bxh7+, một nước bắt quân kèm chiếu rất chủ động. Vua Đen bắt đầu chịu áp lực, và Trắng vẫn giữ lợi thế nhẹ trong thế trận này."
    }
  ]
}
```

Output file:

```text
qwen_sft_train.jsonl
qwen_sft_valid.jsonl
qwen_sft_test.jsonl
```

---

## 10. Split dataset

Không nên split random theo từng dòng, vì các nước cùng một ván sẽ bị rơi vào cả train và test.

Cách đúng:

```text
Split theo game_id
```

Tỷ lệ đề xuất:

```text
Train: 80%
Validation: 10%
Test: 10%
```

Ví dụ:

```python
unique_games = sorted(df["game_id"].unique())

train_games = unique_games[:int(0.8 * len(unique_games))]
valid_games = unique_games[int(0.8 * len(unique_games)):int(0.9 * len(unique_games))]
test_games = unique_games[int(0.9 * len(unique_games)):]
```

---

## 11. Fine-tune Qwen 2.5 7B

### 11.1. Mô hình

```text
Base model: Qwen2.5-7B-Instruct
Fine-tune method: QLoRA
Task: supervised fine-tuning
Input: thông tin một nước đi
Output: bình luận viên realtime
```

### 11.2. Cấu hình đề xuất

```yaml
max_seq_length: 512
num_train_epochs: 2
learning_rate: 2e-4
lr_scheduler_type: cosine
warmup_ratio: 0.03
weight_decay: 0.01

per_device_train_batch_size: 1
per_device_eval_batch_size: 1
gradient_accumulation_steps: 8

lora_r: 16
lora_alpha: 32
lora_dropout: 0.05

bf16: true
fp16: false

logging_steps: 20
eval_steps: 300
save_steps: 300
save_total_limit: 2
```

Nếu GPU yếu, có thể dùng:

```yaml
load_in_4bit: true
bnb_4bit_quant_type: nf4
bnb_4bit_compute_dtype: bfloat16
```

### 11.3. LoRA target modules

```text
q_proj
k_proj
v_proj
o_proj
gate_proj
up_proj
down_proj
```

---

## 12. Đánh giá model sau fine-tune

Không nên chỉ nhìn loss. Với bài toán này, cần đánh giá model bằng rule consistency.

### 12.1. Các lỗi cần đo

| Tên lỗi | Điều kiện |
|---|---|
| Capture hallucination | `is_capture=false` nhưng model nói bắt quân |
| Missing capture | `is_capture=true` nhưng model không nhắc bắt quân trong các case quan trọng |
| Check inconsistency | `is_check=true` nhưng model không nhắc chiếu |
| Checkmate inconsistency | `is_checkmate=true` nhưng model không nói chiếu hết/kết thúc |
| Winner inconsistency | model nói sai bên thắng |
| Advantage inconsistency | model nói sai bên đang lợi thế |
| Castling inconsistency | nhập thành nhưng model nói bắt quân |
| Promotion inconsistency | phong cấp nhưng model không nhắc phong cấp |
| Classification inconsistency | blunder nhưng model không nói lỗi |

### 12.2. Test case bắt buộc

Cần có các nhóm test sau:

```text
O-O
O-O-O
Qh4#
Qxf7#
Bxh7+
Nbd2
Rfe8
e4
h3
Kg8
exf8=Q+
cxb8=Q
```

### 12.3. Metric đề xuất

```text
capture_hallucination_rate
check_consistency_rate
checkmate_consistency_rate
winner_consistency_rate
advantage_consistency_rate
castling_correctness_rate
promotion_correctness_rate
language_consistency_rate
average_output_length
```

---

## 13. Tích hợp model vào code hiện tại

Code hiện tại đang sinh một đoạn bình luận tổng trận. Sau khi fine-tune, cần đổi sang sinh bình luận từng nước.

Pipeline inference mới:

```text
PGN
→ extract từng move
→ tạo move feature
→ enrich Stockfish
→ format prompt
→ model sinh commentary
→ in từng dòng bình luận
```

Ví dụ output mong muốn:

```text
1. e4: Trắng mở màn bằng e4, chiếm trung tâm ngay từ đầu.
1... e5: Đen đáp lại cân bằng, thách thức trung tâm rất trực diện.
2. Nf3: Trắng phát triển mã lên f3, gây áp lực lên tốt e5.
2... Nc6: Đen phát triển mã, bảo vệ trung tâm và giữ thế cân bằng.
```

---

## 14. Cấu trúc thư mục đề xuất

```text
project/
├── data/
│   ├── raw/
│   │   └── chess_commentary_cleaned_combined.json
│   ├── processed/
│   │   ├── parsed_raw_moves.csv
│   │   ├── cleaned_move_flags.jsonl
│   │   ├── move_features.jsonl
│   │   ├── stockfish_enriched_moves.jsonl
│   │   └── commentary_cleaned_vi.jsonl
│   └── sft/
│       ├── qwen_sft_train.jsonl
│       ├── qwen_sft_valid.jsonl
│       └── qwen_sft_test.jsonl
│
├── src/
│   ├── parse_dataset.py
│   ├── build_move_features.py
│   ├── stockfish_enrich.py
│   ├── clean_commentary.py
│   ├── build_sft_dataset.py
│   ├── train_qwen_lora.py
│   ├── evaluate_commentary.py
│   └── inference_commentator.py
│
├── models/
│   └── qwen2_5_7b_chess_commentator_lora/
│
└── reports/
    └── finetune_qwen_chess_commentary_plan.md
```

---

## 15. Lộ trình triển khai chi tiết

### Milestone 1: Data audit

Output:

```text
dataset_audit_report.csv
```

Việc làm:

- Đếm số sample.
- Đếm phase.
- Đếm classification.
- Đếm số move có `x`, `+`, `#`, `O-O`, `=`.
- Đếm lỗi `MoveType`.
- Đếm output nghi ngờ sai.

### Milestone 2: Parse và group game

Output:

```text
parsed_raw_moves.csv
```

Việc làm:

- Parse input string.
- Tạo cột riêng.
- Tạo `game_id`.
- Tạo `ply`.

### Milestone 3: Rebuild flag

Output:

```text
cleaned_move_flags.jsonl
```

Việc làm:

- Bỏ `Captured` và `MoveType` cũ.
- Tạo flag mới từ SAN.
- Nếu có thể, replay bằng `python-chess`.

### Milestone 4: Stockfish enrichment

Output:

```text
stockfish_enriched_moves.jsonl
```

Việc làm:

- Tính eval trước/sau.
- Tính CPL.
- Tạo classification.
- Tạo advantage label.
- Lưu best move.

### Milestone 5: Commentary clean/rewrite

Output:

```text
commentary_cleaned_vi.jsonl
```

Việc làm:

- Lọc câu sai dữ kiện.
- Rewrite sang tiếng Việt.
- Chuẩn hóa style bình luận viên.

### Milestone 6: Build SFT dataset

Output:

```text
qwen_sft_train.jsonl
qwen_sft_valid.jsonl
qwen_sft_test.jsonl
```

Việc làm:

- Convert sang chat format.
- Split theo game_id.
- Kiểm tra token length.
- Kiểm tra output rỗng hoặc quá dài.

### Milestone 7: Fine-tune

Output:

```text
qwen2_5_7b_chess_commentator_lora/
```

Việc làm:

- Train QLoRA.
- Theo dõi train loss/eval loss.
- Save adapter.
- Merge adapter nếu cần deploy.

### Milestone 8: Evaluate

Output:

```text
eval_report.json
bad_cases.csv
```

Việc làm:

- Chạy model trên test set.
- Đo rule consistency.
- Lưu các case sai.
- Phân tích lỗi.
- Điều chỉnh data/prompt nếu cần.

### Milestone 9: Inference app

Output:

```text
inference_commentator.py
```

Việc làm:

- Nhập PGN.
- Sinh feature từng move.
- Gọi model.
- In bình luận từng nước.

---

## 16. Kết luận

Dataset hiện tại là một nền tảng tốt vì đã ở dạng per-move. Tuy nhiên, chưa nên fine-tune trực tiếp vì các field `MoveType` và `Captured` đang sai nặng, khiến model có nguy cơ học sai rằng gần như mọi nước đi đều là bắt quân.

Hướng xử lý đúng là:

```text
1. Giữ cấu trúc per-move hiện tại
2. Parse lại input thành field rõ ràng
3. Group lại theo game
4. Rebuild toàn bộ move flag bằng SAN/python-chess
5. Bổ sung Stockfish eval, CPL, advantage
6. Clean hoặc rewrite commentary
7. Convert sang chat format cho Qwen
8. Split theo game_id
9. Fine-tune Qwen 2.5 7B bằng QLoRA
10. Evaluate bằng rule consistency
11. Tích hợp model để bình luận từng nước đi
```

Nếu làm đúng pipeline này, mô hình Qwen sau fine-tune sẽ có khả năng bình luận từng nước đi tự nhiên hơn, đúng dữ kiện hơn, và tránh được lỗi nghiêm trọng như nói nhập thành là ăn quân hoặc nói di chuyển bình thường là capture.
