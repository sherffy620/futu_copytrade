"""
跟单模块：通过 moomoo-api 连接 OpenD 执行交易

限价单追单逻辑：下单后轮询检查，未成交则改价重挂，循环直到成交。
价格数据通过 yfinance 获取。
跟单持仓独立记录在 copytrade_positions.json，卖出时只卖跟单部分，不动底仓。
"""

import json
import time
import logging
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from moomoo import (
    OpenSecTradeContext,
    TrdEnv,
    TrdMarket,
    TrdSide,
    OrderType,
    OrderStatus,
    ModifyOrderOp,
    RET_OK,
    SecurityFirm,
)

import config
from notify import notify_order_filled, notify_order_timeout
from stoploss import place_stop_loss

logger = logging.getLogger(__name__)

TRD_ENV_MAP = {
    "SIMULATE": TrdEnv.SIMULATE,
    "REAL": TrdEnv.REAL,
}

TRD_MARKET_MAP = {
    "US": TrdMarket.US,
    "HK": TrdMarket.HK,
}

# 从 config 读取滑点参数
LIMIT_SLIPPAGE = config.LIMIT_SLIPPAGE
REPRICE_SLIPPAGE = config.REPRICE_SLIPPAGE
ORDER_CHECK_INTERVAL = config.ORDER_CHECK_INTERVAL
MAX_REPRICE_TIMES = config.MAX_REPRICE_TIMES

ET = ZoneInfo("America/New_York")

# 跟单持仓记录文件
COPYTRADE_POS_FILE = Path(__file__).parent / "copytrade_positions.json"


