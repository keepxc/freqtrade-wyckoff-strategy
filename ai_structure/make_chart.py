# -*- coding: utf-8 -*-
"""生成喂给多模态 AI 的双栏图：上=日线（长结构），下=4H（近两个月）。

刻意**不加任何标注**（不画支撑阻力、不画预测路径、不写结论），
避免污染 AI 的独立判断。人读的带标注版另由 make_chart_annotated.py 生成。

跨平台：字体按 Linux → Windows 顺序探测；数据只依赖标准库 csv。
"""
import csv
import datetime
import math
import os
import sys
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFont

import config as C

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT = os.path.join(C.OUT_DIR, "chart_dual.png")

UP = (38, 166, 154)
DN = (239, 83, 80)
INK = (19, 23, 34)
GRID = (232, 236, 241)
SUB = (120, 130, 145)
BG = (255, 255, 255)
LAST = (41, 98, 255)
FRAME = (206, 213, 222)

W = C.CHART_W
H = 1200
ML, MR = 18, 112
PL, PR = ML, W - MR

# 上栏（日线）/ 下栏（4H）
P1T, P1B = 62, 700
P2T, P2B = 752, 1128

LATIN = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
         "/usr/share/fonts/TTF/DejaVuSans.ttf",
         "C:/Windows/Fonts/segoeui.ttf",
         "C:/Windows/Fonts/arial.ttf"]
CJK = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
       "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
       "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
       "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
       "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
       "/usr/share/fonts/truetype/arphic/uming.ttc",
       "/System/Library/Fonts/PingFang.ttc",
       "C:/Windows/Fonts/msyh.ttc",
       "C:/Windows/Fonts/simhei.ttf"]


def _first(paths, sz):
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except OSError:
                continue
    return ImageFont.load_default()


def lat(sz):
    return _first(LATIN, sz)


def cjk(sz):
    """含中文字形的字体。DejaVu 缺 CJK，中文会渲染成方块。"""
    return _first(CJK + LATIN, sz)


