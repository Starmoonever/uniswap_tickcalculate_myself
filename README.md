# Uniswap Tick Calculate

## 数据来源说明

`tick_prediction.py` 默认会读取本地的 `work_testdata.csv`，从里面提取两笔 Swap 以及它们之间的 Mint/Burn 事件。

脚本会同时使用本地 CSV 和链上 RPC 数据：

- 需要在本地 `.env` 或环境变量中配置 `RPC_URL`
- 通过 Web3 连接以太坊 JSON-RPC，并调用 Uniswap V3 池合约的 `ticks()`，补充当前价格区间附近已经初始化的 Tick 边界和 `liquidityNet`


---

# Uniswap V3 交易价格算法原理

## 1. Uniswap V3 的价格计算

在 Uniswap V2 中，池子的价格变化可以简单理解为在一条统一的常量乘积曲线上移动，即：

$$
x \cdot y = k
$$

### 虚拟存量
在 Uniswap V3 中，可以把当前活跃价格区间近似理解为带有**虚拟存量**的恒定乘积模型：

$$(x + x_v)(y + y_v) = k$$

其中：

- $x, y$：池子中的真实资产数量
- $x_v, y_v$：由当前流动性和价格决定的虚拟存量
- $k$：常数乘积
 
虽然 Uniswap V3 的真实流动性只分布在有限价格区间内，但在**当前活跃区间**里，仍然可以等价看作一个经过虚拟存量修正后的恒定乘积做市池，因此价格计算仍然遵循局部的乘积不变关系。

在 Uniswap V3 中，流动性不再均匀分布在整个价格区间，而是由 LP（流动性提供者）按自己的意愿部署在不同的价格范围内。  
因此，池子的价格变化不再是“一条全程连续、流动性固定的曲线”，而是：

- 在**每一个 Tick 区间内部**，流动性 $L$ 视为常数；
- 一旦价格跨越某个 Tick 边界，活跃流动性会立刻变化；
- 所以整个价格路径实际上是由多个“**局部连续 + 边界跳变**”的片段拼接而成。

Uniswap V3 价格计算的本质：

> **价格在区间内连续变化，流动性在边界处离散变化。**

从一笔 Swap 推导下一笔 Swap 的结果,需要：

1. 取得上一次 Swap 结束时的状态；
2. 恢复这段时间内流动性边界的变化；
3. 按 Tick 边界逐段推进价格；
4. 在最后一个未跨出的区间内计算精确终值。

---

## 2. Uniswap V3 中核心指标

### 2.1 `sqrtPriceX96` 

Uniswap V3 不直接存储价格 $P$，而是存储价格的平方根，并乘以 $2^{96}$ 后以整数形式保存：

$$
\text{sqrtPriceX96} = \sqrt{P} \cdot 2^{96}
$$

记：

$$
Q96 = 2^{96}
$$

那么实际平方根价格为：

$$
\sqrt{P} = \frac{\text{sqrtPriceX96}}{Q96}
$$

实际价格则为：

$$
P = \left(\frac{\text{sqrtPriceX96}}{Q96}\right)^2
$$

通过 `curr_sp` 保存当前的 `sqrtPriceX96`。为了把价格转成 `USDC/ETH`的形式，则只需通过下面的计算：

$$
\text{humanPrice} = \frac{1}{(\text{sqrtPriceX96}/Q96)^2} \times 10^{12}
$$

此处 $10^{12}$ 是由 USDC（6位）和 ETH（18位）的小数位差带来的换算项。

---

### 2.2 Tick

Tick 可以理解为价格网格上的整数索引。  
Uniswap V3 规定每个 Tick 对应一个固定价格：

$$
P = 1.0001^{\text{Tick}}
$$

因此平方根价格为：

$$
\sqrt{P} = \sqrt{1.0001^{\text{Tick}}} = 1.0001^{\text{Tick}/2}
$$

换成 `sqrtPriceX96` 形式：

$$
\text{sqrtPriceX96} = 1.0001^{\text{Tick}/2} \cdot 2^{96}
$$

这就是 `tick_to_sqrtP(tick)` 的数学含义：

```python
def tick_to_sqrtP(tick):
    return (decimal.Decimal("1.0001")**(decimal.Decimal(tick)/2)) * Q96
```

即：

* `Tick` 是离散索引；
* `sqrtPriceX96` 是连续价格状态；
* 当前价格总会落在某个 Tick 区间内；
* 当价格跨过边界 Tick 时，活跃流动性可能发生变化。

