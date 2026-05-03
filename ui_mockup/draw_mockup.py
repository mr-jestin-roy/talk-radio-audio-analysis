#!/usr/bin/env python3
"""Generate a Streamlit UI mockup for the Radio Show Text Analysis app."""

from PIL import Image, ImageDraw, ImageFont
import os

W, H = 1440, 960
SIDEBAR_W = 270

# Colors
C = {
    "sidebar_bg": (14, 17, 23),
    "sidebar_text": (250, 250, 250),
    "sidebar_dim": (150, 155, 165),
    "sidebar_section": (100, 105, 115),
    "main_bg": (255, 255, 255),
    "topbar_bg": (255, 255, 255),
    "tab_active_line": (255, 75, 75),
    "tab_text_active": (31, 31, 31),
    "tab_text_inactive": (100, 100, 100),
    "accent": (255, 75, 75),
    "accent_hover": (220, 53, 53),
    "card_bg": (248, 249, 250),
    "card_border": (225, 228, 232),
    "card_border_left": (255, 75, 75),
    "green": (33, 195, 84),
    "green_bg": (230, 255, 238),
    "orange": (255, 165, 0),
    "orange_bg": (255, 243, 220),
    "blue": (31, 119, 180),
    "blue_bg": (225, 240, 255),
    "gray_btn": (240, 242, 246),
    "gray_btn_text": (80, 80, 90),
    "divider": (230, 232, 236),
    "input_border": (200, 205, 212),
    "input_bg": (255, 255, 255),
    "progress_bg": (240, 242, 246),
    "progress_fill": (255, 75, 75),
    "body_text": (49, 51, 63),
    "heading": (14, 17, 23),
    "warning_bg": (255, 248, 230),
    "warning_border": (255, 195, 0),
    "info_bg": (232, 244, 255),
    "info_border": (31, 119, 180),
    "checkpoint_bg": (245, 255, 245),
    "checkpoint_border": (33, 195, 84),
}

def load_font(size, bold=False):
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{'-Bold' if bold else '-Regular'}.ttf",
        f"/usr/share/fonts/truetype/ubuntu/Ubuntu{'-B' if bold else '-R'}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                pass
    return ImageFont.load_default()

def pill(draw, x, y, text, bg, fg, font, pad_x=10, pad_y=4, radius=10):
    tw = font.getlength(text)
    pw, ph = int(tw + pad_x * 2), int(font.size + pad_y * 2)
    draw.rounded_rectangle([x, y, x + pw, y + ph], radius=radius, fill=bg)
    draw.text((x + pad_x, y + pad_y), text, font=font, fill=fg)
    return pw, ph

def rect(draw, x1, y1, x2, y2, fill=None, outline=None, radius=0, width=1):
    if radius:
        draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill, outline=outline, width=width)
    else:
        draw.rectangle([x1, y1, x2, y2], fill=fill, outline=outline, width=width)

def hline(draw, x1, x2, y, color=None):
    draw.line([(x1, y), (x2, y)], fill=color or C["divider"], width=1)

def text_wrapped(draw, text, x, y, max_w, font, fill, line_spacing=4):
    words = text.split()
    lines, line = [], []
    for w in words:
        test = " ".join(line + [w])
        if font.getlength(test) <= max_w:
            line.append(w)
        else:
            if line:
                lines.append(" ".join(line))
            line = [w]
    if line:
        lines.append(" ".join(line))
    cy = y
    for l in lines:
        draw.text((x, cy), l, font=font, fill=fill)
        cy += font.size + line_spacing
    return cy

# ── Build image ─────────────────────────────────────────────────────────────
img = Image.new("RGB", (W, H), C["main_bg"])
draw = ImageDraw.Draw(img)

# Fonts
f8  = load_font(11)
f9  = load_font(12)
f10 = load_font(13)
f11 = load_font(14)
f12 = load_font(15)
f13 = load_font(16, bold=True)
f14 = load_font(17)
f14b = load_font(17, bold=True)
f16b = load_font(20, bold=True)
f18b = load_font(22, bold=True)
f20b = load_font(26, bold=True)
f_mono = load_font(12)

# ════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ════════════════════════════════════════════════════════════════════════════
rect(draw, 0, 0, SIDEBAR_W, H, fill=C["sidebar_bg"])

# App title
draw.text((20, 22), "Rush Limbaugh Archive", font=load_font(15, bold=True), fill=C["sidebar_text"])
draw.text((20, 42), "Text Analysis Pipeline", font=f10, fill=C["sidebar_dim"])

hline(draw, 20, SIDEBAR_W - 20, 68, (40, 45, 58))

