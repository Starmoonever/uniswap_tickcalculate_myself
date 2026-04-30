# Internship Project: Uniswap V3 Tick Price Prediction

## 1. 项目概述

本项目是一个面向 Uniswap V3 的链上交易状态复现与价格预测工具。项目核心目标是：在已知某个 Uniswap V3 池子相邻两笔 Swap 事件及其之间 Mint/Burn 流动性事件的情况下，使用链上日志和 RPC 数据恢复池子的流动性结构，并模拟后一笔 Swap 对价格和流动性的影响，最终与链上真实结果进行对比验证。

当前项目默认分析的池子是以太坊主网上的 Uniswap V3 USDC/WETH 0.05% 池：

```text
0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
```

项目通过 Python 实现，主要依赖：

- `pandas`：读取、清洗、排序和截取链上日志 CSV 数据；
- `web3`：连接以太坊 RPC，并调用 Uniswap V3 池合约的 `ticks()` 方法；
- `decimal`：进行高精度定点数计算，减少价格模拟中的精度损失；
- ClickHouse HTTP 接口：从日志表中拉取指定池子的链上事件数据。

这个项目的本质不是简单读取价格，而是尝试复现 Uniswap V3 在链上执行 Swap 时的局部定价过程。它把 V3 中“价格在 Tick 区间内连续变化，流动性在 Tick 边界处离散变化”的机制拆解成可验证的计算流程。

## 2. 项目背景

Uniswap V2 的流动性分布在整条价格曲线上，交易价格可以通过统一的常量乘积模型理解：

```text
x * y = k
```

而 Uniswap V3 引入了集中流动性机制。流动性提供者可以只在某个价格区间内提供流动性，例如只在 `[tickLower, tickUpper)` 范围内生效。这带来了两个重要变化：

- 当前价格所在 Tick 区间内，有效流动性 `L` 可以视为常数；
- 当价格跨越某个 Tick 边界时，活跃流动性会根据该边界记录的 `liquidityNet` 发生变化。

因此，一笔 Swap 的价格变化不能再只用一条全局曲线直接计算，而需要按 Tick 边界分段推进。若交易量足够大，价格可能跨过多个 Tick，每跨一个边界都要更新当前有效流动性，再继续计算下一段价格变化。

本项目围绕这个机制，完成了从链上原始日志到 Swap 结果复现的完整流程。

## 3. 项目目标

项目主要完成以下目标：

1. 从 ClickHouse 中拉取指定 Uniswap V3 池子的链上事件日志，生成本地测试数据。
2. 从 CSV 日志中识别 Swap、Mint、Burn 等关键事件，并按照区块号和交易内序号恢复真实执行顺序。
3. 以前一笔 Swap 结束后的状态作为起点，解析出 `sqrtPriceX96`、当前有效流动性 `liquidity` 和当前 `tick`。
4. 结合链上 RPC 查询和 CSV 中间事件，恢复下一笔 Swap 路径上的流动性边界变化。
5. 根据 Uniswap V3 的 Tick 分段定价公式，模拟下一笔 Swap 的价格推进过程。
6. 将模拟得到的最终 `sqrtPriceX96`、流动性和人类可读价格与链上真实 Swap 结果进行对比，验证算法正确性。

## 4. 项目结构

项目文件结构如下：

```text
Uniswap_tickcalculate/
├── tick_prediction.py      # 核心算法：日志读取、事件解析、RPC 扫描、Swap 分段模拟与结果验证
├── get_testdata.py         # 数据获取脚本：从 ClickHouse 拉取指定池子的日志并保存为 data_test.csv
├── config_utils.py         # 配置读取工具：解析本地 config.yaml
├── config.yaml.example     # 配置模板：RPC、ClickHouse 等连接信息示例
├── README.md               # 算法原理说明与公式推导
├── pyproject.toml          # Python 项目依赖声明
└── uv.lock                 # 依赖锁文件
```

其中，最核心的文件是 `tick_prediction.py`，它承担从数据读取到模拟验证的完整计算逻辑。

## 5. 数据来源与数据格式

项目使用两类数据：

### 5.1 本地 CSV 事件日志

`get_testdata.py` 会从 ClickHouse 的 `rrmt_logs` 表中读取指定池子的日志数据，并保存为 `data_test.csv`。查询字段包括：

- `Hash`：交易哈希；
- `BlockNumber`：区块号；
- `Address`：事件所属合约地址；
- `TopicHash`：事件签名哈希；
- `Topics`：事件 indexed 参数；
- `Data`：事件非 indexed 参数编码；
- `CreatedAt`：事件时间；
- `Serial`：日志在区块或交易中的顺序；
- `BlockHash`：区块哈希。