---

### 2.3 Liquidity（流动性）

在 Uniswap V3 中，真正控制价格推进速度的不是简单的储备量，而是**当前价格区间内的有效流动性**，记作 $L$。

* $L$ 越大，池子越“深”，同样的交易量对价格的影响越小；
* $L$ 越小，池子越“浅”，价格更容易被推动。

> 在一个固定 Tick 区间内，$L$ 被视为常数；
> 只有当价格穿越某个 Tick 边界时，$L$ 才会变化。

这就是为什么 V3 的价格计算必须“分段”。

---

### 2.4 `liquidityNet` 与 Tick 边界

每个 LP 在 V3 中不是把流动性加到全局，而是加到一个区间：

$$
(\text{tickLower}, \text{tickUpper})
$$

这意味着：

* 当价格进入这个区间时，这部分流动性应当被激活；
* 当价格离开这个区间时，这部分流动性应当失效。

因此，V3 只需要在边界记录“净变化量”：

* 在 `tickLower` 处，流动性净增加；
* 在 `tickUpper` 处，流动性净减少。

---

## 3. 价格按 Tick 分段推进

### 1.如果当前价格在某个区间内，活跃流动性为 $L$。此时发生一笔 Swap，那么只要价格还没碰到下一个 Tick 边界，就可以把这段过程看作：

* **流动性不变**
* **价格连续变化**

这时就能用单一区间的公式计算价格变化。

### 2.如果交易量很大，价格会一路推进，直到撞到下一个边界。
一旦跨越边界：

* 某些 LP 的流动性开始生效；
* 或某些 LP 的流动性失效；
* 当前有效流动性 $L$ 立刻改变。

因此整笔交易会被拆成：

1. 当前区间内推进一段；
2. 跨过边界，更新 $L$；
3. 在新区间继续推进；
4. 重复直到交易量耗尽。

---

## 4. 整体算法思路

> **用上一笔 Swap 的结束状态作为起点，恢复两笔 Swap 之间流动性的全部变化，再模拟下一笔 Swap 如何把价格推进到最终位置。**

1. **提取起始状态**：读取上一笔 Swap 结束后的 `sqrtPriceX96 / liquidity / tick`
2. **恢复边界流动性变化**：

   * 从 RPC 扫描目标区间内已经初始化的 Tick；
   * 叠加中间发生的 Mint / Burn；
   * 如果这些 Mint / Burn 覆盖当前价格，则立刻修正当前活跃流动性
3. **分段模拟下一笔 Swap**：

   * 判断方向；
   * 逐个边界推进；
   * 每跨一个 Tick 就更新活跃流动性；
   * 最后在末尾区间内计算精确的终点价格

---

## 5. Step 1：确定起始状态

### 5.1 从上一笔 Swap 的结束状态开始

**Swap 1 结束之后链上的真实状态**作为起点。

第一个Swap数据被视为 `Swap_prior`，并从 `Data` 字段中解码出：

* `curr_sp`：当前平方根价格 `sqrtPriceX96`
* `curr_l`：当前有效流动性 `Liquidity`
* `curr_tick`：当前 Tick
* `start_block`：起始区块号，用于后续 RPC 查询

```python
s1_row = df.iloc[0]
s1_data = s1_row['Data'].replace('0x', '')
curr_sp = decimal.Decimal(int(s1_data[128:192], 16))
curr_l = decimal.Decimal(int(s1_data[192:256], 16))
curr_tick = decode_int256(s1_data[256:320])
start_block = int(s1_row['BlockNumber'])
```

### 5.2 第一笔Swap三个关键量

* `curr_sp`：当前价格
* `curr_tick`：当前价格处于的离散区间
* `curr_l`：当前区间流动性深度

它们共同定义了**价格推进的起始状态**。

---

## 6. Step 2：恢复流动性结构

在 V3 中，**流动性边界的变化同样会改变最终价格**。
两笔 Swap 之间即使没有新的交易，只要出现了 Mint / Burn，下一笔交易的价格结果也可能不同。

1. **RPC 扫描存量 Tick**
2. **CSV 叠加增量 Mint / Burn**

---

### 6.1 第一层：从 RPC 扫描存量 Tick 边界

先读取下一笔 Swap 的目标 Tick（用于决定扫描范围）：