# ---------------------------------------------------------------- 数据
def read_bars():
    rows = []
    with open(C.CSV, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append((int(r["ts"]), float(r["open"]), float(r["high"]),
                         float(r["low"]), float(r["close"])))
    rows.sort(key=lambda x: x[0])
    return rows


def agg(rows, hours):
    """把 1h 聚合成 hours 小时的 K 线（按 UTC 对齐）。"""
    step = hours * 3_600_000
    out = OrderedDict()
    for ts, o, h, l, c in rows:
        k = ts // step
        if k in out:
            b = out[k]
            b[2] = max(b[2], h)
            b[3] = min(b[3], l)
            b[4] = c
        else:
            out[k] = [k * step, o, h, l, c]
    bars = sorted(out.values(), key=lambda x: x[0])
    return bars


def window(bars, days):
    if not bars:
        return bars
    cut = bars[-1][0] - days * 86_400_000
    return [b for b in bars if b[0] >= cut]


# ---------------------------------------------------------------- 绘制
def panel(d, bars, top, bot, label, sub, xlab_font, t_font):
    n = len(bars)
    if n < 2:
        return
    lo = min(b[3] for b in bars)
    hi = max(b[2] for b in bars)
    pad = (hi - lo) * 0.06 or hi * 0.01
    lo, hi = lo - pad, hi + pad

    def X(i):
        return PL + (i + 0.5) * (PR - PL) / n

    def Y(p):
        return bot - (p - lo) / (hi - lo) * (bot - top)

    # 网格 + 右侧价格轴（步长取 1/2/2.5/5/10 × 10^n 中最接近的一个）
    span = hi - lo
    raw = span / 6
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if raw / mag <= m),
                10 * mag)
    p = int(lo // step) * step
    while p <= hi:
        if p >= lo:
            y = Y(p)
            d.line([(PL, y), (PR, y)], fill=GRID, width=1)
            d.text((PR + 10, y - 8), f"{p:,.0f}", font=lat(19), fill=SUB)
        p += step

    # 时间轴：长窗口按月打点，短窗口按日打点。
    # 必须防重叠——窗口起点常落在月中，首个月标签与下一个月标签可能只隔几根 K 线。
    last_key, last_right = None, -1e9
    for i, b in enumerate(bars):
        dt = datetime.datetime.fromtimestamp(b[0] / 1000, datetime.UTC)
        if n > 200:
            key, lab = dt.strftime("%Y-%m"), dt.strftime("%y/%m")
        else:
            key, lab = dt.strftime("%Y-%m-%d"), dt.strftime("%m/%d")
        if key != last_key:
            last_key = key
            x = X(i)
            d.line([(x, top), (x, bot)], fill=(246, 248, 250), width=1)
            if x > last_right + 14:
                d.text((x + 3, bot + 8), lab, font=lat(16), fill=SUB)
                last_right = x + 3 + d.textlength(lab, font=lat(16))

    # K 线
    cw = max(1.0, (PR - PL) / n)
    bw = max(1, int(cw * 0.62))
    for i, (_, o, h, l, c) in enumerate(bars):
        xi = int(round(X(i)))
        col = UP if c >= o else DN
        d.line([(xi, Y(h)), (xi, Y(l))], fill=col, width=1)
        y0, y1 = Y(max(o, c)), Y(min(o, c))
        if y1 - y0 < 1:
            y1 = y0 + 1
        if c >= o:
            d.rectangle([xi - bw // 2, y0, xi + bw // 2, y1], outline=col)
        else:
            d.rectangle([xi - bw // 2, y0, xi + bw // 2, y1], fill=col)

    # 最新价
    last = bars[-1][4]
    y = Y(last)
    d.line([(PL, y), (PR, y)], fill=LAST, width=1)
    d.rectangle([PR + 2, y - 11, W - 4, y + 11], fill=LAST)
    d.text((PR + 10, y - 9), f"{last:,.0f}", font=lat(19), fill=(255, 255, 255))

    # 栏目标签画在栏内左上角（TradingView 的做法），避免与页眉争位
    lab = f"{label}   {sub}"
    tw = d.textlength(lab, font=t_font)
    d.rectangle([PL + 6, top + 6, PL + 18 + tw, top + 42], fill=(255, 255, 255))
    d.text((PL + 12, top + 10), lab, font=t_font, fill=INK)
    d.rectangle([PL, top, PR, bot], outline=FRAME, width=1)


def main():
    rows = read_bars()
    if not rows:
        print(f"[!] 数据为空：{C.CSV}", file=sys.stderr)
        return 1
    daily = window(agg(rows, 24), C.DAILY_DAYS)
    h4 = window(agg(rows, 4), C.H4_DAYS)

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    t_font = cjk(26)

    f = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC)
    d.text((ML, 14), f"{C.SYMBOL}", font=cjk(28), fill=INK)
    d.text((ML + 190, 20),
           f"{f(rows[-1][0]):%Y-%m-%d %H:%M} UTC    "
           f"last {rows[-1][4]:,.0f}    "
           f"1h bars {len(rows):,}", font=lat(19), fill=SUB)
    lx = ML + 700
    d.rectangle([lx, 22, lx + 13, 34], outline=UP, width=2)
    d.text((lx + 20, 19), "Up", font=lat(19), fill=SUB)
    d.rectangle([lx + 74, 22, lx + 87, 34], fill=DN)
    d.text((lx + 94, 19), "Down", font=lat(19), fill=SUB)

    panel(d, daily, P1T, P1B, "1D", f"{len(daily)} bars", lat(16), t_font)
    panel(d, h4, P2T, P2B, "4H", f"{len(h4)} bars", lat(16), t_font)

    d.text((ML, H - 34),
           "Upper: daily candles. Lower: 4-hour candles. "
           "No annotations - read the price structure directly.",
           font=lat(18), fill=SUB)

    os.makedirs(C.OUT_DIR, exist_ok=True)
    img.save(OUT, optimize=True)
    sz = os.path.getsize(OUT)
    print(f"已写 {OUT}  {W}x{H}  {sz:,} 字节")
    print(f"  日线 {len(daily)} 根 / 4H {len(h4)} 根 / 1h {len(rows):,} 根")
    return 0


if __name__ == "__main__":
    sys.exit(main())