# ── Vector DB Status ──
sy = 82
draw.text((20, sy), "VECTOR DB STATUS", font=f8, fill=C["sidebar_section"])
sy += 18
pill(draw, 20, sy, "● 9,157 transcripts indexed", C["green_bg"], (15, 130, 55), f9)
sy += 26
draw.text((20, sy), "Last indexed: May 1, 2026", font=f9, fill=C["sidebar_dim"])
sy += 20
rect(draw, 20, sy, 130, sy + 26, fill=C["gray_btn"], outline=C["input_border"], radius=6)
draw.text((34, sy + 6), "Re-index", font=f10, fill=C["gray_btn_text"])

hline(draw, 20, SIDEBAR_W - 20, sy + 40, (40, 45, 58))

# ── Analysis Prompt ──
sy += 54
draw.text((20, sy), "ANALYSIS PROMPT", font=f8, fill=C["sidebar_section"])
sy += 18
rect(draw, 20, sy, SIDEBAR_W - 20, sy + 30, fill=(25, 30, 42), outline=(60, 65, 80), radius=6)
draw.text((30, sy + 9), "immigration_stance.yaml", font=f10, fill=C["sidebar_text"])
draw.text((SIDEBAR_W - 34, sy + 9), "▾", font=f11, fill=C["sidebar_dim"])
sy += 38
draw.text((20, sy), "3 prompts available", font=f9, fill=C["sidebar_dim"])

hline(draw, 20, SIDEBAR_W - 20, sy + 16, (40, 45, 58))

# ── LLM Backend ──
sy += 30
draw.text((20, sy), "LLM BACKEND", font=f8, fill=C["sidebar_section"])
sy += 16
draw.text((20, sy), "LLM_URL", font=f9, fill=C["sidebar_dim"])
sy += 14
rect(draw, 20, sy, SIDEBAR_W - 20, sy + 26, fill=(25, 30, 42), outline=(60, 65, 80), radius=6)
draw.text((30, sy + 7), "https://ab12-34.ngrok-free.app", font=load_font(11), fill=(130, 210, 255))
sy += 34
draw.text((20, sy), "Model:", font=f9, fill=C["sidebar_dim"])
draw.text((58, sy), "llama3  (vllm · guided_json)", font=f9, fill=C["sidebar_text"])
sy += 16
draw.text((20, sy), "Backend:", font=f9, fill=C["sidebar_dim"])
pill(draw, 58, sy - 2, "vllm", (30, 45, 70), (130, 190, 255), f9, pad_x=8, pad_y=3)
sy += 16
draw.text((20, sy), "Cost:", font=f9, fill=C["sidebar_dim"])
draw.text((50, sy), "free (local GPU)", font=load_font(13, bold=True), fill=C["green"])

hline(draw, 20, SIDEBAR_W - 20, sy + 34, (40, 45, 58))

# ── Date Range ──
sy += 48
draw.text((20, sy), "DATE RANGE", font=f8, fill=C["sidebar_section"])
sy += 18
draw.text((20, sy), "From", font=f9, fill=C["sidebar_dim"])
draw.text((70, sy), "2005-01-01", font=f9, fill=C["sidebar_text"])
sy += 16
draw.text((20, sy), "To", font=f9, fill=C["sidebar_dim"])
draw.text((70, sy), "2020-12-31", font=f9, fill=C["sidebar_text"])
sy += 16
draw.text((20, sy), "→ 3,831 episodes in range", font=f9, fill=C["sidebar_dim"])

hline(draw, 20, SIDEBAR_W - 20, sy + 16, (40, 45, 58))

# Version
draw.text((20, H - 22), "v0.1.0  ·  Rush Archive Pipeline", font=f8, fill=(60, 65, 78))

# ════════════════════════════════════════════════════════════════════════════
# MAIN AREA
# ════════════════════════════════════════════════════════════════════════════
mx = SIDEBAR_W  # main area left edge

# ── Tab bar ──
tab_h = 50
rect(draw, mx, 0, W, tab_h, fill=C["topbar_bg"])
hline(draw, mx, W, tab_h, C["divider"])

tabs = [
    ("🔍  Semantic Search", False),
    ("📄  Single-Episode", False),
    ("⚡  Batch Analysis", True),
    ("📈  Time-Series", False),
]
tx = mx + 20
for label, active in tabs:
    tw = f12.getlength(label)
    pad = 18
    if active:
        draw.text((tx, 16), label, font=load_font(15, bold=True), fill=C["tab_text_active"])
        draw.rectangle([tx - 2, tab_h - 3, tx + tw + 2, tab_h], fill=C["tab_active_line"])
    else:
        draw.text((tx, 16), label, font=f12, fill=C["tab_text_inactive"])
    tx += tw + pad * 2 + 10