```python
s2_row = df.iloc[-1]
s2_data = s2_row['Data'].replace('0x', '')
target_tick = decode_int256(s2_data[256:320])
rpc_changes = fetch_tick_changes_from_rpc(curr_tick, target_tick, start_block)
tick_net_changes.update(rpc_changes)
```

这里的 `fetch_tick_changes_from_rpc` 会：

1. 取当前 Tick 和目标 Tick 的上下界；
2. 按 `tick_spacing` 枚举这个区间附近的 Tick；
3. 调用池子合约的 `ticks(tick)`；
4. 如果该 Tick 已初始化且 `liquidityNet != 0`，就记录下来。

这样拿到的其实是：

> **在起始区块时，这段价格路径上已经存在的边界流动性结构。**

***这一步非常关键，因为仅凭 CSV 中间的 Mint/Burn，只能知道“增量变化”，却不知道路径上原本就有哪些已初始化 Tick。***

---

### 6.2 第二层：叠加中间发生的 Mint / Burn

在两次 Swap 之间，用户可能新增或移除流动性。
这些事件不会立刻改变所有价格点的状态，但会改变对应边界上的 `liquidityNet`。

#### Mint 的处理规则

若某笔 Mint 的流动性数量为 $L_m$，价格区间为：

$$
(t_{low}, t_{up})
$$

则边界净变化应记录为：

* `tickLower`：$+L_m$
* `tickUpper`：$-L_m$

因为当价格向上进入这个区间时，这部分流动性要生效；离开上边界时，它要失效。

脚本中对应代码：

```python
tick_net_changes[t_low] = tick_net_changes.get(t_low, 0) + amt
tick_net_changes[t_up] = tick_net_changes.get(t_up, 0) - amt
```

#### Burn 的处理规则

若某笔 Burn 移除了流动性数量 $L_b$，则恰好反过来：

* `tickLower`：$-L_b$
* `tickUpper`：$+L_b$

因为原本会在该区间内生效的流动性被撤走了。

脚本中对应逻辑：

```python
tick_net_changes[t_low] = tick_net_changes.get(t_low, 0) - amt
tick_net_changes[t_up] = tick_net_changes.get(t_up, 0) + amt
```

---

### 6.3 “即时修正”当前活跃流动性

> 如果中间某个 Mint / Burn 的价格区间覆盖当前 `curr_tick`，就立即修正 `curr_l`。

判断条件是：

```python
if t_low <= curr_tick < t_up:
```

这是因为当前价格已经位于这个区间内部。
对于这种区间：

* Mint 表示这部分流动性从现在起就是活跃的；
* Burn 表示这部分流动性从现在起就不再活跃。

因此必须立刻更新 `curr_l`，否则后续第一段价格推进会基于错误的流动性深度。

也就是说，这一步修正的是：

> **“当前已经生效的流动性”**

而 `tick_net_changes` 记录的是：

> **“未来跨过边界时需要发生的流动性变化”**

这两者缺一不可。

---

## 7. Step 3：模拟下一笔 Swap 的价格推进

恢复完状态之后，就开始真正模拟下一笔 Swap。

---

### 7.1 从 Swap 事件中提取交易量和目标结果

脚本从最后一行（即 `Swap_later`）中提取：

* `amt0_in`：`amount0`
* `actual_sp2`：链上实际结束时的 `sqrtPriceX96`
* `actual_l2`：链上实际结束时的有效流动性

```python
amt0_in = decode_int256(s2_data[0:64])
actual_sp2 = decimal.Decimal(int(s2_data[128:192], 16))
actual_l2 = decimal.Decimal(int(s2_data[192:256], 16))
```

其中 `actual_sp2` 和 `actual_l2` 不是拿来参与计算的，而是用于最后做校验。

---

### 7.2 先扣手续费，再决定价格方向

脚本使用的示例池子是 0.05% 费率，因此先将参与价格推进的有效输入量改为：

$$\text{rem\_amt0} = \text{amount0} \times (1 - 0.0005)$$

代码：

```python
rem_amt0 = decimal.Decimal(amt0_in) * decimal.Decimal("0.9995")
```

因为真正推动价格的不是用户输入的原始数量，而是**扣完手续费后进入池子定价公式的有效数量**。

然后脚本根据 `amount0` 的正负判断价格方向：

```python
is_down = amt0_in > 0
```

在这份脚本的约定里：

* `amount0 > 0`：价格向下走
* `amount0 < 0`：价格向上走

