# BStocksHybridV1 Dry-Run 部署指南

对应 TASK109（`TASK_NEW/quant_system_tasks.md`）的 ✅ 采纳策略：`user_data/strategies/BStocksHybridV1DryRun.py`（半仓买入持有+半仓 Donchian 波段，含美股时段执行层过滤）。

## 0. 部署前必须检查的一项配置

`user_data/config_bstocks_dryrun.json` 里的：

```json
"ccxt_config": {"options": {"fetchMarkets": ["spot"]}, "httpsProxy": "http://host.docker.internal:7890"}
```

`httpsProxy` 是本机（开发机）为绕过本地网络 DNS 污染临时加的，**不是策略本身需要的配置**。在新服务器上：
- 如果该服务器能直接访问 `api.binance.com`，**必须删掉 `httpsProxy` 这一行**，否则会尝试连一个不存在的本地代理端口，直接连接失败。
- 如果新服务器也需要代理，把地址换成该服务器上实际可用的代理地址（不能沿用 `host.docker.internal:7890`，那是本机专属）。

## 1. 拉取代码后需要补的数据

`user_data/data/` 整个目录在 `.gitignore` 里，**不会随 git push 一起同步**（正股历史数据+币安K线数据体积大，且非项目代码）。新服务器 clone 后需要重新生成：

```bash
python scripts/prepare_bstocks_data.py     # 16支 bStocks 正股历史（Nasdaq官方API代理）
python scripts/prepare_ndx_top10.py        # 10支纳指前十点位对照数据（可选，仅供研究对比，非交易必需）
```

这两个脚本会把数据写到 `user_data/data/binance/*.feather`，命名与 `config_bstocks_dryrun.json` 里的 pair_whitelist（`NVDAB/USDT` 等 `B` 后缀命名）一致。

## 2. 启动 dry-run

本策略未接入现有 `docker-compose.yml`（该文件是 WyckoffV8 专用，不要改动，避免影响其 dry-run）。用独立的 `docker run` 命令启动：

```bash
docker run -d --name freqtrade_bstocks_hybrid_dryrun \
  --restart unless-stopped \
  -v "$(pwd)/user_data:/freqtrade/user_data" \
  --add-host host.docker.internal:host-gateway \
  -p 127.0.0.1:8082:8081 \
  freqtradeorg/freqtrade:stable trade \
  --logfile /freqtrade/user_data/logs/freqtrade_bstocks_hybrid.log \
  --db-url sqlite:////freqtrade/user_data/tradesv3.bstocks_hybrid_dryrun.sqlite \
  --config /freqtrade/user_data/config_bstocks_dryrun.json \
  --strategy BStocksHybridV1DryRun \
  --strategy-path /freqtrade/user_data/strategies
```

查看日志：`docker logs -f freqtrade_bstocks_hybrid_dryrun`
停止：`docker stop freqtrade_bstocks_hybrid_dryrun && docker rm freqtrade_bstocks_hybrid_dryrun`

## 3. 已知限制（部署前必读，避免误判为故障）

- **回测该策略文件会显示 0 笔交易，这是预期行为，不是 bug**——`BStocksHybridV1DryRun` 的美股时段过滤 (`confirm_trade_entry`) 依赖 live 模式下的持续轮询才能命中开盘窗口，1d 回测只在 00:00 UTC 评估一次信号，必然落在窗口外。已验证的回测结论以不含该过滤器的 `BStocksHybridV1.py`（TASK109）为准，dry-run 只是把同一套已验证信号/仓位逻辑接入执行层时间限制。
- `config_bstocks_dryrun.json` 的 `api_server.username`/`password`/`jwt_secret_key` 是占位符 `changeme_bstocks_dryrun`，如果要把 `8082` 端口暴露到 `127.0.0.1` 之外（比如接 FreqUI 远程访问），**必须先换成真实密码**，否则等于裸奔。当前配置只绑定 `127.0.0.1`，同机访问相对安全，但仍建议换密码。
- NYSE 假期表硬编码到 2027 年，2028 起需要手工更新 `BStocksHybridV1DryRun.py` 里的 `NYSE_HOLIDAYS`（当前 Docker 镜像没有 `pandas_market_calendars`，见该文件 docstring）。
- 16支标的子集仍非币安 bStocks 全部46+支标的（TASK108/109 已声明，此处延续适用）。
