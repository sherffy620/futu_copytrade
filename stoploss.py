"""
止损模块：买入成交后自动挂止损单

支持两种模式：
1. 固定比例止损：跌破买入价 x (1 - stop_ratio) 触发卖出
2. 跟踪止损：从最高价回撤 trail_value 触发卖出
"""

import logging
from moomoo import (
    TrdSide,
    OrderType,
    TrailType,
    RET_OK,
)

import config
from notify import notify_stop_loss_placed, notify_stop_loss_failed

logger = logging.getLogger(__name__)


def place_stop_loss(trd_ctx, code, qty, buy_price, trd_env):
    """
    根据配置挂止损单。买入成交后调用。

    config.STOP_LOSS_MODE:
        "fixed"    -> 固定比例止损
        "trailing" -> 跟踪止损
        "none"     -> 不挂止损
    """
    mode = getattr(config, "STOP_LOSS_MODE", "none")

    if mode == "none":
        return None

    if mode == "fixed":
        return _place_fixed_stop(trd_ctx, code, qty, buy_price, trd_env)
    elif mode == "trailing":
        return _place_trailing_stop(trd_ctx, code, qty, buy_price, trd_env)
    else:
        logger.warning("未知止损模式: %s", mode)
        return None


def _place_fixed_stop(trd_ctx, code, qty, buy_price, trd_env):
    """
    固定比例止损。
    触发价 = 买入价 * (1 - STOP_LOSS_RATIO)
    卖出价 = 触发价 * (1 - 0.002)  略低于触发价确保成交
    """
    ratio = getattr(config, "STOP_LOSS_RATIO", 0.05)
    trigger_price = round(buy_price * (1 - ratio), 2)
    sell_price = round(trigger_price * (1 - 0.002), 2)

    logger.info("挂固定止损 %s x %d, 触发价=%s (-%s%%)",
                code, qty, trigger_price, round(ratio * 100, 1))

    ret, data = trd_ctx.place_order(
        price=sell_price,
        qty=qty,
        code=code,
        trd_side=TrdSide.SELL,
        order_type=OrderType.STOP_LIMIT,
        aux_price=trigger_price,
        trd_env=trd_env,
        fill_outside_rth=False,
    )
    if ret != RET_OK:
        logger.error("挂固定止损失败 %s: %s", code, data)
        notify_stop_loss_failed(code, qty, str(data))
        return None

    order_id = str(data["order_id"].iloc[0])
    logger.info("固定止损已挂 %s, order_id=%s", code, order_id)
    notify_stop_loss_placed(code, qty, "fixed", order_id,
                            "触发价=%s (-%s%%)" % (trigger_price, round(ratio * 100, 1)))
    return order_id


def _place_trailing_stop(trd_ctx, code, qty, buy_price, trd_env):
    """
    跟踪止损。用 buy_price 作为初始锚点，券商自动跟踪最高价。
    """
    trail_mode = getattr(config, "TRAIL_TYPE", "ratio")
    trail_val = getattr(config, "TRAIL_VALUE", 0.05)

    if trail_mode == "ratio":
        t_type = TrailType.RATIO
        logger.info("挂跟踪止损 %s x %d, 回撤比例=%s%%", code, qty, trail_val)
    else:
        t_type = TrailType.AMOUNT
        logger.info("挂跟踪止损 %s x %d, 回撤金额=%s", code, qty, trail_val)

    ret, data = trd_ctx.place_order(
        price=0,
        qty=qty,
        code=code,
        trd_side=TrdSide.SELL,
        order_type=OrderType.TRAILING_STOP,
        aux_price=buy_price,
        trail_type=t_type,
        trail_value=trail_val,
        trd_env=trd_env,
        fill_outside_rth=False,
    )
    if ret != RET_OK:
        logger.error("挂跟踪止损失败 %s: %s", code, data)
        notify_stop_loss_failed(code, qty, str(data))
        return None

    order_id = str(data["order_id"].iloc[0])
    logger.info("跟踪止损(市价)已挂 %s, order_id=%s", code, order_id)
    trail_desc = "回撤比例=%s%%" % trail_val if trail_mode == "ratio" else "回撤金额=%s" % trail_val
    notify_stop_loss_placed(code, qty, "trailing", order_id, trail_desc)
    return order_id