本质上这是根据 token0 的流入/流出方向，推断价格曲线的移动方向。

---

### 7.3 先找出路径上可能会碰到的所有 Tick 边界

把所有边界取出并按方向排序：

```python
all_ticks = sorted(tick_net_changes.keys(), reverse=is_down)
relevant_edges = [t for t in all_ticks if (t < curr_tick if is_down else t > curr_tick)]
```

含义是：

* 如果价格向下走，只关心当前 Tick 下方的边界；
* 如果价格向上走，只关心当前 Tick 上方的边界。

这一步构建出来的 `relevant_edges`，就是价格推进过程中可能经过的“关卡”。

---

## 8. 区间内的数学计算

当流动性 $L$ 固定时，Uniswap V3 在当前区间内的价格变化遵循局部常量流动性模型。

---

### 8.1 到达边界所需的 Token0 数量

设当前平方根价格为 $\sqrt{P_a}$，边界平方根价格为 $\sqrt{P_b}$，区间内活跃流动性为 $L$。
那么从当前价格走到边界价格所需的 Token0 数量为：

$$
\Delta x = \frac{L \cdot |\sqrt{P_b} - \sqrt{P_a}|}{\sqrt{P_b}\sqrt{P_a}}
$$

由于使用的是 `sqrtPriceX96` 的整数形式，所以写成：

$$
\Delta x =
\frac{L \cdot |\text{sp}_b - \text{sp}_a| \cdot Q96}
{\text{sp}_b \cdot \text{sp}_a}
$$

对应代码：

```python
dx_to_edge = (curr_l * abs(curr_sp - sp_edge) * Q96) / (curr_sp * sp_edge)
```

这个量的物理意义是：

> **如果当前区间流动性固定，要把价格刚好推到下一个 Tick 边界，需要消耗多少 Token0。**

---

### 8.2 如果剩余输入量足够跨越边界

若：

$$
|\text{rem\_amt0}| \ge \Delta x
$$

说明本段交易量足够把价格推到边界，甚至继续往前走。
于是执行：

1. 扣除这段消耗量；
2. 将当前价格设置为边界价格；
3. 根据边界净变化量更新流动性；
4. 继续处理下一个边界。

代码：

```python
if abs(rem_amt0) >= dx_to_edge:
    rem_amt0 -= dx_to_edge * (1 if is_down else -1)
    curr_sp = sp_edge
```

这一步等价于：

> **价格先完整走完当前区间，再进入下一个区间。**

---

### 8.3 跨 Tick 后更新流动性

跨边界以后，要根据 `tick_net_changes[edge]` 更新当前活跃流动性。

```python
change = decimal.Decimal(tick_net_changes[edge])
if not is_down:
    curr_l += change
else:
    curr_l -= change
```

这是因为同一个 `liquidityNet`，在“价格向上穿越边界”和“价格向下穿越边界”时，作用方向是相反的。

#### 当价格向上走时

* 穿越 `tickLower`：一个区间开始生效，流动性增加
* 穿越 `tickUpper`：一个区间失效，流动性减少

因此可写成：

$$
L_{\text{new}} = L_{\text{old}} + \text{liquidityNet}
$$

#### 当价格向下走时

因为是逆方向穿越边界，所以效果相反：

$$
L_{\text{new}} = L_{\text{old}} - \text{liquidityNet}
$$

这也是你脚本里“上涨加 net、下跌减 net”的原因。

---

## 9. 尾部计算：当交易量不足以跨出当前tick区间

有些时候，交易量在某个区间内就耗尽了，价格并不会碰到下一个 Tick。
这时就不能再继续跨边界，而是要在当前 $L$ 不变的区间内，直接算出最终价格。

脚本中这一步是：

```python
if abs(rem_amt0) > 0:
    num = curr_l * curr_sp * Q96
    den = (curr_l * Q96) + (rem_amt0 * curr_sp)
    curr_sp = num / den
```

对应公式为：

$$
\sqrt{P}_{next}
==

\frac{L \cdot \sqrt{P} \cdot 2^{96}}
{L \cdot 2^{96} + \Delta x \cdot \sqrt{P}}
$$

这里的含义是：

* 当前流动性 $L$ 已经固定；
* 剩余的 Token0 输入量不足以触碰下一个边界；
* 所以价格只会在当前区间内部平滑推进到一个终点。

