#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - 麦麦默契值计算 CLI（Step 11）
负责：
- 从双方购物车提取 productCode 集合
- 计算 Jaccard 相似度 → 0~100 麦麦默契值
- 列出相同商品 / 各自独有商品
- 将结果写入 space.finalResult

公式：
  SA = 购物车A 的顶层 productCode 去重集合
  SB = 购物车B 的顶层 productCode 去重集合
  默契值 = round( |SA ∩ SB| / |SA ∪ SB| × 100 )

触发时机：checkout.py prepare 锁定购物车后，下单支付前
正确时序：锁定(prepare) → 默契值(compute) → 抽专属券(reward draw) → 支付(calculate-price → create-order)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from space import load_space, save_space, log_event, now_iso, out, fail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 档位阈值（已拍板）
TIERS = [
    (90, "默契拉满"),
    (70, "高级默契"),
    (40, "普通默契"),
    (0,  "无默契"),
]


def classify_tier(score):
    for threshold, label in TIERS:
        if score >= threshold:
            return label, threshold
    return "无默契", 0


def cmd_compute(args):
    space = load_space(args.space)

    # 前置检查：双方必须有商品
    cart_a = space["carts"]["cart-A"]
    cart_b = space["carts"]["cart-B"]
    if not cart_a["items"]:
        fail("A 的购物车是空的，无法计算默契值")
    if not cart_b["items"]:
        fail("B 的购物车是空的，无法计算默契值")

    # 构建 productCode 去重集合
    set_a = set(it["productCode"] for it in cart_a["items"])
    set_b = set(it["productCode"] for it in cart_b["items"])

    intersection = set_a & set_b
    union = set_a | set_b

    if not union:
        fail("双方购物车商品集合为空，无法计算")

    # Jaccard → 麦麦默契值
    score = round(len(intersection) / len(union) * 100)

    # 商品名称映射（用于友好展示）
    name_map = {}
    for it in cart_a["items"] + cart_b["items"]:
        name_map[it["productCode"]] = it["productName"]

    # 各自独有商品
    only_a = sorted([{"code": c, "name": name_map.get(c, c)} for c in set_a - set_b],
                    key=lambda x: x["name"])
    only_b = sorted([{"code": c, "name": name_map.get(c, c)} for c in set_b - set_a],
                    key=lambda x: x["name"])
    common = sorted([{"code": c, "name": name_map.get(c, c)} for c in intersection],
                    key=lambda x: x["name"])

    # 档位
    tier_label, tier_threshold = classify_tier(score)

    # 写入 finalResult
    space["finalResult"] = {
        "compatibilityScore": score,
        "commonItems": common,
        "onlyA": only_a,
        "onlyB": only_b,
        "rewardTier": tier_label,
        "rewardTierThreshold": tier_threshold,
        "rewardCoupon": None,  # 由 reward.py 填充
        "computedAt": now_iso(),
    }

    log_event(space, "system", "compute_score",
              score=score, tier=tier_label,
              commonCount=len(common), onlyACount=len(only_a), onlyBCount=len(only_b))
    save_space(space)

    out({
        "success": True,
        "spaceId": space["spaceId"],
        "麦麦默契值": score,
        "档位": tier_label,
        "相同商品": common,
        "A独有": only_a,
        "B独有": only_b,
        "broadcast": "🎯 麦麦默契值：%d 分！%s" % (score, _broadcast_suffix(score, tier_label, common)),
        "下一步": "调用 reward.py draw --space %s 抽取「和我一起麦」专属优惠券，然后下单" % space["spaceId"],
    })


def _broadcast_suffix(score, tier, common):
    if score >= 90:
        return "默契拉满！你们简直心有灵犀！"
    elif score >= 70:
        names = "、".join(c["name"] for c in common[:3])
        return "高级默契！你们都选了%s%s" % (names, "等" if len(common) > 3 else "")
    elif score >= 40:
        names = "、".join(c["name"] for c in common[:2])
        return "普通默契，你们都选了%s" % names if names else "普通默契"
    else:
        return "你们口味差异挺大的，但这也挺有趣！"


def cmd_show(args):
    space = load_space(args.space)
    fr = space["finalResult"]
    if fr.get("compatibilityScore") is None:
        fail("默契值尚未计算，请先执行 compute")
    out({
        "success": True,
        "spaceId": space["spaceId"],
        "麦麦默契值": fr["compatibilityScore"],
        "档位": fr["rewardTier"],
        "相同商品": fr["commonItems"],
        "A独有": fr["onlyA"],
        "B独有": fr["onlyB"],
        "优惠券": fr["rewardCoupon"],
        "computedAt": fr["computedAt"],
    })


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - 麦麦默契值")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("compute", help="计算双方购物车默契值（锁定后、支付前调用）")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_compute)

    p = sub.add_parser("show", help="查看已计算的默契值结果")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
