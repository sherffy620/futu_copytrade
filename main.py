"""
富途模拟组合跟单主程序

用法:
    python main.py          # 启动跟单
    python main.py --once   # 只检查一次（测试用）
    python main.py --dry    # 干跑模式，只检测变化不下单
"""

import json
import sys
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import config
from moomoo import TrdSide
from monitor import fetch_portfolio, load_snapshot, save_snapshot, diff_positions, has_changes
from trader import Trader, _is_regular_hours, _is_tradable_hours, _is_premarket_hours
from notify import notify_changes, notify_error, notify_overnight_change

ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# 夜盘已通知的持仓快照（内存中），用于去重避免重复发邮件
# 进入可交易时段后清空
_overnight_notified = None

# 夜盘账户持仓快照文件，用于盘前对账
OVERNIGHT_ACCT_FILE = Path(__file__).parent / "overnight_account_snapshot.json"

# 待买入列表文件（夜盘启动时用户选择买入的股票）
PENDING_BUYS_FILE = Path(__file__).parent / "pending_buys.json"


def _save_overnight_account(positions: dict):
    """保存夜盘时的账户实际持仓快照"""
    with open(OVERNIGHT_ACCT_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)
    logger.info("夜盘账户持仓快照已保存: %s", positions if positions else "空")


def _load_overnight_account() -> dict | None:
    """加载夜盘账户持仓快照，不存在返回 None"""
    if OVERNIGHT_ACCT_FILE.exists():
        with open(OVERNIGHT_ACCT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def _clear_overnight_account():
    """清除夜盘账户持仓快照"""
    if OVERNIGHT_ACCT_FILE.exists():
        OVERNIGHT_ACCT_FILE.unlink()
        logger.info("夜盘账户持仓快照已清除")


def _save_pending_buys(buys: list):
    """保存待买入列表 [{code, weight}, ...]"""
    with open(PENDING_BUYS_FILE, "w", encoding="utf-8") as f:
        json.dump(buys, f, ensure_ascii=False, indent=2)


def _load_pending_buys() -> list:
    """加载待买入列表"""
    if PENDING_BUYS_FILE.exists():
        with open(PENDING_BUYS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def _clear_pending_buys():
    """清除待买入列表"""
    if PENDING_BUYS_FILE.exists():
        PENDING_BUYS_FILE.unlink()


def _snapshot_account_positions():
    """连接 OpenD 查询账户实际持仓，返回 {code: qty}"""
    trader = Trader()
    try:
        trader.connect()
        positions = trader.get_my_positions()
        return positions
    except Exception as e:
        logger.error("查询账户持仓失败: %s", e)
        return {}
    finally:
        trader.close()

def _init_copytrade_positions():
    """每次启动时重新初始化跟单持仓记录。

    1. 删除旧的 snapshot.json 和 copytrade_positions.json
    2. 获取当前组合持仓
    3. 查询账户实际持仓
    4. 对于组合中每只股票:
       - 账户有持仓 → 询问跟单数量
       - 账户无持仓 → 询问是否买入
    5. 保存新的 snapshot.json
    """
    from trader import COPYTRADE_POS_FILE, _save_copytrade_positions
    from monitor import SNAPSHOT_FILE

    print("\n" + "=" * 50)
    print("启动初始化")
    print("=" * 50)

    # 删除旧文件
    if COPYTRADE_POS_FILE.exists():
        COPYTRADE_POS_FILE.unlink()
        print("已删除旧的 copytrade_positions.json")
    if SNAPSHOT_FILE.exists():
        SNAPSHOT_FILE.unlink()
        print("已删除旧的 snapshot.json")

    # 获取当前组合持仓
    portfolio = fetch_portfolio(config.PORTFOLIO_ID, config.FUTU_COOKIE)
    if not portfolio:
        print("无法获取组合持仓，跳过初始化")
        print("=" * 50 + "\n")
        return

    print(f"\n当前组合持仓 ({len(portfolio)} 只):")
    for p in portfolio:
        print(f"  {p['code']} {p['name']} 权重={p['weight']:.1%}")

    # 查询账户实际持仓
    acct_positions = _snapshot_account_positions()
    print(f"\n当前账户持仓 ({len(acct_positions)} 只):")
    for code, qty in sorted(acct_positions.items()):
        print(f"  {code}: {qty} 股")

    ct_pos = {}
    pending_buys = []
    tradable = _is_tradable_hours()

    print("\n" + "-" * 50)
    print("请为组合中的每只股票设置跟单状态:")
    print("-" * 50)

    for p in portfolio:
        code = p["code"]
        name = p["name"]
        weight = p["weight"]
        acct_qty = acct_positions.get(code, 0)

        if acct_qty > 0:
            # 账户有持仓，询问跟单数量
            while True:
                answer = input(f"\n  {code} {name} (持有 {acct_qty} 股)\n  跟单数量 (回车=0, all=全部): ").strip()
                if answer == "":
                    ct_qty = 0
                    break
                elif answer.lower() == "all":
                    ct_qty = acct_qty
                    break
                else:
                    try:
                        ct_qty = int(answer)
                        if 0 <= ct_qty <= acct_qty:
                            break
                        else:
                            print(f"    请输入 0 到 {acct_qty} 之间的整数")
                    except ValueError:
                        print("    请输入整数、all 或直接回车")
            if ct_qty > 0:
                ct_pos[code] = ct_qty
        else:
            # 账户无持仓，询问是否买入
            while True:
                answer = input(f"\n  {code} {name} (未持有, 权重={weight:.1%})\n  是否买入? (y/n): ").strip().lower()
                if answer in ("y", "yes", "是"):
                    if tradable:
                        # 可交易时段，立即买入
                        print(f"    将立即买入 {code}")
                        pending_buys.append({"code": code, "weight": weight, "immediate": True})
                    else:
                        # 夜盘，记录待买入
                        print(f"    夜盘不可交易，将在盘前自动买入 {code}")
                        pending_buys.append({"code": code, "weight": weight, "immediate": False})
                    break
                elif answer in ("n", "no", "否", ""):
                    print(f"    跳过 {code}")
                    break
                else:
                    print("    请输入 y 或 n")

    # 保存跟单持仓记录
    _save_copytrade_positions(ct_pos)
    print(f"\n跟单持仓记录已保存: {ct_pos if ct_pos else '空'}")

    # 保存当前组合为 snapshot
    save_snapshot(portfolio)
    print("组合快照已保存")

    # 处理买入
    if pending_buys:
        immediate_buys = [b for b in pending_buys if b.get("immediate")]
        deferred_buys = [b for b in pending_buys if not b.get("immediate")]

        if immediate_buys:
            print(f"\n执行立即买入 ({len(immediate_buys)} 只)...")
            trader = Trader()
            try:
                trader.connect()
                for b in immediate_buys:
                    code = b["code"]
                    weight = b["weight"]
                    target_qty = trader.calc_target_qty(code, weight)
                    if target_qty > 0:
                        order_id = trader.place_order(code, target_qty, TrdSide.BUY, _is_regular_hours())
                        if order_id:
                            ct_pos[code] = target_qty
                            print(f"    买入 {code} x {target_qty} 成功")
                        else:
                            print(f"    买入 {code} 失败")
                    else:
                        print(f"    {code} 计算目标数量为0，跳过")
                _save_copytrade_positions(ct_pos)
            finally:
                trader.close()

        if deferred_buys:
            # 保存待买入列表
            _save_pending_buys([{"code": b["code"], "weight": b["weight"]} for b in deferred_buys])
            print(f"\n待买入列表已保存 ({len(deferred_buys)} 只)，将在盘前自动执行")

    print("\n" + "=" * 50)
    print("初始化完成")
    print("=" * 50 + "\n")
    logger.info("跟单持仓初始化完成: %s", ct_pos if ct_pos else "空")



def is_weekend_closed() -> bool:
    now_et = datetime.now(ET)
    wd = now_et.weekday()
    hour = now_et.hour
    if wd == 5:
        return True
    if wd == 4 and hour >= 20:
        return True
    if wd == 6 and hour < 16:
        return True
    return False


def run_once(dry_run=False):
    global _overnight_notified

    logger.info("开始检查组合持仓...")

    new_positions = fetch_portfolio(config.PORTFOLIO_ID, config.FUTU_COOKIE)
    if new_positions is None:
        logger.warning("未获取到持仓数据，跳过")
        return False

    old_positions = load_snapshot()
    diff = diff_positions(old_positions, new_positions)

    if not has_changes(diff):
        logger.info("持仓无变化")
        return False

    logger.info("检测到持仓变化:")
    for p in diff["added"]:
        tag = " [待成交]" if p.get("is_pending") else ""
        logger.info("  [新增] %s %s 权重=%.1f%%%s", p["code"], p["name"], p.get("weight", 0) * 100, tag)
    for p in diff["removed"]:
        logger.info("  [清仓] %s %s", p["code"], p["name"])
    for p in diff["changed"]:
        tag = " [待成交]" if p.get("is_pending") else ""
        logger.info("  [调仓] %s %s %.1f%% -> %.1f%%%s", p["code"], p["name"], p["old_weight"] * 100, p["new_weight"] * 100, tag)

    tradable = _is_tradable_hours()
    regular = _is_regular_hours()

    if not tradable:
        # 夜盘时段（20:00-4:00 ET）：只发邮件，不更新磁盘快照
        # 用 _overnight_notified 去重，避免同一变化反复发邮件
        should_notify = False
        if _overnight_notified is None:
            should_notify = True
            logger.info("夜盘首次检测到变化，发送邮件通知")
            notify_overnight_change(diff)
            _overnight_notified = new_positions
        else:
            overnight_diff = diff_positions(_overnight_notified, new_positions)
            if has_changes(overnight_diff):
                should_notify = True
                logger.info("夜盘检测到新变化，发送邮件通知")
                notify_overnight_change(overnight_diff)
                _overnight_notified = new_positions
            else:
                logger.info("夜盘无新变化，跳过邮件")

        # 首次检测到夜盘变化时，连 OpenD 查账户持仓并保存快照
        # 用于盘前对账，判断用户是否手动操作过
        if should_notify and not OVERNIGHT_ACCT_FILE.exists():
            logger.info("记录夜盘账户持仓快照...")
            acct_pos = _snapshot_account_positions()
            _save_overnight_account(acct_pos)

        return True

    # 进入可交易时段，清空夜盘记录
    _overnight_notified = None

    # 处理待买入列表（夜盘启动时用户选择买入的股票）
    pending_buys = _load_pending_buys()
    if pending_buys:
        logger.info("处理待买入列表 (%d 只)...", len(pending_buys))
        trader = Trader()
        try:
            trader.connect()
            # 查询当前持仓，避免重复买入
            current_positions = trader.get_my_positions()
            for b in pending_buys:
                code = b["code"]
                weight = b["weight"]
                target_qty = trader.calc_target_qty(code, weight)
                current_qty = current_positions.get(code, 0)
                buy_qty = max(0, target_qty - current_qty)
                if buy_qty > 0:
                    order_id = trader.place_order(code, buy_qty, TrdSide.BUY, regular)
                    if order_id:
                        trader._update_ct_pos(code, buy_qty)
                        logger.info("待买入执行成功: %s x %d (目标%d, 已有%d)", code, buy_qty, target_qty, current_qty)
                elif current_qty > 0:
                    # 用户已手动买入，只更新跟单记录
                    trader._update_ct_pos(code, current_qty)
                    logger.info("待买入 %s 用户已手动买入 %d 股, 更新跟单记录", code, current_qty)
                else:
                    logger.info("待买入 %s 目标数量为0, 跳过", code)
        finally:
            trader.close()
        _clear_pending_buys()

    # 可交易时段：发送变化通知 + 执行交易
    notify_changes(diff)

    if dry_run:
        order_type = "市价单" if regular else "限价单"
        logger.info("干跑模式，不执行交易 (%s)", order_type)
    else:
        # 加载夜盘账户快照用于对账
        overnight_acct = _load_overnight_account()

        trader = Trader()
        try:
            trader.connect()
            # 盘前对账：比较夜盘快照和当前持仓，补录手动操作
            if overnight_acct is not None:
                portfolio_codes = {p["code"] for p in new_positions}
                trader.reconcile_overnight(overnight_acct, portfolio_codes)
            # 计算盘前是否跳过卖出
            skip_sells = False
            if _is_premarket_hours():
                mode = getattr(config, "PREMARKET_SELL_MODE", "never")
                if mode == "never":
                    skip_sells = True
                elif mode == "same_count":
                    # 只有5→5换仓时跳过卖出，其他情况正常卖出
                    old_count = len(old_positions)
                    new_count = len(new_positions)
                    if old_count == 5 and new_count == 5:
                        skip_sells = True
                        logger.info("盘前换仓模式: 5只→5只, 跳过卖出")
                # mode == "always" 时 skip_sells 保持 False
            
            trader.execute_diff(diff, use_market_order=regular, skip_sells=skip_sells)
        finally:
            trader.close()

        # 对账完成，清除夜盘快照
        _clear_overnight_account()

    save_snapshot(new_positions)
    logger.info("快照已更新")
    return True


def main():
    dry_run = "--dry" in sys.argv
    once = "--once" in sys.argv

    if dry_run:
        logger.info("=== 干跑模式 ===")

    if once:
        _init_copytrade_positions()
        run_once(dry_run)
        return

    logger.info("=== 跟单程序启动 ===")
    logger.info("组合ID: %s", config.PORTFOLIO_ID)
    logger.info("轮询间隔: %ss", config.POLL_INTERVAL)
    logger.info("交易环境: %s", config.TRADE_ENV)
    logger.info("跟单资金: $%s", f"{config.TOTAL_CAPITAL:,.0f}")

    _init_copytrade_positions()

    while True:
        try:
            if is_weekend_closed():
                logger.debug("周末休市，跳过")
            else:
                run_once(dry_run)
        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            break
        except Exception as e:
            logger.error("运行出错: %s", e, exc_info=True)
            notify_error(str(e))

        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    main()
