from moomoo import *

host = "127.0.0.1"
port = 11111

# ✅ moomoo US 常用：SecurityFirm.FUTUINC
# ✅ 先不加 filter_trdmarket，避免把实盘账户过滤没
trd_ctx = OpenSecTradeContext(
security_firm=SecurityFirm.FUTUINC,
filter_trdmarket=TrdMarket.US,
    host=host,
    port=port,

)

ret, data = trd_ctx.get_acc_list()

if ret == RET_OK:
    print("\n--- 成功获取账户列表（前几行）---")
    print(data.head(10))

    # 打印核心列（存在就打印）
    cols = ["acc_id", "trd_env", "trd_market", "stk_market", "sim_acc_type", "acc_type"]
    existing_cols = [c for c in cols if c in data.columns]

    print("\n--- 关键信息摘要 ---")
    if existing_cols:
        print(data[existing_cols])
    else:
        print("未找到预期列名，可用列名：")
        print(list(data.columns))

    # ✅ 判断是否拿到实盘
    if "trd_env" in data.columns:
        envs = set(data["trd_env"].astype(str).str.upper().tolist())
        print("\ntrd_env 集合:", envs)
        if "REAL" in envs:
            print("✅ 检测到 REAL（实盘）账户")
        else:
            print("⚠️ 只有 SIMULATE（模拟）账户 -> OpenD 当前会话没拿到实盘账户/权限")
else:
    print("获取账户失败:", data)

trd_ctx.close()