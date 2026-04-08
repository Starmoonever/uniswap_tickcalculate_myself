import pandas as pd
import decimal
import os
from io import StringIO
from web3 import Web3
from dotenv import load_dotenv


# 1. 基础高精度配置
decimal.getcontext().prec = 100
Q96 = decimal.Decimal(2**96)

# --- RPC 连接 ---
load_dotenv()
RPC_URL = os.getenv("RPC_URL")
if not RPC_URL:
    raise RuntimeError("缺少 RPC_URL。请在本地 .env 或环境变量中配置以太坊 RPC 地址。")

# print(f"[*] 使用 RPC URL: {RPC_URL}")
POOL_ADDRESS = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640" #示例池
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# ABI
POOL_ABI = [
    {
        "inputs": [{"name": "tick", "type": "int24"}],
        "name": "ticks",
        "outputs": [
            {"name": "liquidityGross", "type": "uint128"},
            {"name": "liquidityNet", "type": "int128"},
            {"name": "feeGrowthOutside0X128", "type": "uint256"},
            {"name": "feeGrowthOutside1X128", "type": "uint256"},
            {"name": "tickCumulativeOutside", "type": "int56"},
            {"name": "secondsPerLiquidityOutsideX128", "type": "uint160"},
            {"name": "secondsOutside", "type": "uint32"},
            {"name": "initialized", "type": "bool"}
        ],
        "stateMutability": "view", "type": "function"
    }
]
pool_contract = w3.eth.contract(address=w3.to_checksum_address(POOL_ADDRESS), abi=POOL_ABI)

# 事件哈希签名
TOPIC_SWAP  = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TOPIC_MINT  = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
TOPIC_BURN  = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"

def decode_int256(hex_str):
    if not hex_str or pd.isna(hex_str): return 0
    hex_str = str(hex_str).replace('0x', '').strip()
    if not hex_str: return 0
    val = int(hex_str, 16)
    if val >= 2**255: val -= 2**256
    return val

def tick_to_sqrtP(tick):
    return (decimal.Decimal("1.0001")**(decimal.Decimal(tick)/2)) * Q96

def to_human_price(sp):
    return (1 / ((sp / Q96)**2)) * (10**12)