这些字段被定义在 `tick_prediction.py` 的 `LOG_COLUMNS` 中。脚本读取 CSV 后，会按照 `BlockNumber` 和 `Serial` 排序，保证事件处理顺序与链上执行顺序一致。

### 5.2 链上 RPC 数据

CSV 中记录了某一段时间内发生的事件，但仅依靠中间 Mint/Burn 事件无法知道起点之前已经存在的 Tick 流动性边界。因此项目还会通过 Web3 调用 Uniswap V3 池合约的：

```solidity
ticks(int24 tick)
```

从链上读取指定 Tick 的存量信息，重点使用其中的：

- `liquidityNet`：穿越该 Tick 时有效流动性的净变化；
- `initialized`：该 Tick 是否已经初始化。

RPC 查询时会指定 `block_identifier`，也就是以前一笔 Swap 所在区块作为状态基准，避免读取到未来状态。

## 6. 配置管理

项目使用本地 `config.yaml` 保存敏感配置，例如：

- `rpc_url`：以太坊 JSON-RPC 地址；
- `clickhouse.host`；
- `clickhouse.port`；
- `clickhouse.user`；
- `clickhouse.password`；
- `clickhouse.database`。

为了避免把真实连接信息提交到仓库，项目提供了 `config.yaml.example` 作为模板，并在 `.gitignore` 中忽略真实的 `config.yaml` 和 CSV 数据文件。

`config_utils.py` 实现了一个轻量级 YAML 配置解析器。它支持：

- 普通键值对；
- 缩进表示的嵌套配置；
- 字符串、整数、布尔值和 `null` 的基础解析；
- 注释和空行跳过。

由于项目配置格式比较简单，所以没有额外引入完整 YAML 解析库，而是用少量代码满足当前需求。

## 7. 核心算法流程

项目的核心流程可以概括为：

```text
读取日志 -> 选择相邻 Swap 区间 -> 解析起始状态 -> 恢复流动性边界 -> 分段模拟 Swap -> 对比链上结果
```

### 7.1 日志读取与排序

`load_logs_dataframe()` 负责读取 CSV 数据。它会处理两种情况：

- CSV 文件已经带有表头；
- CSV 内容不带表头，需要手动指定 `LOG_COLUMNS`。

随后，函数会把 `BlockNumber` 和 `Serial` 转换为整数，并按照这两个字段排序。这一步非常重要，因为 Mint、Burn、Swap 的处理必须严格遵循链上真实顺序。

### 7.2 选择测试区间

`get_data_from_csv()` 会从完整日志中定位第 `start_n` 笔 Swap 到第 `end_n` 笔 Swap，并截取这两笔 Swap 之间的全部事件。

这样得到的数据片段包含：

- 第一笔 Swap：作为模拟起点；
- 中间 Mint/Burn：作为流动性变化增量；
- 第二笔 Swap：作为需要模拟和验证的目标。

### 7.3 解析起始 Swap 状态

`run_automated_test()` 会把第一行事件视为上一笔 Swap 结束后的状态，并从 `Data` 字段中解析：

- `curr_sp`：当前 `sqrtPriceX96`；
- `curr_l`：当前有效流动性；
- `curr_tick`：当前 Tick；
- `start_block`：用于 RPC 查询的区块号。

其中 `sqrtPriceX96` 是 Uniswap V3 链上保存价格的核心格式：

```text
sqrtPriceX96 = sqrt(P) * 2^96
```

项目使用 `decimal.Decimal` 并设置 100 位精度，以尽量贴近链上定点数计算。

### 7.4 恢复存量 Tick 边界

`fetch_tick_changes_from_rpc()` 会根据当前 Tick 和目标 Tick，确定价格可能经过的 Tick 范围，并按 `tick_spacing=10` 枚举。

对于每个 Tick，脚本调用池合约的 `ticks(tick)`：

- 如果该 Tick 已初始化；
- 且 `liquidityNet != 0`；

就把该 Tick 的 `liquidityNet` 记录到 `tick_net_changes` 中。

这一步恢复的是起始区块时已经存在的流动性结构，也就是“历史存量”。

### 7.5 叠加中间 Mint/Burn 变化

项目会扫描两笔 Swap 之间的所有事件。如果事件是 Mint 或 Burn，就解析其 `tickLower`、`tickUpper` 和流动性数量。

Mint 的处理规则是：

```text
tickLower: +amount
tickUpper: -amount
```

