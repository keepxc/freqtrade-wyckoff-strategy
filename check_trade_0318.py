import zipfile, json, sys, glob, os

# 找最新结果
results_dir = os.path.join(os.path.dirname(__file__), "user_data", "backtest_results")
zips = sorted(glob.glob(os.path.join(results_dir, "backtest-result-2026-03-23_14-19-33.zip")))
if not zips:
    print("找不到结果文件")
    sys.exit(1)

zip_path = zips[-1]
print(f"读取: {zip_path}")

with zipfile.ZipFile(zip_path, "r") as z:
    names = z.namelist()
    json_name = [n for n in names if n.endswith(".json") and "meta" not in n][0]
    with z.open(json_name) as fh:
        data = json.load(fh)

strat = list(data["strategy"].values())[0]
trades = strat["trades"]

print(f"\n总交易笔数: {len(trades)}")

# 找 2026-03-18 全部入场
target = [t for t in trades if "2026-03-18" in str(t.get("open_date", ""))]
print(f"\n=== 2026-03-18 入场交易 ({len(target)} 笔) ===")
for t in target:
    print(f"  open={t.get('open_date')}  tag={t.get('enter_tag'):<30}  rate={t.get('open_rate')}  profit={round(t.get('profit_ratio',0)*100,2)}%  close={t.get('close_date')}")

# 找 2026-03-17~19 全部
nearby = [t for t in trades if any(d in str(t.get("open_date","")) for d in ["2026-03-17","2026-03-18","2026-03-19"])]
print(f"\n=== 2026-03-17~19 全部交易 ({len(nearby)} 笔) ===")
for t in nearby:
    print(f"  open={t.get('open_date')}  tag={t.get('enter_tag'):<30}  rate={t.get('open_rate')}  profit={round(t.get('profit_ratio',0)*100,2)}%")

# 专门搜 72849 附近
print(f"\n=== 开仓价在 72800~72900 的交易 ===")
for t in trades:
    r = t.get("open_rate", 0)
    if 72800 <= r <= 72900:
        print(f"  open={t.get('open_date')}  tag={t.get('enter_tag')}  rate={r}  profit={round(t.get('profit_ratio',0)*100,2)}%")
