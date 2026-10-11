#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - 默契值→专属优惠券 CLI（Step 12）

核心变化（vs 旧版）：
- 不再从麦当劳真实券池抽券
- 生成「和我一起麦」产品自有的专属虚拟优惠券
- 优惠券金额按默契值档位决定，单笔订单减免 1~2.5 元
- 两笔订单各自独立享受同等折扣

正确时序：锁定(prepare) → 默契值(compute) → 抽专属券(draw) → 支付(calculate-price → create-order)

档位 → 概率 & 减免金额：
  0~39  无默契：0%   不抽券，减免 0 元
  40~69 普通默契：30% 概率，减免 1 元/单
  70~89 高级默契：60% 概率，减免 1.5 元/单
  90~100 默契拉满：90% 概率，减免 2.5 元/单

优惠券性质：
- 这是「和我一起麦」产品的专属虚拟券，不是麦当劳现存的 couponId/couponCode
- MCP 的 calculate-price 和 create-order 仍按原价执行
- 减免金额由「和我一起麦」产品侧承担（MVP 阶段为产品承诺，后续可对接真实补贴通道）
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from space import load_space, save_space, log_event, now_iso, out, fail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 档位 → 抽券概率 & 减免金额（元/单）
TIER_CONFIG = {
    "默契拉满": {"probability": 0.90, "discountPerOrder": 2.5},
    "高级默契": {"probability": 0.60, "discountPerOrder": 1.5},
    "普通默契": {"probability": 0.30, "discountPerOrder": 1.0},
    "无默契":   {"probability": 0.00, "discountPerOrder": 0},
}

# 优惠券标题模板
COUPON_TITLE = "和我一起麦专属优惠券"
COUPON_DESC = {
    "默契拉满": "默契拉满！单笔订单减免2.5元",
    "高级默契": "高级默契！单笔订单减免1.5元",
    "普通默契": "普通默契！单笔订单减免1元",
    "无默契":   "",
}


def cmd_draw(args):
    """根据默契值档位抽取「和我一起麦」专属优惠券"""
    space = load_space(args.space)
    fr = space["finalResult"]

    if fr.get("compatibilityScore") is None:
        fail("默契值尚未计算，请先执行 score.py compute")

    tier = fr["rewardTier"]
    config = TIER_CONFIG.get(tier, TIER_CONFIG["无默契"])
    probability = config["probability"]
    discount = config["discountPerOrder"]

    # --- 无默契：直接返回 ---
    if probability == 0:
        fr["rewardCoupon"] = None
        fr["orderDiscount"] = {"perOrder": 0, "totalDiscount": 0}
        save_space(space)
        out({
            "success": True,
            "麦麦默契值": fr["compatibilityScore"],
            "档位": tier,
            "概率": "0%",
            "结果": "未抽中优惠券（当前档位无抽奖资格）",
            "broadcast": "本次默契值 %d 分（%s），暂无额外优惠" % (fr["compatibilityScore"], tier),
        })
        return

    # --- 抽奖 ---
    hit = random.random() < probability

    if not hit:
        fr["rewardCoupon"] = None
        fr["orderDiscount"] = {"perOrder": 0, "totalDiscount": 0}
        log_event(space, "system", "reward_draw", hit=False, tier=tier, probability=probability)
        save_space(space)
        out({
            "success": True,
            "麦麦默契值": fr["compatibilityScore"],
            "档位": tier,
            "概率": "%d%%" % round(probability * 100),
            "结果": "未抽中",
            "broadcast": "默契值 %d 分（%s），运气稍差，下次再接再厉！" % (fr["compatibilityScore"], tier),
        })
        return

    # --- 抽中了：生成专属优惠券 ---
    coupon = {
        "title": "%s（%s）" % (COUPON_TITLE, tier),
        "type": "yiqi-mai-exclusive",
        "discountPerOrder": discount,          # 元
        "totalDiscount": round(discount * 2, 2),  # 两单合计减免
        "discountDesc": COUPON_DESC[tier],
        "tier": tier,
        "score": fr["compatibilityScore"],
        "issuedAt": now_iso(),
        # 注意：无 couponId/couponCode，这是产品侧虚拟券，不走 MCP 券系统
        "note": "本券由「和我一起麦」产品发放，减免金额从产品侧结算，不影响 MCP 下单流程",
    }

    fr["rewardCoupon"] = coupon
    fr["orderDiscount"] = {
        "perOrder": discount,
        "totalDiscount": round(discount * 2, 2),
        "couponTitle": coupon["title"],
    }

    log_event(space, "system", "reward_draw",
              hit=True, tier=tier, probability=probability,
              discountPerOrder=discount, totalDiscount=round(discount * 2, 2))
    save_space(space)

    out({
        "success": True,
        "麦麦默契值": fr["compatibilityScore"],
        "档位": tier,
        "概率": "%d%%" % round(probability * 100),
        "结果": "抽中！",
        "优惠券": coupon,
        "每单减免": "%.1f元" % discount,
        "两单合计减免": "%.1f元" % round(discount * 2, 2),
        "broadcast": "🎉 默契值 %d 分（%s），恭喜获得【%s】！每单减免%.1f元" % (
            fr["compatibilityScore"], tier, coupon["title"], discount),
        "下一步": "调用 calculate-price（按原价）→ create-order（按原价）→ 实付金额=试算价-%.1f元" % discount,
    })


def cmd_show(args):
    """查看专属优惠券抽取结果"""
    space = load_space(args.space)
    fr = space["finalResult"]

    if fr.get("compatibilityScore") is None:
        fail("默契值尚未计算，请先执行 score.py compute")

    out({
        "success": True,
        "spaceId": space["spaceId"],
        "麦麦默契值": fr["compatibilityScore"],
        "档位": fr.get("rewardTier"),
        "专属优惠券": fr.get("rewardCoupon"),
        "每单减免": fr.get("orderDiscount", {}).get("perOrder", 0),
        "两单合计减免": fr.get("orderDiscount", {}).get("totalDiscount", 0),
    })


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - 默契值→专属优惠券")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("draw", help="根据默契值档位抽取「和我一起麦」专属优惠券")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_draw)

    p = sub.add_parser("show", help="查看专属优惠券结果")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