def _load_copytrade_positions() -> dict:
    """加载跟单持仓记录 {code: qty}"""
    if COPYTRADE_POS_FILE.exists():
        with open(COPYTRADE_POS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_copytrade_positions(positions: dict):
    """保存跟单持仓记录"""
    with open(COPYTRADE_POS_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def _is_regular_hours():
    """判断当前是否为美股正常交易时段 9:30-16:00 ET"""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def _is_tradable_hours():
    """判断当前是否在可交易时段（盘前+盘中+盘后 4:00-20:00 ET）
    
    夜盘 20:00-4:00 ET 不自动交易，只发邮件通知。
    """
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 4 * 60 <= minutes < 20 * 60

def _is_premarket_hours():
    """判断当前是否为盘前时段 4:00-9:30 ET

    盘前只买不卖，卖出信号只更新跟单记录不下单。
    """
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 4 * 60 <= minutes < 9 * 60 + 30



class Trader:
    def __init__(self):
        self.host = config.OPEND_HOST
        self.port = config.OPEND_PORT
        self.trd_env = TRD_ENV_MAP[config.TRADE_ENV]
        self.trd_market = TRD_MARKET_MAP[config.TRADE_MARKET]
        self.capital = config.TOTAL_CAPITAL
        self.min_amount = config.MIN_TRADE_AMOUNT
        self._trd_ctx = None
        self._stop_orders = {}  # code -> stop_loss_order_id
        self._ct_pos = _load_copytrade_positions()  # 跟单持仓

    def connect(self):
        self._trd_ctx = OpenSecTradeContext(
            security_firm=SecurityFirm.FUTUINC,
            host=self.host, port=self.port,
            filter_trdmarket=self.trd_market,
        )
        logger.info("已连接 OpenD %s:%s (交易)", self.host, self.port)
        ret, data = self._trd_ctx.unlock_trade(config.TRADE_PASSWORD)
        if ret != RET_OK:
            logger.error("解锁交易失败: %s", data)
            raise RuntimeError("解锁交易失败: %s" % data)
        logger.info("交易已解锁")
        self.sync_stop_orders()
        logger.info("跟单持仓: %s", self._ct_pos if self._ct_pos else "空")

    def close(self):
        if self._trd_ctx:
            self._trd_ctx.close()
        logger.info("已断开 OpenD 连接")

    def _update_ct_pos(self, code, delta):
        """更新跟单持仓记录，delta>0 买入，delta<0 卖出"""
        current = self._ct_pos.get(code, 0)
        new_qty = max(0, current + delta)
        if new_qty > 0:
            self._ct_pos[code] = new_qty
        else:
            self._ct_pos.pop(code, None)
        _save_copytrade_positions(self._ct_pos)
        logger.info("跟单持仓更新 %s: %d -> %d", code, current, new_qty)

    def reconcile_overnight(self, overnight_acct: dict, portfolio_codes: set = None):
        """盘前对账：比较夜盘快照和当前实际持仓，补录用户手动操作。

        overnight_acct: 夜盘检测到变化时保存的账户持仓 {code: qty}

        逻辑：
        - 当前持仓比夜盘快照多的部分 = 用户手动买入，补录到跟单记录
        - 当前持仓比夜盘快照少的部分 = 用户手动卖出，从跟单记录扣除
        """
        current_acct = self.get_my_positions()
        logger.info("盘前对账: 夜盘快照=%s, 当前持仓=%s", overnight_acct, current_acct)

        # 收集所有涉及的股票代码
        all_codes = set(overnight_acct.keys()) | set(current_acct.keys())

        # 只对账组合中的股票，避免把底仓误录为跟单
        if portfolio_codes:
            all_codes = all_codes & portfolio_codes

        for code in all_codes:
            old_qty = overnight_acct.get(code, 0)
            new_qty = current_acct.get(code, 0)
            delta = new_qty - old_qty

            if delta == 0:
                continue

            ct_qty = self._ct_pos.get(code, 0)

            if delta > 0:
                # 用户手动买入了 delta 股，补录到跟单记录
                logger.info("对账: %s 持仓增加 %d 股 (%d -> %d), 补录到跟单记录",
                            code, delta, old_qty, new_qty)
                self._update_ct_pos(code, delta)
            else:
                # 用户手动卖出了 abs(delta) 股，从跟单记录扣除
                reduce = min(abs(delta), ct_qty)
                if reduce > 0:
                    logger.info("对账: %s 持仓减少 %d 股 (%d -> %d), 从跟单记录扣除 %d",
                                code, abs(delta), old_qty, new_qty, reduce)
                    self._update_ct_pos(code, -reduce)
                else:
                    logger.info("对账: %s 持仓减少 %d 股, 但跟单记录为0, 可能是底仓变动",
                                code, abs(delta))

        logger.info("对账完成, 跟单持仓: %s", self._ct_pos if self._ct_pos else "空")


    def sync_stop_orders(self):
        """从账户同步所有未成交的卖出止损单到 _stop_orders。
        
        查询所有订单，筛选出未终结的 STOP_LIMIT 和 TRAILING_STOP_LIMIT 卖单，
        按 code 记录 order_id。这样不管是程序挂的还是手动挂的，都能感知到。
        """
        self._stop_orders.clear()

        # 不传 status_filter_list，查全部订单，代码里自己过滤
        ret, data = self._trd_ctx.order_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            logger.warning("同步止损单失败: %s", data)
            return

        if data.empty:
            logger.info("账户无未成交止损单")
            return

        # 已终结的状态，这些不需要关注
        terminal_statuses = {
            OrderStatus.FILLED_ALL,
            OrderStatus.CANCELLED_ALL,
            OrderStatus.FAILED,
            OrderStatus.DISABLED,
            OrderStatus.DELETED,
        }
        stop_types = {"STOP_LIMIT", "TRAILING_STOP_LIMIT", "TRAILING_STOP"}

        for _, row in data.iterrows():
            # 跳过已终结的订单
            if row["order_status"] in terminal_statuses:
                continue
            # 只关注卖出方向的止损单
            order_type_str = str(row["order_type"])
            if row["trd_side"] == TrdSide.SELL and order_type_str in stop_types:
                code = row["code"]
                order_id = str(row["order_id"])
                self._stop_orders[code] = order_id
                logger.info("同步到止损单 %s, order_id=%s, 类型=%s",
                            code, order_id, order_type_str)

    def cancel_stop_order(self, code):
        """撤销某只股票的止损单"""
        order_id = self._stop_orders.get(code)
        if not order_id:
            return
        logger.info("撤销止损单 %s, order_id=%s", code, order_id)
        ret, data = self._trd_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            logger.warning("撤销止损单失败 %s: %s", code, data)
        else:
            logger.info("止损单已撤销 %s", code)
        del self._stop_orders[code]

    def get_price(self, code):
        """通过 yfinance 获取实时价格

        优先级：
        1. 盘前: preMarketPrice
        2. 盘后: postMarketPrice
        3. 盘中/兜底: regularMarketPrice 或 fast_info.lastPrice
        """
        try:
            symbol = code.split(".")[-1] if "." in code else code
            ticker = yf.Ticker(symbol)

            # 先尝试从 info 拿盘前/盘后价格
            try:
                info = ticker.info
                pre = info.get("preMarketPrice")
                post = info.get("postMarketPrice")
                regular = info.get("regularMarketPrice") or info.get("currentPrice")

                if _is_regular_hours():
                    price = regular
                    src = "盘中"
                elif pre and pre > 0:
                    price = pre
                    src = "盘前"
                elif post and post > 0:
                    price = post
                    src = "盘后"
                else:
                    price = regular
                    src = "兜底(regularMarket)"

                if price and price > 0:
                    logger.info("yfinance %s价格 %s: %.2f", src, code, price)
                    return round(price, 2)
            except Exception as e:
                logger.warning("yfinance info 获取失败 %s: %s, 尝试 fast_info", code, e)

            # fallback: fast_info.lastPrice（盘前盘后可能是收盘价）
            fast = ticker.fast_info
            price = getattr(fast, "lastPrice", None) or getattr(fast, "last_price", None)
            if price and price > 0:
                logger.info("yfinance fast_info 价格 %s: %.2f (可能为收盘价)", code, price)
                return round(price, 2)

            logger.warning("yfinance 返回无效价格 %s", code)
        except Exception as e:
            logger.error("获取 %s 价格失败: %s", code, e)
        return None

    def get_my_positions(self):
        ret, data = self._trd_ctx.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            logger.error("查询持仓失败: %s", data)
            return {}
        positions = {}
        for _, row in data.iterrows():
            if row["qty"] > 0:
                positions[row["code"]] = int(row["qty"])
        return positions

    def calc_target_qty(self, code, weight):
        price = self.get_price(code)
        if not price or price <= 0:
            return 0
        target_amount = self.capital * weight
        if target_amount < self.min_amount:
            return 0
        return int(target_amount / price)

    def place_order(self, code, qty, side, use_market_order):
        """下单，返回 order_id 或 None"""
        if qty <= 0:
            return None

        price = self.get_price(code)
        if price is None:
            return None

        if use_market_order:
            order_type = OrderType.MARKET
            order_price = 0.0
            order_desc = "市价单"
        else:
            order_type = OrderType.NORMAL
            if side == TrdSide.BUY:
                order_price = round(price * (1 + LIMIT_SLIPPAGE), 2)
            else:
                order_price = round(price * (1 - LIMIT_SLIPPAGE), 2)
            order_desc = "限价单 @%s" % order_price

        side_name = "买入" if side == TrdSide.BUY else "卖出"
        logger.info("%s %s x %d (%s)", side_name, code, qty, order_desc)

        ret, data = self._trd_ctx.place_order(
            price=order_price,
            qty=qty,
            code=code,
            trd_side=side,
            order_type=order_type,
            trd_env=self.trd_env,
            fill_outside_rth=not use_market_order,
        )
        if ret != RET_OK:
            logger.error("%s %s 失败: %s", side_name, code, data)
            return None

        order_id = str(data["order_id"].iloc[0])
        logger.info("%s %s x %d 下单成功, order_id=%s", side_name, code, qty, order_id)
        return order_id

    def modify_order_price(self, order_id, code, qty, side, reprice_count):
        """改价重挂，返回是否成功"""
        price = self.get_price(code)
        if price is None:
            return False

        total_slippage = LIMIT_SLIPPAGE + REPRICE_SLIPPAGE * reprice_count
        if side == TrdSide.BUY:
            new_price = round(price * (1 + total_slippage), 2)
        else:
            new_price = round(price * (1 - total_slippage), 2)

        logger.info("改价 order_id=%s -> @%s (第%d次)", order_id, new_price, reprice_count)

        ret, data = self._trd_ctx.modify_order(
            modify_order_op=ModifyOrderOp.NORMAL,
            order_id=order_id,
            qty=qty,
            price=new_price,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            logger.error("改价失败 order_id=%s: %s", order_id, data)
            return False
        return True

    def check_order_status(self, order_id):
        """查询订单状态，返回 'filled' / 'pending' / 'failed'"""
        ret, data = self._trd_ctx.order_list_query(
            order_id=order_id, trd_env=self.trd_env
        )
        if ret != RET_OK or len(data) == 0:
            return "pending"
        status = data["order_status"].iloc[0]
        if status == OrderStatus.FILLED_ALL:
            return "filled"
        if status in (OrderStatus.CANCELLED_ALL, OrderStatus.FAILED,
                      OrderStatus.DISABLED, OrderStatus.DELETED):
            return "failed"
        return "pending"

    def chase_orders(self, pending_orders, side):
        """追单逻辑，返回已成交的 [(code, qty, order_id)]"""
        filled_list = []
        side_name = "买入" if side == TrdSide.BUY else "卖出"

        while pending_orders:
            time.sleep(ORDER_CHECK_INTERVAL)

            done_ids = []
            for order_id, (code, qty, reprice_count) in list(pending_orders.items()):
                status = self.check_order_status(order_id)

                if status == "filled":
                    logger.info("%s %s x %d 已成交, order_id=%s", side_name, code, qty, order_id)
                    notify_order_filled(side_name, code, qty, order_id)
                    filled_list.append((code, qty, order_id))
                    done_ids.append(order_id)
                elif status == "failed":
                    logger.error("%s %s 订单失败, order_id=%s", side_name, code, order_id)
                    done_ids.append(order_id)
                elif status == "pending":
                    if reprice_count >= MAX_REPRICE_TIMES:
                        logger.warning("%s %s 达到最大改价次数(%d), order_id=%s",
                                       side_name, code, MAX_REPRICE_TIMES, order_id)
                        notify_order_timeout(side_name, code, qty, order_id)
                        done_ids.append(order_id)
                    else:
                        new_count = reprice_count + 1
                        ok = self.modify_order_price(order_id, code, qty, side, new_count)
                        if ok:
                            pending_orders[order_id] = (code, qty, new_count)
                        else:
                            notify_order_timeout(side_name, code, qty, order_id)
                            done_ids.append(order_id)

            for oid in done_ids:
                del pending_orders[oid]

        return filled_list

    def execute_diff(self, diff, use_market_order=True, skip_sells=False):
        """根据持仓变化执行跟单交易，先卖后买。

        卖出数量基于跟单持仓记录（_ct_pos），不动底仓。
        买入数量 = 目标股数 - 跟单已持有股数。

        skip_sells: 盘前盘后跳过卖出，只更新跟单记录
        """
        sell_tasks = []
        buy_tasks = []

        # 清仓：只卖跟单记录里的数量，保护底仓
        for pos in diff["removed"]:
            code = pos["code"]
            ct_qty = self._ct_pos.get(code, 0)
            if ct_qty > 0:
                if skip_sells:
                    # 盘前盘后跳过卖出，直接更新跟单记录
                    logger.info("盘前盘后跳过卖出 %s, 已从跟单记录删除", code)
                    self._update_ct_pos(code, -ct_qty)
                else:
                    sell_tasks.append((code, ct_qty))
            else:
                # 跟单记录为0，不自动卖出以保护底仓，发通知让用户手动处理
                logger.warning("清仓信号 %s, 但跟单记录为0, 跳过卖出(保护底仓), 请手动处理", code)
                from notify import notify_error
                notify_error("清仓信号 %s, 但跟单记录为0, 已跳过自动卖出以保护底仓, 请手动检查" % code)

        # 调仓
        for pos in diff["changed"]:
            code = pos["code"]
            new_weight = pos["new_weight"]
            target_qty = self.calc_target_qty(code, new_weight)
            ct_qty = self._ct_pos.get(code, 0)
            delta = target_qty - ct_qty
            if delta < 0:
                if skip_sells:
                    # 盘前盘后跳过减仓卖出，直接更新跟单记录
                    logger.info("盘前盘后跳过减仓卖出 %s x %d, 已从跟单记录扣除", code, abs(delta))
                    self._update_ct_pos(code, -abs(delta))
                else:
                    sell_tasks.append((code, abs(delta)))
            elif delta > 0:
                buy_tasks.append((code, new_weight))

        # 新增
        for pos in diff["added"]:
            code = pos["code"]
            weight = pos.get("weight", 0)
            buy_tasks.append((code, weight))

        if sell_tasks:
            self._execute_sells(sell_tasks, use_market_order)
        if buy_tasks:
            self._execute_buys(buy_tasks, use_market_order)

    def _execute_sells(self, sell_tasks, use_market_order):
        """批量卖出，卖出前检查实际持仓并撤销止损单。
        
        卖出数量取 min(跟单记录, 实际持仓)，防止超卖。
        """
        my_positions = self.get_my_positions()

        pending = {}
        for code, qty in sell_tasks:
            actual_qty = my_positions.get(code, 0)
            if actual_qty <= 0:
                logger.info("跳过卖出 %s, 实际持仓已为0 (可能已被止损卖出)", code)
                self._stop_orders.pop(code, None)
                # 清除跟单记录
                self._update_ct_pos(code, -self._ct_pos.get(code, 0))
                continue

            self.cancel_stop_order(code)

            # 不能卖超过实际持仓
            sell_qty = min(qty, actual_qty)
            order_id = self.place_order(code, sell_qty, TrdSide.SELL, use_market_order)
            if order_id:
                if use_market_order:
                    notify_order_filled("卖出", code, sell_qty, order_id)
                    self._update_ct_pos(code, -sell_qty)
                else:
                    pending[order_id] = (code, sell_qty, 0)

        if pending:
            filled = self.chase_orders(pending, TrdSide.SELL)
            for code, qty, _ in filled:
                self._update_ct_pos(code, -qty)
            if filled:
                logger.info("卖出完成: %d 笔成交", len(filled))

    def _execute_buys(self, buy_tasks, use_market_order):
        """批量买入，限价单追单，成交后自动挂止损（已有止损单的跳过）。
        
        买入数量 = 目标股数 - 跟单已持有股数。
        """
        pending = {}
        for code, weight in buy_tasks:
            target_qty = self.calc_target_qty(code, weight)
            ct_qty = self._ct_pos.get(code, 0)
            buy_qty = target_qty - ct_qty
            if buy_qty > 0:
                order_id = self.place_order(code, buy_qty, TrdSide.BUY, use_market_order)
                if order_id:
                    if use_market_order:
                        notify_order_filled("买入", code, buy_qty, order_id)
                        self._update_ct_pos(code, buy_qty)
                        self._maybe_place_stop_loss(code, buy_qty)
                    else:
                        pending[order_id] = (code, buy_qty, 0)

        if pending:
            filled = self.chase_orders(pending, TrdSide.BUY)
            if filled:
                logger.info("买入完成: %d 笔成交", len(filled))
                for code, qty, _ in filled:
                    self._update_ct_pos(code, qty)
                    self._maybe_place_stop_loss(code, qty)

    def _maybe_place_stop_loss(self, code, qty):
        """买入成交后挂止损，如果该股票已有止损单则跳过"""
        if code in self._stop_orders:
            logger.info("跳过挂止损 %s, 已有止损单 order_id=%s", code, self._stop_orders[code])
            return
        buy_price = self.get_price(code)
        if buy_price:
            sl_id = place_stop_loss(self._trd_ctx, code, qty, buy_price, self.trd_env)
            if sl_id:
                self._stop_orders[code] = sl_id
