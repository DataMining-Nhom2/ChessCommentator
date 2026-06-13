"""
♟️ Demo: Dự đoán ELO Kỳ thủ Cờ vua — CNN-BiLSTM (Hikaru_Nakamura_V1)

Chạy: conda activate MMDS && streamlit run app_demo.py
"""

import os
import sys
import json

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
import chess

# ═══════════════════════════════════════════════════════════
# CẤU HÌNH CỐ ĐỊNH
# ═══════════════════════════════════════════════════════════
MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "Hikaru_Nakamura_V1")
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "sample_600k_dl.parquet")

CONFIG = {
    "conv_filters": 32, "lstm_layers": 3, "lstm_hidden": 64,
    "fc1_hidden": 32, "dropout_rate": 0.5, "bidirectional": True,
    "use_cpl": True, "use_blunder": True, "max_moves": 150,
    "ratings_mean": 1514.0, "ratings_std": 366.0,
    "clocks_mean": 273.0, "clocks_std": 380.0,
    "cpl_mean": 50.0, "cpl_std": 100.0,
    "blunder_threshold": 200,
}

# Phân khúc ELO (band 0-4)
BAND_NAMES = {
    0: "Beginner (<1000)",
    1: "Novice (1000-1400)",
    2: "Intermediate (1400-1800)",
    3: "Advanced (1800-2200)",
    4: "Expert (≥2200)",
}