Burn 的处理规则相反：

```text
tickLower: -amount
tickUpper: +amount
```

这些变化会叠加到 `tick_net_changes` 中，用于后续跨 Tick 时更新流动性。

同时，如果某个 Mint/Burn 区间覆盖当前价格，即：

```text
tickLower <= curr_tick < tickUpper
```

那么它会立即影响当前有效流动性 `curr_l`：

- Mint 覆盖当前价格：当前活跃流动性增加；
- Burn 覆盖当前价格：当前活跃流动性减少。

这一步区分了两个概念：

- `curr_l`：当前已经生效的活跃流动性；
- `tick_net_changes`：未来跨越 Tick 边界时才会触发的流动性变化。

### 7.6 判断 Swap 方向与有效输入量

项目从第二笔 Swap 的 `Data` 字段中解析 `amount0`，并根据 `amount0` 的正负判断价格方向：

- `amount0 > 0`：token0 流入池子，价格向下；
- `amount0 < 0`：token0 流出池子，价格向上。

由于当前池子是 0.05% 费率池，项目会先扣除手续费：

```text
rem_amt0 = amount0 * 0.9995
```

扣完手续费后的 `rem_amt0` 才是实际参与价格推进的有效数量。

### 7.7 按 Tick 分段推进价格

项目会根据方向筛选出价格路径上可能经过的 Tick 边界：

- 下跌：只关注当前 Tick 下方的边界，并按从高到低排序；
- 上涨：只关注当前 Tick 上方的边界，并按从低到高排序。

对于每个边界，脚本计算从当前价格推进到该边界需要消耗多少 token0：

```text
dx = L * |sqrtP_edge - sqrtP_current| * Q96 / (sqrtP_edge * sqrtP_current)
```

如果剩余输入量足够跨越该边界，则：

1. 扣除这段消耗；
2. 将当前价格更新为边界价格；
3. 根据 `liquidityNet` 更新当前有效流动性；
4. 继续处理下一个边界。

跨 Tick 后的流动性更新方向取决于价格移动方向：

- 价格向上穿越 Tick：`L = L + liquidityNet`；
- 价格向下穿越 Tick：`L = L - liquidityNet`。

这正是 Uniswap V3 集中流动性的核心机制。

### 7.8 尾部区间精确计算

如果剩余输入量不足以跨越下一个 Tick，说明最终价格落在当前 Tick 区间内部。此时流动性 `L` 不再变化，项目使用区间内公式计算最终 `sqrtPriceX96`：

```text
sqrtP_next = L * sqrtP * Q96 / (L * Q96 + amount0 * sqrtP)
```

最终得到的 `curr_sp` 就是模拟出来的第二笔 Swap 结束价格。

## 8. 结果验证

项目会把模拟结果与第二笔 Swap 事件中真实记录的链上状态对比。

主要验证指标包括：

### 8.1 流动性验证

比较模拟结束后的 `curr_l` 与链上事件中的 `actual_l2`。

如果二者一致，说明：

- Tick 穿越顺序正确；
- `liquidityNet` 更新方向正确；
- 中间 Mint/Burn 事件处理正确；
- 起始活跃流动性修正正确。

### 8.2 `sqrtPriceX96` 验证

比较模拟得到的 `sqrtPriceX96` 与链上真实 `sqrtPriceX96`。

这是最直接、最精确的验证方式，因为 `sqrtPriceX96` 就是 Uniswap V3 在链上使用的价格存储格式。

### 8.3 人类可读价格验证

项目还会把 `sqrtPriceX96` 转换为 USDC/ETH 价格：

```text
USDC/ETH = 1 / (sqrtPriceX96 / Q96)^2 * 10^12
```

其中 `10^12` 来自 USDC 6 位小数与 WETH 18 位小数之间的小数位差。

最终输出包括：

- 计算流动性；
- 实际流动性；
- 流动性是否匹配；
- 计算 `sqrtPriceX96`；
- 实际 `sqrtPriceX96`；
- X96 误差；
- 计算 USDC/ETH 价格；
- 实际 USDC/ETH 价格；
- 价格误差。

## 9. 主要实现细节

### 9.1 事件签名识别

项目通过 TopicHash 区分不同事件：

- `Swap`：`0xc42079f9...`;
- `Mint`：`0x7a53080b...`;
- `Burn`：`0x0c396cd9...`。

这样可以从通用日志表中筛选出与 Uniswap V3 状态变化相关的事件。

### 9.2 int256 解码

