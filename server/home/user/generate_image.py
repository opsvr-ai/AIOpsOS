#!/usr/bin/env python3
"""小盟 - AIOps 智能运维助手的形象图生成"""

from PIL import Image, ImageDraw, ImageFont
import os

# ===== 配置 =====
WIDTH, HEIGHT = 800, 600
OUTPUT_PATH = "/home/user/xiaomeng_image.png"

# ===== 创建画布 =====
img = Image.new("RGB", (WIDTH, HEIGHT), "#0a0e27")
draw = ImageDraw.Draw(img)

# ===== 配色 =====
COLORS = {
    "primary": "#00d4ff",      # 亮青
    "secondary": "#7c3aed",    # 紫
    "accent": "#f59e0b",       # 琥珀
    "success": "#10b981",      # 翠绿
    "bg_card": "#141938",      # 深蓝底
    "bg_dark": "#0a0e27",      # 最深底
    "text": "#e2e8f0",
    "text_dim": "#64748b",
}

# ===== 辅助函数 =====
def draw_rounded_rect(draw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def draw_circle(draw, center, radius, fill=None, outline=None, width=1):
    x, y = center
    draw.ellipse([x-radius, y-radius, x+radius, y+radius], fill=fill, outline=outline, width=width)

# ===== 背景渐变 =====
for y in range(HEIGHT):
    ratio = y / HEIGHT
    r = int(10 + ratio * 15)
    g = int(14 + ratio * 20)
    b = int(39 + ratio * 30)
    draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))

# ===== 装饰：网格/星点 =====
import random
random.seed(42)
for _ in range(80):
    x = random.randint(0, WIDTH)
    y = random.randint(0, HEIGHT)
    size = random.randint(1, 3)
    alpha = random.randint(30, 120)
    draw_circle(draw, (x, y), size, fill=(alpha, alpha, alpha + 50))

# ===== 装饰：浮动光环 =====
draw_circle(draw, (650, 100), 120, fill=None, outline=(0, 212, 255, 20), width=2)
draw_circle(draw, (650, 100), 90, fill=None, outline=(124, 58, 237, 30), width=1)
draw_circle(draw, (150, 480), 80, fill=None, outline=(245, 158, 11, 20), width=2)

# ===== 主卡片 =====
card_x, card_y, card_w, card_h = 60, 50, 680, 500
draw_rounded_rect(draw, (card_x, card_y, card_x+card_w, card_y+card_h),
                  radius=20, fill=COLORS["bg_card"], outline=(30, 50, 90), width=1)

# ===== 左侧头像区 =====
avatar_x, avatar_y = 120, 120
# 外圈光环
draw_circle(draw, (avatar_x, avatar_y), 80, fill=None, outline=COLORS["primary"], width=2)
draw_circle(draw, (avatar_x, avatar_y), 70, fill=None, outline=COLORS["secondary"], width=1)
# 头像底圆
draw_circle(draw, (avatar_x, avatar_y), 60, fill="#1a1f4e")
# 笑脸
draw_circle(draw, (avatar_x, avatar_y), 50, fill="#1e2456")
# 眼睛
draw_circle(draw, (avatar_x-15, avatar_y-8), 6, fill=COLORS["primary"])
draw_circle(draw, (avatar_x+15, avatar_y-8), 6, fill=COLORS["primary"])
draw_circle(draw, (avatar_x-15, avatar_y-8), 2, fill="#fff")
draw_circle(draw, (avatar_x+15, avatar_y-8), 2, fill="#fff")
# 微笑
import math
for i in range(20):
    angle = math.pi * (0.3 + 0.4 * i / 19)
    x = avatar_x - 15 + 18 * math.cos(angle)
    y = avatar_y + 10 + 12 * math.sin(angle)
    draw.point((x, y), fill=COLORS["primary"])

# ===== 右侧文字区 =====
text_x = 220
line_y = 120

# 标题：小盟
try:
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
    font_sub = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    font_body = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
except:
    font_title = ImageFont.load_default()
    font_sub = ImageFont.load_default()
    font_body = ImageFont.load_default()
    font_small = ImageFont.load_default()

draw.text((text_x, line_y), "小盟", fill=COLORS["primary"], font=font_title)
line_y += 55

draw.text((text_x, line_y), "AIOps 智能运维助手", fill=COLORS["accent"], font=font_sub)
line_y += 35

# 分隔线
draw.line([(text_x, line_y), (text_x + 380, line_y)], fill=(40, 60, 120), width=1)
line_y += 25

# 身份信息标签
labels = [
    ("🏢", "山东省城市商业银行合作联盟"),
    ("🏗", "生产运维部 · 运维工具支撑组"),
    ("👨‍💻", "开发者：李龙"),
    ("🎯", "专注智能运维（AIOps）"),
]
for icon, text in labels:
    draw.text((text_x, line_y), f"{icon}  {text}", fill=COLORS["text"], font=font_body)
    line_y += 30

line_y += 15

# ===== 底部能力卡片 =====
cards = [
    ("🔧", "运维管理", "命令执行\n任务调度\n系统监控", COLORS["primary"]),
    ("📚", "知识管理", "知识库\n记忆检索\n故障复盘", COLORS["success"]),
    ("📊", "报告分析", "文档分析\n数据洞察\nHTML报告", COLORS["accent"]),
    ("🤖", "多智能体", "监控·运维\n分析·知识\n报告·界面", COLORS["secondary"]),
]

card_start_y = 340
card_width = 145
card_height = 170
card_gap = 15
total_width = 4 * card_width + 3 * card_gap
start_x = (WIDTH - total_width) // 2

for i, (icon, title, desc, color) in enumerate(cards):
    cx = start_x + i * (card_width + card_gap)
    cy = card_start_y

    draw_rounded_rect(draw, (cx, cy, cx+card_width, cy+card_height),
                      radius=12, fill="#1a1f4e", outline=(40, 60, 120), width=1)

    # 顶部色条
    draw_rounded_rect(draw, (cx+10, cy+8, cx+card_width-10, cy+12),
                      radius=4, fill=color)

    # 图标
    draw.text((cx + card_width//2 - 15, cy + 25), icon, fill=color, font=font_sub)

    # 标题
    tw = font_body.getlength(title) if hasattr(font_body, 'getlength') else len(title) * 10
    draw.text((cx + (card_width - tw) // 2, cy + 58), title, fill=color, font=font_body)

    # 描述
    desc_lines = desc.split("\n")
    dy = cy + 88
    for dl in desc_lines:
        dw = font_small.getlength(dl) if hasattr(font_small, 'getlength') else len(dl) * 7
        draw.text((cx + (card_width - dw) // 2, dy), dl, fill=COLORS["text_dim"], font=font_small)
        dy += 22

# ===== 底部标语 =====
footer_text = "让运维不再枯燥的救火，而是一场从容不迫的协奏"
fw = font_small.getlength(footer_text) if hasattr(font_small, 'getlength') else len(footer_text) * 7
draw.text(((WIDTH - fw) // 2, 530), footer_text, fill=COLORS["text_dim"], font=font_small)

# ===== 保存 =====
img.save(OUTPUT_PATH)
print(f"✅ 图片已生成: {OUTPUT_PATH}")
print(f"   尺寸: {WIDTH}x{HEIGHT}")