# ── Page heading ──
content_x = mx + 36
cy = tab_h + 30

draw.text((content_x, cy), "Batch Analysis", font=f20b, fill=C["heading"])
cy += 34
draw.text((content_x, cy), "Run LLM analysis across a date range and export results for research.", font=f11, fill=(100, 105, 115))
cy += 30
hline(draw, content_x, W - 36, cy, C["divider"])
cy += 22

# ── Two-column layout ──
col1_x = content_x
col2_x = mx + (W - mx) // 2 + 10
col_w = (W - mx) // 2 - 46

# ── LEFT COLUMN: Configuration ──
draw.text((col1_x, cy), "Configuration", font=load_font(14, bold=True), fill=C["heading"])
lcy = cy + 26

# Prompt card
rect(draw, col1_x, lcy, col1_x + col_w, lcy + 88, fill=C["card_bg"], outline=C["card_border"], radius=8)
draw.text((col1_x + 14, lcy + 12), "Selected Prompt", font=f9, fill=C["sidebar_section"])
draw.text((col1_x + 14, lcy + 28), "immigration_stance.yaml", font=load_font(14, bold=True), fill=C["heading"])
draw.text((col1_x + 14, lcy + 50), "Classifies stance on immigration policy.", font=f10, fill=(100, 105, 115))
draw.text((col1_x + 14, lcy + 65), "Output fields:  stance  ·  confidence  ·  topics  ·  quote", font=f9, fill=C["blue"])
lcy += 100

# Date range display
draw.text((col1_x, lcy), "Date Range", font=f9, fill=C["sidebar_section"])
lcy += 16
rect(draw, col1_x, lcy, col1_x + col_w, lcy + 36, fill=C["input_bg"], outline=C["input_border"], radius=6)
draw.text((col1_x + 14, lcy + 11), "2005-01-01  →  2020-12-31  (3,831 episodes)", font=f10, fill=C["body_text"])
lcy += 48

# Chunk strategy
draw.text((col1_x, lcy), "Analysis Unit", font=f9, fill=C["sidebar_section"])
lcy += 16
rect(draw, col1_x, lcy, col1_x + col_w, lcy + 36, fill=C["input_bg"], outline=C["input_border"], radius=6)
draw.text((col1_x + 14, lcy + 11), "Per episode  (merge 3 hours → one LLM call per day)", font=f10, fill=C["body_text"])
lcy += 48

# ── Pre-flight estimate box ──
rect(draw, col1_x, lcy, col1_x + col_w, lcy + 110, fill=C["info_bg"], outline=C["info_border"], radius=8, width=1)
draw.text((col1_x + 14, lcy + 12), "ℹ  Pre-flight Estimate", font=load_font(13, bold=True), fill=C["blue"])
draw.line([(col1_x + 14, lcy + 30), (col1_x + col_w - 14, lcy + 30)], fill=C["info_border"], width=1)

estimates = [
    ("Episodes matched:", "3,831"),
    ("Tokens (est.):", "~38.3M"),
    ("Cost (Haiku):", "~$14.60"),
    ("Time (est.):", "~28 min"),
]
ex = col1_x + 14
ey = lcy + 38
for label, val in estimates:
    draw.text((ex, ey), label, font=f9, fill=(80, 100, 130))
    draw.text((ex + 140, ey), val, font=load_font(13, bold=True), fill=C["heading"])
    ey += 17

lcy += 122