# ═══════════════════════════════════════════════════════════
# MODEL DEFINITION (Inline — RatingNet)
# ═══════════════════════════════════════════════════════════
class RatingNet(nn.Module):
    """CNN-BiLSTM cho dự đoán ELO."""
    def __init__(self, conv_filters=32, lstm_layers=3, lstm_hidden=64,
                 fc1_hidden=32, dropout_rate=0.5, bidirectional=True,
                 use_cpl=True, use_blunder=True):
        super().__init__()
        self.use_cpl = use_cpl
        self.use_blunder = use_blunder
        self.conv1 = nn.Conv2d(12, conv_filters, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm2d(conv_filters)
        self.conv2 = nn.Conv2d(conv_filters, conv_filters * 2, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm2d(conv_filters * 2)
        self.conv3 = nn.Conv2d(conv_filters * 2, conv_filters * 4, kernel_size=3, padding=1)
        self.bn3 = nn.BatchNorm2d(conv_filters * 4)
        self.conv4 = nn.Conv2d(conv_filters * 4, conv_filters * 8, kernel_size=3, padding=1)
        self.bn4 = nn.BatchNorm2d(conv_filters * 8)
        self.pool = nn.AvgPool2d(2, 2)
        self.dropout = nn.Dropout(dropout_rate)
        extra = 1
        if use_cpl: extra += 1
        if use_blunder: extra += 1
        self.lstm = nn.LSTM(input_size=conv_filters * 8 + extra, hidden_size=lstm_hidden,
                            num_layers=lstm_layers, batch_first=True, bidirectional=bidirectional)
        fc_in = lstm_hidden * 2 if bidirectional else lstm_hidden
        self.fc1 = nn.Linear(fc_in, fc1_hidden)
        self.fc2 = nn.Linear(fc1_hidden, 2)

    def forward(self, positions, clocks, lengths, cpls=None, blunders=None):
        B, T = positions.size(0), positions.size(1)
        x = positions.view(-1, 12, 8, 8)
        x = self.pool(F.leaky_relu(self.bn1(self.conv1(x))))
        x = self.pool(F.leaky_relu(self.bn2(self.conv2(x))))
        x = self.pool(F.leaky_relu(self.bn3(self.conv3(x))))
        x = self.dropout(F.leaky_relu(self.bn4(self.conv4(x))))
        x = x.view(B, T, -1)
        cats = [x, clocks.unsqueeze(2)]
        if self.use_cpl and cpls is not None: cats.append(cpls.unsqueeze(2))
        if self.use_blunder and blunders is not None: cats.append(blunders.unsqueeze(2))
        lstm_in = torch.cat(cats, dim=2)
        packed = pack_padded_sequence(lstm_in, lengths.cpu(), batch_first=True, enforce_sorted=False)
        packed_out, _ = self.lstm(packed)
        lstm_out, _ = pad_packed_sequence(packed_out, batch_first=True)
        y = self.dropout(F.leaky_relu(self.fc1(lstm_out)))
        all_out = self.fc2(y)
        idx = torch.arange(B, device=positions.device)
        last_out = all_out[idx, lengths - 1, :]
        return all_out, last_out


# ═══════════════════════════════════════════════════════════
# HÀM TIỆN ÍCH
# ═══════════════════════════════════════════════════════════
PIECE_TYPE_TO_PLANE = {
    chess.PAWN: 0, chess.KNIGHT: 1, chess.BISHOP: 2,
    chess.ROOK: 3, chess.QUEEN: 4, chess.KING: 5,
}

def encode_board(board):
    planes = np.zeros((12, 8, 8), dtype=np.float32)
    for sq in chess.SQUARES:
        piece = board.piece_at(sq)
        if piece is None: continue
        pi = PIECE_TYPE_TO_PLANE[piece.piece_type]
        if piece.color == chess.BLACK: pi += 6
        planes[pi, sq // 8, sq % 8] = 1.0
    return planes

def replay_game(moves_san, max_moves=150):
    board = chess.Board()
    boards = []
    tokens = [t for t in moves_san.split() if not (t.endswith('.') or t in ('1-0','0-1','1/2-1/2','*'))]
    for token in tokens[:max_moves]:
        try:
            board.push_san(token)
            boards.append(encode_board(board))
        except: break
    return boards

def prepare_game(row, config):
    """Chuẩn bị dữ liệu 1 ván cờ cho inference."""
    boards = replay_game(str(row.get("Moves", "")), config["max_moves"])
    if not boards: boards = [np.zeros((12, 8, 8), dtype=np.float32)]
    length = len(boards)
    positions = torch.tensor(np.stack(boards), dtype=torch.float32).unsqueeze(0)

    # Clock
    clock_seq = json.loads(str(row.get("ClockSeq", "[]")).replace('NaN','null').replace('Infinity','null')) or []
    clock_seq = [((c if c is not None else 0.0) - config["clocks_mean"]) / config["clocks_std"] for c in clock_seq[:length]]
    while len(clock_seq) < length: clock_seq.append(0.0)
    clocks = torch.tensor(clock_seq[:length], dtype=torch.float32).unsqueeze(0)

    # CPL & Blunder
    cpl_seq = json.loads(str(row.get("cpl_seq", "[]")).replace('NaN','null').replace('Infinity','null')) or []
    cpl_seq = [max(0.0, min(float(c), 2000.0)) if c is not None else 0.0 for c in cpl_seq[:length]]
    cpls_norm = [((c - config["cpl_mean"]) / config["cpl_std"]) for c in cpl_seq]
    blunders = [1.0 if c > config["blunder_threshold"] else 0.0 for c in cpl_seq]
    while len(cpls_norm) < length: cpls_norm.append(0.0); blunders.append(0.0)

    cpls = torch.tensor(cpls_norm[:length], dtype=torch.float32).unsqueeze(0)
    blunders_t = torch.tensor(blunders[:length], dtype=torch.float32).unsqueeze(0)
    lengths = torch.tensor([length])

    return positions, clocks, cpls, blunders_t, lengths

def categorize_tc(tc_str):
    try:
        parts = tc_str.split("+")
        est = int(parts[0]) + 40 * (int(parts[1]) if len(parts) > 1 else 0)
        if est < 29: return "UltraBullet"
        elif est < 179: return "Bullet"
        elif est < 479: return "Blitz"
        elif est < 1499: return "Rapid"
        else: return "Classical"
    except: return "Unknown"


# ═══════════════════════════════════════════════════════════
# CACHE: TẢI MODEL & DATA
# ═══════════════════════════════════════════════════════════
@st.cache_resource
def load_model():
    model = RatingNet(
        conv_filters=CONFIG["conv_filters"], lstm_layers=CONFIG["lstm_layers"],
        lstm_hidden=CONFIG["lstm_hidden"], fc1_hidden=CONFIG["fc1_hidden"],
        dropout_rate=CONFIG["dropout_rate"], bidirectional=CONFIG["bidirectional"],
        use_cpl=CONFIG["use_cpl"], use_blunder=CONFIG["use_blunder"],
    )
    checkpoint = torch.load(MODEL_PATH, map_location="cpu")
    model.load_state_dict(checkpoint["model_state_dict"])
    model.eval()
    return model, checkpoint

@st.cache_data
def load_sample_games():
    """Lấy 1 ván đại diện cho mỗi band (0-4)."""
    df = pd.read_parquet(DATA_PATH)
    samples = {}
    for band_id in range(5):
        band_df = df[df["ModelBand"] == band_id]
        if len(band_df) > 0:
            # Chọn ván gần trung bình ELO của band nhất
            mid_elo = band_df["EloAvg"].median()
            idx = (band_df["EloAvg"] - mid_elo).abs().idxmin()
            samples[band_id] = band_df.loc[idx].to_dict()
    return samples


# ═══════════════════════════════════════════════════════════
# GIAO DIỆN STREAMLIT
# ═══════════════════════════════════════════════════════════
st.set_page_config(page_title="♟️ Chess ELO Predictor", layout="wide")

st.title("♟️ Demo: Dự đoán ELO bằng CNN-BiLSTM")
st.caption("Mô hình: **Hikaru_Nakamura_V1** — Mining Massive Data Project")

# Tải model
model, ckpt = load_model()
st.sidebar.success(f"✅ Model loaded (Epoch {ckpt.get('epoch', '?')})")

# Tải sample games
samples = load_sample_games()

# Sidebar: chọn band
st.sidebar.header("🎯 Chọn phân khúc ELO")
band_choice = st.sidebar.radio(
    "Chọn một band:",
    options=list(samples.keys()),
    format_func=lambda x: BAND_NAMES[x],
)

game = samples[band_choice]

# ── HIỂN THỊ DỮ LIỆU VÁN CỜ ──
st.header(f"📋 Dữ liệu ván cờ — {BAND_NAMES[band_choice]}")

col1, col2, col3 = st.columns(3)
col1.metric("♔ White ELO (thực tế)", int(game["WhiteElo"]))
col2.metric("♚ Black ELO (thực tế)", int(game["BlackElo"]))
col3.metric("⏱ Time Control", f"{game.get('TimeControl', '?')} ({categorize_tc(str(game.get('TimeControl', '')))})")

col4, col5, col6 = st.columns(3)
col4.metric("📊 ELO Trung bình", int(game.get("EloAvg", 0)))
col5.metric("🔢 Số nước đi", int(game.get("NumMoves", 0)))
col6.metric("🏁 Kết quả", game.get("Result", "?"))

with st.expander("📜 Xem chuỗi nước đi (Moves)", expanded=False):
    st.code(game.get("Moves", ""), language=None)

with st.expander("⏰ Xem chuỗi đồng hồ (ClockSeq)", expanded=False):
    try:
        clocks = json.loads(str(game.get("ClockSeq", "[]")).replace('NaN','null').replace('Infinity','null'))
        st.line_chart(pd.DataFrame({"Clock (s)": [c if c is not None else 0 for c in clocks]}))
    except:
        st.write("Không có dữ liệu đồng hồ.")

with st.expander("📉 Xem chuỗi CPL (Centipawn Loss)", expanded=False):
    try:
        cpls = json.loads(str(game.get("cpl_seq", "[]")).replace('NaN','null').replace('Infinity','null'))
        cpls_clean = [max(0, min(float(c), 500)) if c is not None else 0 for c in cpls]
        st.bar_chart(pd.DataFrame({"CPL": cpls_clean}))
    except:
        st.write("Không có dữ liệu CPL.")

# ── NÚT DỰ ĐOÁN ──
st.header("🚀 Dự đoán ELO")

if st.button("⚡ Chạy Inference", type="primary", use_container_width=True):
    with st.spinner("Đang giải mã ván cờ và chạy mô hình..."):
        # Chuẩn bị dữ liệu
        row = pd.Series(game)
        positions, clocks, cpls, blunders, lengths = prepare_game(row, CONFIG)

        # Inference
        with torch.no_grad():
            _, last_out = model(
                positions, clocks, lengths,
                cpls=cpls if CONFIG["use_cpl"] else None,
                blunders=blunders if CONFIG["use_blunder"] else None,
            )
            pred = last_out * CONFIG["ratings_std"] + CONFIG["ratings_mean"]
            pred_white = pred[0, 0].item()
            pred_black = pred[0, 1].item()

    # Hiển thị kết quả
    st.success("✅ Dự đoán hoàn tất!")
    true_w = int(game["WhiteElo"])
    true_b = int(game["BlackElo"])

    res_col1, res_col2 = st.columns(2)

    with res_col1:
        st.subheader("♔ White")
        st.metric("ELO Thực tế", true_w)
        st.metric("ELO Dự đoán", f"{pred_white:.0f}", delta=f"{pred_white - true_w:+.0f}")

    with res_col2:
        st.subheader("♚ Black")
        st.metric("ELO Thực tế", true_b)
        st.metric("ELO Dự đoán", f"{pred_black:.0f}", delta=f"{pred_black - true_b:+.0f}")

    mae = (abs(pred_white - true_w) + abs(pred_black - true_b)) / 2
    st.info(f"📏 **Sai số trung bình (MAE) của ván này:** {mae:.1f} điểm ELO")
