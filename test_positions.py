"""测试脚本：查询实仓持仓"""

from moomoo import (
    OpenSecTradeContext,
    TrdEnv,
    TrdMarket,
    SecurityFirm,
    RET_OK,
)

host = "127.0.0.1"
port = 11111

trd_ctx = OpenSecTradeContext(
    security_firm=SecurityFirm.FUTUINC,
    filter_trdmarket=TrdMarket.US,
    host=host,
    port=port,
)

# 解锁交易
ret, data = trd_ctx.unlock_trade("xxxxxx")  # 替换为你的交易密码
if ret != RET_OK:
    print("解锁失败:", data)
    trd_ctx.close()
    exit(1)
print("交易已解锁")

# 查询实仓持仓
ret, data = trd_ctx.position_list_query(trd_env=TrdEnv.REAL)
if ret == RET_OK:
    print("\n--- 实仓持仓 ---")
    if data.empty:
        print("无持仓")
    else:
        cols = ["code", "stock_name", "qty", "can_sell_qty", "cost_price",
                "market_val", "pl_ratio", "position_side"]
        existing = [c for c in cols if c in data.columns]
        print(data[existing].to_string(index=False))
        print("\n--- 持仓汇总 (code: qty) ---")
        for _, row in data.iterrows():
            if row["qty"] > 0:
                print(f"  {row['code']}: {int(row['qty'])} 股")
else:
    print("查询持仓失败:", data)

trd_ctx.close()