def fetch_tick_changes_from_rpc(start_tick, end_tick, block_number, tick_spacing=10):
    net_changes = {}
    low, high = min(start_tick, end_tick), max(start_tick, end_tick)
    # 向外扩展 1 个 spacing 确保安全
    s = ((low // tick_spacing) - 1) * tick_spacing
    e = ((high // tick_spacing) + 1) * tick_spacing
    
    print(f"[*] 正在从 RPC 扫描存量 Tick 边界: {s} -> {e} (Block {block_number})")
    for t in range(s, e + tick_spacing, tick_spacing):
        try:
            res = pool_contract.functions.ticks(t).call(block_identifier=block_number)
            liquidity_net = res[1]
            initialized = res[7]
            if initialized and liquidity_net != 0:
                net_changes[t] = decimal.Decimal(liquidity_net)
                print(f"    - 发现链上存量 Tick {t}: Net={liquidity_net}")
        except Exception:
            continue
    return net_changes

def run_automated_test(csv_text):
    columns = ["Hash","BlockNumber","Address","TopicHash","Topics","Data","CreatedAt","Serial","BlockHash"]
    df = pd.read_csv(StringIO(csv_text), names=columns, quotechar='"')
    df = df.sort_values(by=['BlockNumber', 'Serial']).reset_index(drop=True)

    # --- Step 1: 初始状态 ---
    s1_row = df.iloc[0]
    s1_data = s1_row['Data'].replace('0x', '')
    curr_sp = decimal.Decimal(int(s1_data[128:192], 16))
    curr_l = decimal.Decimal(int(s1_data[192:256], 16))
    curr_tick = decode_int256(s1_data[256:320])
    start_block = int(s1_row['BlockNumber'])
    print(f"\n>>> [初始状态] Swap_prior 结束: Tick={curr_tick}, L={int(curr_l)}")

    # --- Step 2: 动态调整流动性 (存量 RPC + 增量 CSV) ---
    tick_net_changes = {}
    
    # 1. RPC 获取存量
    s2_row = df.iloc[-1]
    s2_data = s2_row['Data'].replace('0x', '')
    target_tick = decode_int256(s2_data[256:320])
    rpc_changes = fetch_tick_changes_from_rpc(curr_tick, target_tick, start_block)
    tick_net_changes.update(rpc_changes)

    # 2. 处理 CSV 中间发生的 Mint/Burn (叠加到 tick_net_changes 并实时修正初始 curr_l)
    mid_df = df.iloc[1:-1]
    print(f"--- [流水日志: 预处理中间 Mint/Burn 事件] ---")
    for idx, row in mid_df.iterrows():
        th = str(row['TopicHash']).lower().strip()
        if th not in [TOPIC_MINT.lower(), TOPIC_BURN.lower()]: continue
        
        topics = str(row['Topics']).split(',')
        t_low, t_up = decode_int256(topics[1]), decode_int256(topics[2])
        data = str(row['Data']).replace('0x', '')

        if th == TOPIC_MINT.lower():
            amt = int(data[64:128], 16)
            tick_net_changes[t_low] = tick_net_changes.get(t_low, 0) + amt
            tick_net_changes[t_up] = tick_net_changes.get(t_up, 0) - amt
            # 如果 Mint 区间覆盖当前价格，立刻修正初始 L
            if t_low <= curr_tick < t_up:
                curr_l += amt
                print(f"    行 {idx} [Mint]: 区间覆盖当前价格, L 增加 {amt}")
        
        elif th == TOPIC_BURN.lower():
            amt = int(data[0:64], 16)
            tick_net_changes[t_low] = tick_net_changes.get(t_low, 0) - amt
            tick_net_changes[t_up] = tick_net_changes.get(t_up, 0) + amt
            # 如果 Burn 区间覆盖当前价格，立刻修正初始 L
            if t_low <= curr_tick < t_up:
                curr_l -= amt
                print(f"    行 {idx} [Burn]: 区间覆盖当前价格, L 减少 {amt}")
    

    # --- Step 3: 分段计算 ---
    amt0_in = decode_int256(s2_data[0:64])
    actual_sp2 = decimal.Decimal(int(s2_data[128:192], 16))
    actual_l2 = decimal.Decimal(int(s2_data[192:256], 16))
    
    rem_amt0 = decimal.Decimal(amt0_in) * decimal.Decimal("0.9995") # 0.05% 费率
    is_down = amt0_in > 0 
    
    all_ticks = sorted(tick_net_changes.keys(), reverse=is_down)
    relevant_edges = [t for t in all_ticks if (t < curr_tick if is_down else t > curr_tick)]

    print(f"\n--- [分段模拟计算开始] 方向: {'下跌' if is_down else '上涨'} ---")
    for edge in relevant_edges:
        sp_edge = tick_to_sqrtP(edge)
        dx_to_edge = (curr_l * abs(curr_sp - sp_edge) * Q96) / (curr_sp * sp_edge)
        
        if abs(rem_amt0) >= dx_to_edge:
            rem_amt0 -= dx_to_edge * (1 if is_down else -1)
            curr_sp = sp_edge
            
            # 基于方向的流动性变换
            change = decimal.Decimal(tick_net_changes[edge])
            if not is_down: 
                curr_l += change # 上涨：L = L + net
            else: 
                curr_l -= change # 下跌：L = L - net
            
            print(f"    跨越 Tick {edge}: 消耗 dx={dx_to_edge:.2f}, L 更新为 {int(curr_l)}")
        else:
            break

    if abs(rem_amt0) > 0:
        num = curr_l * curr_sp * Q96
        den = (curr_l * Q96) + (rem_amt0 * curr_sp)
        curr_sp = num / den

    # --- 最终对比检验 ---
    print(f"\n--- 最终验证结果 ---")
    print(f"计算 L: {int(curr_l)} | 实际 L: {int(actual_l2)}")
    print(f"L 验证结果: {'✅ 完美匹配' if abs(int(curr_l) - int(actual_l2)) < 1000 else '❌ 存在偏差'}")

    print(f"计算 sqrtP: {int(curr_sp)}")
    print(f"实际 sqrtP: {int(actual_sp2)}")
    print(f"误差: {abs(int(curr_sp) - int(actual_sp2))} (X96)")
    print(f"计算 USDC/ETH 价格: {to_human_price(curr_sp):.10f}")
    print(f"实际 USDC/ETH 价格: {to_human_price(actual_sp2):.10f}")
    print(f"价格误差: {abs(to_human_price(curr_sp) - to_human_price(actual_sp2)):.10f} USDC/ETH")



def get_data_from_csv(file_path, start_n=1, end_n=2):
    columns = ["Hash","BlockNumber","Address","TopicHash","Topics","Data","CreatedAt","Serial","BlockHash"]
    full_df = pd.read_csv(file_path, names=columns, quotechar='"')
    full_df = full_df.sort_values(by=['BlockNumber', 'Serial']).reset_index(drop=True)
    swap_indices = full_df[full_df['TopicHash'].str.lower() == TOPIC_SWAP.lower()].index.tolist()
    if len(swap_indices) < end_n: return None
    return full_df.loc[swap_indices[start_n - 1] : swap_indices[end_n - 1]]

def main():
    csv_file = "./work_testdata.csv"
    target_df = get_data_from_csv(csv_file, start_n=5, end_n=6) # 选择第 i 到 i+1 次 Swap 事件进行测试   
    if target_df is not None:
        run_automated_test(target_df.to_csv(index=False, header=False))


if __name__ == "__main__":
    main()
