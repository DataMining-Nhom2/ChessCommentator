# Bàn Giao Cho Anh Bạn No.2: Dựng AI Engine Server

Hi bro,

Team Web đã xây xong phần chơi cờ và host qua LAN/VPN rồi. Bên mình đã train xong model `Hikaru_Nakamura_V1` (CNN-BiLSTM), có sẵn code inference chạy được luôn. Giờ cần bro **dựng cái server FastAPI** để nối 2 đầu lại với nhau + làm phần **Multi-Agent giải thích ván cờ**.

Tất cả chạy trên máy bàn của bro (Máy Host), bên cạnh cái Web Game Server của team web.

---

## 1. Bức Tranh Toàn Cảnh

Khi 2 người chơi đánh cờ xong trên web, web server sẽ bắn **1 request duy nhất** sang cổng `8000` của bro:

```
Web Game Server (cổng 3001)
        │
        │  POST /api/predict-elo
        │  { pgn, clock_times, result, time_control }
        ▼
AI Engine Server (cổng 8000) ← CÁI NÀY BRO CODE
        │
        │  Xử lý xong trả JSON về
        ▼
Web hiện kết quả cho 2 Laptop
```

Bên trong AI Engine Server, luồng chạy **3 bước tuần tự**:

```
Request từ Web { pgn, clock_times, result, time_control }
    │
    ▼
┌───────────────────────────────────────────────────────┐
│  BƯỚC 1: Stockfish Analysis                            │
│                                                         │
│  Input:  PGN string                                     │
│  Làm gì: Replay ván cờ bằng python-chess, gọi           │
│          Stockfish đánh giá (eval) từng nước đi.         │
│  Output: CPL sequence (centipawn loss từng nước)        │
│          Blunder flags (nước nào CPL > 200)             │
│                                                         │
│  ⚠ BẮT BUỘC — Model cần CPL + Blunder làm input.       │
│  Không có Stockfish = không chạy được model.            │
└────────────────────┬──────────────────────────────────┘
                     │
                     │  CPL seq, Blunder flags
                     ▼
┌───────────────────────────────────────────────────────┐
│  BƯỚC 2: ELO Prediction (Model Inference)              │
│  (Code có sẵn trong app_demo.py — bro rút ra dùng)    │
│                                                         │
│  Input:  Board states (encode từ PGN — 12×8×8 planes)  │
│          Clock times (chuẩn hóa)                        │
│          CPL sequence (chuẩn hóa) ← từ Bước 1          │
│          Blunder flags ← từ Bước 1                      │
│                                                         │
│  Làm gì: Đưa vào model Hikaru_Nakamura_V1              │
│  Output: white_elo, black_elo (denormalized)            │
└────────────────────┬──────────────────────────────────┘
                     │
                     │  Truyền xuống tất cả:
                     │  - ELO dự đoán (white, black)
                     │  - CPL từng nước đi (raw, chưa chuẩn hóa)
                     │  - Clock times từng nước
                     │  - Blunder flags
                     │  - PGN gốc
                     ▼
┌───────────────────────────────────────────────────────┐
│  BƯỚC 3: Multi-Agent Analyst                            │
│  (Phần bro code mới — 3 Agent nối tiếp)                │
│                                                         │
│  Agent 1 (Data Miner) → Agent 2 (Tactician)            │
│                       → Agent 3 (Head Coach)            │
│                                                         │
│  Output: explanation (text giải thích tiếng Việt)       │
│          eco (mã khai cuộc)                             │
│          stats (CPL trung bình, blunders tổng hợp)      │
└────────────────────┬──────────────────────────────────┘
                     │
                     ▼
              Đóng gói JSON trả về Web
```

---

## 2. API Contract (Giao Kèo Với Web — KHÔNG Được Đổi)

**Endpoint:** `POST http://localhost:8000/api/predict-elo`

**Request web bắn sang:**
```json
{
  "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6",
  "clock_times": [5.2, 3.1, 12.0, 8.5, 2.1, 45.3, 3.0, 7.2],
  "result": "1-0",
  "time_control": "5+0"
}
```

| Trường | Kiểu | Mô tả |
|:-------|:-----|:------|
| `pgn` | `string` | Chuỗi nước đi chuẩn PGN, không có header |
| `clock_times` | `float[]` | Thời gian suy nghĩ (giây), xen kẽ Trắng → Đen |
| `result` | `string` | `"1-0"` / `"0-1"` / `"1/2-1/2"` |
| `time_control` | `string` | Thể thức thời gian, VD `"5+0"`, `"10+5"` |

