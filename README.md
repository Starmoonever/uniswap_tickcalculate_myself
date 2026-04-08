# Uniswap Tick Calculate

这个项目用于离线分析 Uniswap V3 池子的价格推进过程，并结合链上 Tick 边界与 Mint/Burn 事件回放价格变化。

## 运行环境

- Python 3.9+
- 建议使用 `uv`

## 安装依赖

```bash
uv sync
```

## 配置 RPC

不要把真实 RPC 地址直接写进代码或提交到 GitHub。

可以在本地环境中设置：

```bash
export RPC_URL="https://your-ethereum-rpc.example"
```

也可以参考仓库里的 `.env.example` 自行维护本地配置文件，但不要提交真实 `.env`。

## 运行

```bash
uv run python work_price_pre.py
```

## 数据来源说明

`work_price_pre.py` 默认一定会读取本地的 `work_testdata.csv`，从里面提取两笔 Swap 以及它们之间的 Mint/Burn 事件。

脚本还支持可选的链上 RPC 数据：

- 如果配置了 `RPC_URL`，脚本会通过 Web3 连接以太坊 JSON-RPC，并调用 Uniswap V3 池合约的 `ticks()`，补充当前价格区间附近已经初始化的 Tick 边界和 `liquidityNet`。
- 如果没有配置 `RPC_URL`，脚本会跳过链上扫描，只使用 CSV 里的数据继续计算。

所以这个脚本不是“永远只用 testdata”。它的实际行为分两种：

- 未配置 `RPC_URL`：只用 `work_testdata.csv`
- 已配置 `RPC_URL`：同时用 `work_testdata.csv` 和 RPC 返回的链上 Tick 数据

需要注意，`RPC_URL` 不是普通 REST API 数据源，而是以太坊节点的 JSON-RPC 地址。

## 仓库建议提交内容

建议保留：

- 源代码
- `pyproject.toml`
- `uv.lock`
- 说明文档
- 作为示例的公开测试数据

建议不要提交：

- `.venv/`
- `.env`
- 本地缓存和临时文件
- 任何私有 RPC、密钥、令牌或账号信息
