"""
邮件通知模块
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import config

logger = logging.getLogger(__name__)


def send_email(subject: str, body: str):
    """发送邮件通知"""
    if not config.EMAIL_ENABLED:
        return

    msg = MIMEMultipart()
    msg["From"] = config.EMAIL_SENDER
    msg["To"] = config.EMAIL_RECEIVER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL(config.EMAIL_SMTP_HOST, config.EMAIL_SMTP_PORT) as server:
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            server.sendmail(config.EMAIL_SENDER, config.EMAIL_RECEIVER, msg.as_string())
        logger.info(f"邮件已发送: {subject}")
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")


def notify_changes(diff: dict):
    """持仓变化时发送邮件通知"""
    lines = ["检测到模拟组合持仓变化:\n"]

    for p in diff.get("added", []):
        pending = " [待成交]" if p.get("is_pending") else ""
        lines.append(f"[新增] {p['code']} {p['name']} 权重={p.get('weight', 0):.1%}{pending}")

    for p in diff.get("removed", []):
        lines.append(f"[清仓] {p['code']} {p['name']}")

    for p in diff.get("changed", []):
        pending = " [待成交]" if p.get("is_pending") else ""
        lines.append(f"[调仓] {p['code']} {p['name']} {p['old_weight']:.1%} -> {p['new_weight']:.1%}{pending}")

    body = "\n".join(lines)
    send_email(f"跟单通知 - 组合 {config.PORTFOLIO_ID} 持仓变化", body)


def notify_order_filled(side: str, code: str, qty: int, order_id: str):
    """买入/卖出成交通知"""
    body = "%s成交\n股票: %s\n数量: %d\n订单号: %s" % (side, code, qty, order_id)
    send_email("跟单通知 - %s %s x %d 成交" % (side, code, qty), body)


def notify_order_timeout(side: str, code: str, qty: int, order_id: str):
    """订单超时未成交通知"""
    body = "%s超时未成交，请手动处理\n股票: %s\n数量: %d\n订单号: %s" % (side, code, qty, order_id)
    send_email("跟单警告 - %s %s 超时未成交" % (code, side), body)


def notify_stop_loss_placed(code: str, qty: int, mode: str, order_id: str, detail: str = ""):
    """止损单挂单通知"""
    mode_name = "固定止损" if mode == "fixed" else "跟踪止损"
    body = "%s已挂单\n股票: %s\n数量: %d\n订单号: %s" % (mode_name, code, qty, order_id)
    if detail:
        body += "\n" + detail
    send_email("跟单通知 - %s %s 已挂%s" % (code, qty, mode_name), body)


def notify_stop_loss_failed(code: str, qty: int, reason: str = ""):
    """止损单挂单失败通知"""
    body = "止损单挂单失败，请手动处理\n股票: %s\n数量: %d" % (code, qty)
    if reason:
        body += "\n原因: " + reason
    send_email("跟单警告 - %s 止损挂单失败" % code, body)

def notify_error(error_msg: str):
    """程序运行出错时发送邮件"""
    send_email("跟单报错 - 程序异常", "跟单程序运行出错，请检查:\n\n%s" % error_msg)



def notify_overnight_change(diff: dict):
    """夜盘时段持仓变化通知，提醒手动操作"""
    lines = ["⚠️ 夜盘时段检测到模拟组合持仓变化，请手动操作:\n"]

    for p in diff.get("added", []):
        pending = " [待成交]" if p.get("is_pending") else ""
        lines.append(f"[新增] {p['code']} {p['name']} 权重={p.get('weight', 0):.1%}{pending}")

    for p in diff.get("removed", []):
        lines.append(f"[清仓] {p['code']} {p['name']}")

    for p in diff.get("changed", []):
        pending = " [待成交]" if p.get("is_pending") else ""
        lines.append(f"[调仓] {p['code']} {p['name']} {p['old_weight']:.1%} -> {p['new_weight']:.1%}{pending}")

    lines.append("\n当前为夜盘时段(20:00-4:00 ET)，程序不会自动下单。")
    lines.append("请在盘前/盘中时段手动处理，或等待下次可交易时段自动执行。")

    body = "\n".join(lines)
    send_email(f"跟单通知 - 夜盘变动(需手动) 组合 {config.PORTFOLIO_ID}", body)