**Response bro cần trả về:**
```json
{
  "success": true,
  "data": {
    "white_elo": 1540,
    "black_elo": 1200,
    "eco": {
      "code": "C70",
      "name": "Ruy Lopez"
    },
    "stats": {
      "white_avg_cpl": 25.3,
      "black_avg_cpl": 78.1,
      "white_blunders": 0,
      "black_blunders": 3,
      "total_moves": 42
    },
    "explanation": "Trắng khai cuộc Ruy Lopez rất bài bản. Đen mắc 3 sai lầm nghiêm trọng..."
  }
}
```

**Khi lỗi:** `{ "success": false, "error": "Mô tả lỗi" }` — web sẽ tự handle, không crash.

---

## 3. Chi Tiết Bước 1: Stockfish Analysis (Bro Phải Code)

Web chỉ gửi sang PGN + clock_times. **Không gửi CPL.** Nhưng model CNN-BiLSTM của mình được train với CPL + Blunder flags — thiếu 2 cái này thì model chạy không chính xác.

**Vậy nên bro phải chạy Stockfish ở server để tính CPL trước khi đưa vào model.**

**Luồng xử lý:**
1. Parse PGN → replay từng nước bằng `python-chess`.
2. Tại mỗi nước đi, gọi Stockfish đánh giá thế cờ (eval) với depth ~15.
3. Tính CPL = `|eval_trước_nước_đi - eval_sau_nước_đi|` (đơn vị centipawn).
4. Nếu CPL > 200 → đánh dấu là Blunder.
5. Kết quả: 2 mảng — `cpl_sequence[]` và `blunder_flags[]`, cùng độ dài với số nước đi.

**Dữ liệu ra (giữ lại cho cả Bước 2 và Bước 3):**
- `cpl_sequence`: `[12, 5, 350, 8, 280, ...]` (raw, chưa chuẩn hóa)
- `blunder_flags`: `[0, 0, 1, 0, 1, ...]` (1 nếu CPL > 200)
- Tính luôn: `white_avg_cpl`, `black_avg_cpl`, `white_blunders`, `black_blunders`

**Lưu ý hiệu năng:** Stockfish là bước **tốn thời gian nhất** (5–15 giây). Giảm depth xuống 12–15 cho PoC để giữ response < 30s.

---

## 4. Chi Tiết Bước 2: ELO Prediction (Code Có Sẵn — Bro Rút Ra Dùng)

Code inference có sẵn trong `app_demo.py`. Bro chỉ cần **rút ruột** ra, bỏ phần Streamlit UI đi.

**Các hàm cần lấy từ `app_demo.py`:**
- `encode_board(board)` — chuyển trạng thái bàn cờ thành tensor `[12, 8, 8]`
- `replay_game(moves_san)` — replay ván cờ, encode từng nước
- `prepare_game(row, config)` — chuẩn bị tensor cho model
- Class `RatingNet` — kiến trúc model
- `CONFIG` — các hằng số chuẩn hóa

**Các hằng số chuẩn hóa (đã fix sẵn từ lúc train):**
```
ratings_mean = 1514.0,  ratings_std = 366.0
clocks_mean  = 273.0,   clocks_std  = 380.0
cpl_mean     = 50.0,    cpl_std     = 100.0
blunder_threshold = 200
```

**Luồng inference:**
1. PGN → replay → encode board states → tensor `[1, T, 12, 8, 8]`
2. Clock times → chuẩn hóa: `(clock - 273.0) / 380.0` → tensor `[1, T]`
3. CPL sequence (từ Bước 1) → chuẩn hóa: `(cpl - 50.0) / 100.0` → tensor `[1, T]`
4. Blunder flags (từ Bước 1) → tensor `[1, T]`
5. Đưa vào model → ra `[white_elo_norm, black_elo_norm]`
6. Denormalize: `elo = pred * 366.0 + 1514.0`

**Model file:** `models/Hikaru_Nakamura_V1`
**Load 1 lần duy nhất** khi server khởi động, cache trong RAM. Không reload mỗi request.

---

## 5. Chi Tiết Bước 3: Multi-Agent Analyst (Bro Code Mới)

Đây là phần show trình. Sau khi có ELO + CPL + Blunders từ 2 bước trên, bro dùng 3 Agents nối tiếp để sinh lời giải thích:

### ⚙️ Agent 1: The Data Miner (Python thuần — KHÔNG gọi LLM)

**Đầu vào:** CPL sequence + clock_times + PGN (từ Bước 1 & 2)

