"""
监控模块：抓取富途公开模拟组合的持仓数据
"""

import json
import logging
import time
import requests
from pathlib import Path
import config

logger = logging.getLogger(__name__)

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"

# 富途组合持仓 API
PORTFOLIO_API = "https://portfolio.futunn.com/portfolio-api/get-portfolio-position"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.futunn.com/",
    "Origin": "https://www.futunn.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fetch_portfolio(portfolio_id: str, cookie: str = "") -> list[dict] | None:
    """
    抓取公开组合持仓数据。

    返回格式:
    [
        {
            "code": "US.AGQ",
            "name": "2倍做多白银ETF-ProShares",
            "weight": 0.20,          # 持仓权重 (0-1)
            "stock_id": 202635,
        },
        ...
    ]
    """
    params = {
        "portfolio_id": portfolio_id,
        "language": 0,
        "_": int(time.time() * 1000),
    }

    headers = {**HEADERS}
    if cookie:
        headers["Cookie"] = cookie

    try:
        resp = requests.get(PORTFOLIO_API, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        logger.error(f"请求组合数据失败: {e}")
        return None

    if result.get("code") != 0:
        logger.error(f"API 返回错误: {result.get('message')}")
        return None

    data = result.get("data", {})
    record_items = data.get("record_items", [])

    if not record_items:
        logger.info("组合持仓为空（已全部清仓）")
        return []

    # 市场代码映射
    market_map = {1: "HK", 2: "US", 3: "SH", 4: "SZ"}

    positions = []
    for item in record_items:
        market_prefix = market_map.get(item["market"], "US")
        code = f"{market_prefix}.{item['stock_code']}"

        # 用 total_ratio（已成交 + 待成交），这样非盘中的挂单也能捕捉到
        # ratio 是 10 亿为基数的比例值，转换为 0-1
        total = item.get("total_ratio", 0)
        position = item.get("position_ratio", 0)
        pending = item.get("pending_ratio", 0)
        weight = total / 1_000_000_000

        # status: 2=已成交, 其他值=待成交
        status = item.get("status", 0)
        is_pending = pending > 0

        positions.append({
            "code": code,
            "name": item.get("stock_name", ""),
            "weight": round(weight, 6),
            "position_weight": round(position / 1_000_000_000, 6),
            "pending_weight": round(pending / 1_000_000_000, 6),
            "is_pending": is_pending,
            "stock_id": item.get("stock_id"),
        })

    logger.info(f"获取到 {len(positions)} 只持仓: "
                + ", ".join(f"{p['code']}({p['weight']:.1%})" for p in positions))
    return positions


def load_snapshot() -> list[dict]:
    """加载上次的持仓快照"""
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_snapshot(positions: list[dict]):
    """保存当前持仓快照"""
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(positions, f, ensure_ascii=False, indent=2)


def diff_positions(old: list[dict], new: list[dict]) -> dict:
    """
    对比新旧持仓，返回变化。

    返回:
    {
        "added": [...],    # 新买入
        "removed": [...],  # 已清仓
        "changed": [...],  # 调仓（权重变化超过阈值）
    }
    """
    old_map = {p["code"]: p for p in old}
    new_map = {p["code"]: p for p in new}

    added = [p for code, p in new_map.items() if code not in old_map]
    removed = [p for code, p in old_map.items() if code not in new_map]

    changed = []
    for code, new_pos in new_map.items():
        if code in old_map:
            old_pos = old_map[code]
            old_w = old_pos.get("weight", 0)
            new_w = new_pos.get("weight", 0)
            if abs(new_w - old_w) > config.WEIGHT_CHANGE_THRESHOLD:
                changed.append({
                    **new_pos,
                    "old_weight": old_w,
                    "new_weight": new_w,
                })

    return {"added": added, "removed": removed, "changed": changed}


def has_changes(diff: dict) -> bool:
    """检查是否有持仓变化"""
    return bool(diff["added"] or diff["removed"] or diff["changed"])