# Run button
rect(draw, col1_x, lcy, col1_x + col_w, lcy + 40, fill=C["accent"], radius=8)
label = "▶  Run Batch Analysis"
lw = load_font(14, bold=True).getlength(label)
draw.text((col1_x + (col_w - lw) // 2, lcy + 11), label, font=load_font(14, bold=True), fill=(255, 255, 255))

# ── RIGHT COLUMN: Progress ──
draw.text((col2_x, cy), "Progress", font=load_font(14, bold=True), fill=C["heading"])
rcy = cy + 26

# Status badge
pill(draw, col2_x, rcy, "● Running", C["orange_bg"], (180, 100, 0), f10)
draw.text((col2_x + 90, rcy + 4), "Started 14:03:22  ·  Elapsed: 08:41", font=f9, fill=(100, 105, 115))
rcy += 30

# Progress bar
draw.text((col2_x, rcy), "Episodes processed", font=f9, fill=C["sidebar_section"])
draw.text((col2_x + col_w - 60, rcy), "1,247 / 3,831", font=load_font(12, bold=True), fill=C["heading"])
rcy += 16
bar_w = col_w
bar_h = 12
rect(draw, col2_x, rcy, col2_x + bar_w, rcy + bar_h, fill=C["progress_bg"], radius=6)
fill_w = int(bar_w * (1247 / 3831))
rect(draw, col2_x, rcy, col2_x + fill_w, rcy + bar_h, fill=C["progress_fill"], radius=6)
rcy += 22

# Stats row
stats = [
    ("Completed", "1,247", C["green"]),
    ("Errors", "3", (220, 60, 60)),
    ("Remaining", "2,584", C["blue"]),
    ("Rate", "2.4/s", C["body_text"]),
]
sw = col_w // len(stats)
for i, (label, val, color) in enumerate(stats):
    sx = col2_x + i * sw
    rect(draw, sx, rcy, sx + sw - 6, rcy + 52, fill=C["card_bg"], outline=C["card_border"], radius=6)
    draw.text((sx + 10, rcy + 8), label, font=f9, fill=(120, 125, 135))
    draw.text((sx + 10, rcy + 24), val, font=load_font(16, bold=True), fill=color)
rcy += 64

# ETA
rect(draw, col2_x, rcy, col2_x + col_w, rcy + 30, fill=C["warning_bg"], outline=C["warning_border"], radius=6)
draw.text((col2_x + 12, rcy + 8), "⏱  ETA: ~19 min remaining  (2026-05-02 14:31)", font=f10, fill=(140, 100, 20))
rcy += 42

# Checkpoint box
rect(draw, col2_x, rcy, col2_x + col_w, rcy + 64, fill=C["checkpoint_bg"], outline=C["checkpoint_border"], radius=6)
draw.text((col2_x + 12, rcy + 10), "✓  Auto-checkpoint enabled", font=load_font(12, bold=True), fill=(20, 140, 60))
draw.text((col2_x + 12, rcy + 28), "Saving progress to  results/immigration_20260502_1403.csv", font=f9, fill=(60, 100, 70))
draw.text((col2_x + 12, rcy + 44), "Interrupted runs resume from last checkpoint automatically.", font=f9, fill=(80, 120, 90))
rcy += 76

# Last result preview
draw.text((col2_x, rcy), "Last Result", font=load_font(13, bold=True), fill=C["heading"])
rcy += 18
rect(draw, col2_x, rcy, col2_x + col_w, rcy + 90, fill=C["card_bg"], outline=C["card_border"], radius=8)
draw.rectangle([col2_x, rcy, col2_x + 4, rcy + 90], fill=C["accent"])

pill(draw, col2_x + 14, rcy + 10, "Apr 22, 2010 · Hour 2", C["blue_bg"], C["blue"], f9)
pill(draw, col2_x + col_w - 80, rcy + 10, "stance: negative", (255, 230, 230), (180, 30, 30), f9)

draw.text((col2_x + 14, rcy + 34), "\"The amnesty bill is nothing more than a backdoor for people", font=f9, fill=C["body_text"])
draw.text((col2_x + 14, rcy + 48), " who broke the law to cut the line ahead of legal immigrants.\"", font=f9, fill=C["body_text"])

draw.text((col2_x + 14, rcy + 68), "Topics: amnesty · border security · legal immigration", font=f9, fill=(120, 125, 135))

rcy += 102

# Export button (grayed out while running)
rect(draw, col2_x, rcy, col2_x + col_w, rcy + 36, fill=C["gray_btn"], outline=C["input_border"], radius=8)
label2 = "⬇  Export CSV  (available when complete)"
draw.text((col2_x + 20, rcy + 10), label2, font=f10, fill=(160, 163, 175))

# ── Thin vertical divider between columns ──
draw.line([(col2_x - 14, cy), (col2_x - 14, H - 20)], fill=C["divider"], width=1)

# ── Bottom status bar ──
rect(draw, mx, H - 28, W, H, fill=(248, 249, 250))
hline(draw, mx, W, H - 28, C["divider"])
draw.text((content_x, H - 18), "Rush Limbaugh Archive  ·  Text Analysis Pipeline  ·  v0.1.0", font=f8, fill=(160, 163, 175))
draw.text((W - 220, H - 18), "ChromaDB  ·  sentence-transformers/all-MiniLM-L6-v2", font=f8, fill=(160, 163, 175))

# Save
out = "/home/jestin/Radio Show Pipeline/ui_mockup/streamlit_mockup.png"
img.save(out, "PNG", dpi=(144, 144))
print(f"Saved → {out}")