**Nhiệm vụ:**
1. Lọc ra **top 3 nước đi tệ nhất** (CPL cao nhất) → 3 Critical Blunders.
2. Lấy thời gian suy nghĩ tương ứng (người chơi nghĩ 2 giây hay 2 phút rồi mới đi sai?).
3. Xác định mã khai cuộc ECO từ vài nước đi đầu (bảng tra cứu).
4. Tổng hợp stats: `white_avg_cpl`, `black_avg_cpl`, `white_blunders`, `black_blunders`, `total_moves`.

**Đầu ra mẫu:**
```json
{
  "eco": { "code": "C70", "name": "Ruy Lopez" },
  "stats": { "white_avg_cpl": 25.3, "black_avg_cpl": 78.1, "white_blunders": 0, "black_blunders": 3, "total_moves": 42 },
  "critical_blunders": [
    { "move_number": 18, "side": "black", "move": "Qd5", "cpl": 350, "time_spent": 2.1 },
    { "move_number": 22, "side": "black", "move": "Bg4", "cpl": 280, "time_spent": 45.3 },
    { "move_number": 15, "side": "black", "move": "Nxe4", "cpl": 210, "time_spent": 8.5 }
  ]
}
```

### ⚙️ Agent 2: The Tactician (Gọi LLM lần 1 — Phân tích lỗi)

**Đầu vào:** JSON từ Agent 1

**Nhiệm vụ:** Gọi LLM, đưa JSON vào, bảo nó phân tích nguyên nhân chiến thuật từng lỗi sai (bị chĩa đôi, lộ vua, ghim quân, v.v.).

**Kỹ thuật:** Ép LLM trả về JSON đúng schema. Nếu trả lung tung → retry.

**Đầu ra mẫu:**
```json
{
  "analysis": [
    { "move_number": 18, "move": "Qd5", "reason": "Đưa Hậu vào vị trí bị chĩa đôi bởi Mã" },
    { "move_number": 22, "move": "Bg4", "reason": "Tượng bị ghim vào Vua, mất kiểm soát cánh vua" },
    { "move_number": 15, "move": "Nxe4", "reason": "Ăn tốt tham lam, bỏ lỡ phát triển quân" }
  ]
}
```

### ⚙️ Agent 3: The Head Coach (Gọi LLM lần 2 — Tổng hợp báo cáo)

**Đầu vào:** Phân tích chiến thuật (Agent 2) + ELO dự đoán (Bước 2)

**Nhiệm vụ:** Đóng vai HLV Trưởng, viết đoạn phê bình ngắn gọn bằng tiếng Việt, có đề cập đến khai cuộc, lỗi sai cụ thể, và mức ELO.

**Đầu ra:** Đoạn text tiếng Việt → gán vào trường `"explanation"` trong JSON response.

---

## 6. Cấu Trúc Thư Mục Gợi Ý

```
src/ai_engine/
├── main.py                # FastAPI, endpoint /api/predict-elo, load model lúc startup
├── pipeline.py            # Điều phối: Bước 1 → 2 → 3 → đóng gói JSON
│
├── stockfish_analyzer.py  # Bước 1: Gọi Stockfish, tính CPL + Blunders
├── predictor.py           # Bước 2: Rút từ app_demo.py → inference ELO
│
├── agents/
│   ├── data_miner.py      # Agent 1: Parse CPL, tìm blunders, phân loại ECO
│   ├── tactician.py       # Agent 2: Gọi LLM phân tích lỗi chiến thuật
│   └── head_coach.py      # Agent 3: Gọi LLM tổng hợp báo cáo
│
└── eco_data/
    └── eco.json           # Bảng tra mã khai cuộc ECO
```

---

## 7. Checklist Nghiệm Thu

- [ ] Server bật lên ở `http://localhost:8000`, truy cập `/docs` thấy Swagger UI.
- [ ] Stockfish binary có mặt trên Máy Host, chạy được.
- [ ] Gửi POST `/api/predict-elo` với PGN hợp lệ → nhận response JSON đúng format.
- [ ] CPL + Blunders được tính đúng (không âm, không vô cực).
- [ ] ELO dự đoán trong khoảng hợp lý (400–3000).
- [ ] `explanation` là tiếng Việt, có đề cập khai cuộc và lỗi sai cụ thể.
- [ ] PGN sai/rỗng → trả `success: false`, không crash server.
- [ ] Response time toàn pipeline < 30 giây.
- [ ] Model load 1 lần lúc startup, không reload mỗi request.

Bro ok hướng này thì bắt tay vào làm nhé. Có gì unclear thì ới!