这一步给出的，就是最终的精确 `sqrtPriceX96`。


---

## 10. 数据提取与时间线一致性

`get_data_from_csv` 函数实际上解决了另一个问题：**事件顺序必须与链上执行顺序完全一致。**

函数的处理流程是：

1. 读取原始日志；
2. 按 `BlockNumber` 和 `Serial` 排序；
3. 自动定位第 `N` 笔和第 `M` 笔 Swap；
4. 截取它们之间的所有日志；
5. 将这段日志送入模拟器。

这样做：

* 确保两笔 Swap 之间的 Mint/Burn 不会漏掉；
* 确保事件处理顺序与真实链上执行一致；
* 避免因为日志乱序而造成流动性恢复错误。

---

## 11. 验证指标说明

脚本最后使用了三类指标来检验模拟结果是否正确。

### 11.1 L 匹配度

比较模拟结束后的 `curr_l` 与链上实际 `actual_l2`：

```python
print(f"计算 L: {int(curr_l)} | 实际 L: {int(actual_l2)}")
```

如果一致，说明 Tick 穿越逻辑和流动性更新逻辑完全正确。

---

### 11.2 `sqrtPriceX96` 误差

比较模拟结果与链上实际结果的整数差值：

```python
print(f"误差: {abs(int(curr_sp) - int(actual_sp2))} (X96)")
```

这是最直接的高精度对比方式。
因为 `sqrtPriceX96` 本身就是链上真实存储格式，所以它比“显示价格”更适合作为算法精度标准。

---

### 11.3 价格误差

将 `sqrtPriceX96` 转成 `USDC/ETH` 后再比较：

```python
print(f"价格误差: {abs(to_human_price(curr_sp) - to_human_price(actual_sp2)):.10f} USDC/ETH")
```

---

# 整套算法的完整流程总结

将整个算法逻辑用更抽象的话概括：

### Step 1：读取上一笔 Swap 的结束状态

得到：

* 当前价格 `curr_sp`
* 当前 Tick `curr_tick`
* 当前有效流动性 `curr_l`

### Step 2：恢复从起点到目标路径上的流动性结构

包括两部分：

1. **链上存量边界**

   * 通过 RPC 查询初始化 Tick
   * 得到已有的 `liquidityNet`

2. **中间增量变化**

   * 处理 Mint/Burn
   * 更新 `tick_net_changes`
   * 若覆盖当前价格，则立即修正 `curr_l`

### Step 3：按 Tick 边界推进下一笔 Swap

* 扣除手续费
* 判断价格方向
* 依次计算到每个边界所需的 `dx_to_edge`
* 如果够跨越：

  * 价格推进到边界
  * 更新 `curr_l`
  * 继续下一段
* 如果不够跨越：

  * 在当前区间内做尾部精确计算
  * 得到最终价格

### Step 4：与链上结果对比验证

将模拟值与 Swap 事件中实际记录的：

* `actual_l2`
* `actual_sp2`

进行比较，看是否匹配。

---

## 附：与你脚本对应的简化伪代码

```python
# 1. 读取上一笔 Swap 的结束状态
curr_sp   = swap1.sqrtPriceX96
curr_l    = swap1.liquidity
curr_tick = swap1.tick

# 2. 恢复路径上的流动性结构
tick_net_changes = rpc_scan_initialized_ticks(curr_tick, target_tick)
tick_net_changes += mid_mint_burn_changes()

# 3. 若中间 Mint/Burn 覆盖当前价格，则即时修正 curr_l
for event in mid_events:
    if event.range_contains(curr_tick):
        curr_l += event.delta_liquidity_on_active_range

# 4. 读取下一笔 Swap 的 amount0
rem_amt0 = amount0_after_fee
direction = infer_direction(rem_amt0)

# 5. 按 Tick 边界逐段推进
for edge in relevant_edges:
    dx_to_edge = calc_dx_to_edge(curr_sp, edge_sp, curr_l)
    if rem_amt0_enough_to_cross:
        rem_amt0 -= dx_to_edge
        curr_sp = edge_sp
        curr_l = update_liquidity_after_cross(edge, curr_l, direction)
    else:
        break

# 6. 在最后一个区间内计算精确终值
curr_sp = calc_final_sqrt_price_in_range(curr_sp, curr_l, rem_amt0)

# 7. 与链上实际结果比较
compare(curr_sp, actual_sp2)
compare(curr_l, actual_l2)
```

---
