"""
探索性——非结论，禁止引用为证据。

实测 VergeX trending-crypto 榜单（net_flow / oi）的变化频率：
  - 每 INTERVAL_SEC 采样一次，共 N_SAMPLES 次
  - 记录 top N 的 symbol 集合、rank、关键字段
  - 输出：榜单成员变化频率、排名变化频率、字段变化幅度

用途：为 AI500V1DryRun 数据源替换（trending-category -> trending-crypto）
      的退出/防抖设计提供"榜单多久变一次"的实测依据。
"""
import sys
import time
from datetime import datetime, timezone

import requests

URL = "https://vergex.trade/trending-crypto"
TABS = ["net_flow", "oi"]
LIMIT = 20
INTERVAL_SEC = 60
N_SAMPLES = int(sys.argv[1]) if len(sys.argv) > 1 else 30
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch(tab):
    r = requests.get(URL, params={"tab": tab, "limit": LIMIT},
                     headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()


def main():
    print(f"[{datetime.now(timezone.utc).isoformat()}] 开始采样: tabs={TABS}, "
          f"每 {INTERVAL_SEC}s 一次, 共 {N_SAMPLES} 次", flush=True)
    prev = {t: None for t in TABS}
    changes = {t: [] for t in TABS}

    for k in range(N_SAMPLES):
        ts = datetime.now(timezone.utc).isoformat()
        line_parts = [f"#{k + 1} {ts}"]
        for tab in TABS:
            try:
                data = fetch(tab)
                top = data.get("top") or []
                syms = [x["symbol"] for x in top[:5]]
                ranks = {x["symbol"]: x["rank"] for x in top}
                # 与前次对比：前5成员变化
                if prev[tab] is not None:
                    old_syms = prev[tab]
                    added = [s for s in syms if s not in old_syms]
                    removed = [s for s in old_syms if s not in syms]
                    # 排名位移（仅共同成员）
                    common = [s for s in syms if s in old_syms]
                    if common:
                        rank_shift = prev[tab + "_ranks"]
                        moves = [abs(ranks[s] - rank_shift[s]) for s in common if s in rank_shift]
                        avg_move = sum(moves) / len(moves) if moves else 0
                    else:
                        avg_move = 0
                    changes[tab].append({"added": len(added), "removed": len(removed),
                                         "avg_rank_move": avg_move})
                    change_tag = f"±{len(added)}/{len(removed)} 排名位移{avg_move:.1f}"
                else:
                    change_tag = "(首次)"
                prev[tab] = syms
                prev[tab + "_ranks"] = ranks
                line_parts.append(f"{tab}[{','.join(syms)}]{change_tag}")
            except Exception as e:
                line_parts.append(f"{tab}[FETCH_ERR {e}]")
        print("  ".join(line_parts), flush=True)
        if k < N_SAMPLES - 1:
            time.sleep(INTERVAL_SEC)

    # 汇总
    print("\n===== 变化频率汇总 =====", flush=True)
    for tab in TABS:
        cs = changes[tab]
        if not cs:
            print(f"{tab}: 无相邻采样对比数据", flush=True)
            continue
        n_changed = sum(1 for c in cs if c["added"] + c["removed"] > 0)
        avg_add = sum(c["added"] for c in cs) / len(cs)
        avg_move = sum(c["avg_rank_move"] for c in cs) / len(cs)
        print(f"{tab}: 相邻采样 {len(cs)} 对, 成员有变化的 {n_changed} 对 "
              f"({n_changed / len(cs):.0%}), 平均每对新增 {avg_add:.2f} 币, "
              f"共同成员平均排名位移 {avg_move:.2f}", flush=True)
    print("探索性——非结论，禁止引用为证据", flush=True)


if __name__ == "__main__":
    main()