链上日志中的有符号整数以 256 位补码形式编码。`decode_int256()` 会先按无符号整数解析十六进制字符串，再判断是否超过 `2^255`：

```text
if value >= 2^255:
    value -= 2^256
```

这使得脚本可以正确解析负数，例如 Swap 中的 `amount0`、`amount1` 和 Tick。

### 9.3 Tick 到价格转换

`tick_to_sqrtP()` 根据 Uniswap V3 的定义，将 Tick 转换为 `sqrtPriceX96`：

```text
sqrtPriceX96 = 1.0001^(tick / 2) * 2^96
```

这一步用于计算价格推进到某个 Tick 边界时的目标平方根价格。

### 9.4 高精度计算

项目使用：

```python
decimal.getcontext().prec = 100
```

这是因为 Uniswap V3 的价格和流动性数据非常大，且 `sqrtPriceX96` 是高精度定点数。如果直接使用普通浮点数，可能产生明显误差，影响最终验证结果。

### 9.5 CSV 与 RPC 结合

项目没有只依赖 CSV，也没有只依赖 RPC，而是结合了两者：

- CSV 负责提供真实事件时间线和中间增量变化；
- RPC 负责补全起点时已经存在的存量 Tick 边界。

这种设计能更接近链上真实执行环境。

## 10. 实习项目中的技术收获

通过该项目，我完成了从链上数据获取、事件解析、协议机制理解到算法验证的一整套工作，主要收获包括：

1. 深入理解 Uniswap V3 集中流动性的核心机制，尤其是 Tick、`sqrtPriceX96`、`liquidity`、`liquidityNet` 之间的关系。
2. 学会从链上事件日志中解析协议状态，而不是只依赖现成 API 或前端展示数据。
3. 理解了 Swap 价格变化需要按 Tick 边界分段计算，不能简单使用全局常量乘积公式。
4. 掌握了 Web3 RPC 调用方式，包括指定历史区块读取合约状态。
5. 使用 ClickHouse 拉取和整理大规模链上日志，完成本地可复现测试数据构建。
6. 使用 Python 的 `decimal` 模块处理高精度金融计算，避免浮点误差影响验证。
7. 建立了“模拟结果必须和链上真实结果对齐”的验证思路，提高了对 DeFi 协议实现细节的敏感度。

## 11. 项目成果

项目最终实现了一个可运行的 Uniswap V3 Swap 复现工具，具备以下能力：

- 自动读取指定池子的链上事件日志；
- 自动定位任意两笔 Swap 之间的完整事件区间；
- 自动解析 Swap、Mint、Burn 事件；
- 通过 RPC 补全链上存量 Tick 流动性边界；
- 根据 Tick 边界分段模拟 Swap 价格变化；
- 输出模拟值与链上真实值的误差；
- 支持对不同 Swap 区间进行测试和验证。

这个工具可以用于研究 Uniswap V3 的价格形成机制，也可以作为后续构建链上交易模拟、MEV 分析、流动性风险评估或 DeFi 数据回放系统的基础模块。

## 12. 当前限制与后续优化方向

当前项目已经完成核心算法验证，但仍有一些可以继续优化的方向：

1. 池子地址和手续费目前写在代码中，后续可以改为配置项，支持更多 Uniswap V3 池子。
2. 当前 Tick 扫描使用固定 `tick_spacing=10`，后续可以根据池子 fee tier 自动读取对应 spacing。
3. 当前主要围绕 token0 输入方向进行计算，后续可以进一步补全 token1 方向下的完整模拟公式。
4. 可以引入正式的 ABI 解码库，减少手动切片解析事件 `Data` 和 `Topics` 的复杂度。
5. 可以增加单元测试，将日志解析、Tick 转换、流动性更新、价格推进等逻辑拆分验证。
6. 可以封装命令行参数，让用户选择池子、起止 Swap 序号、CSV 路径和区块范围。
7. 可以把结果输出为结构化 JSON 或报告文件，便于批量回测和数据分析。

## 13. 总结

本实习项目围绕 Uniswap V3 的核心定价机制，实现了一个从链上日志出发、恢复流动性结构并复现 Swap 价格变化的 Python 工具。项目不仅涉及数据工程中的日志获取和清洗，也涉及 DeFi 协议中的数学模型、链上状态读取和高精度数值计算。

通过这个项目，我把 Uniswap V3 中较抽象的 Tick、集中流动性和 `sqrtPriceX96` 机制转化为了可运行、可验证的代码流程，并通过与真实链上 Swap 结果对比来检验算法正确性。这使项目既有工程落地价值，也有较强的协议研究价值。
