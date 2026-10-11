#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - 门店上下文 & 菜单管理 CLI
负责：
- set-context：为每个成员独立设置取餐方式/门店/地址（各回各家的基础）
- import-menu：把 mcd-mcp query-meals 的真实返回导入空间，作为该成员购物车的唯一合法商品池
- menu：查看某成员的菜单

关键规则：菜单按人隔离。cart-A 只能加 user-A 门店菜单里的商品，cart-B 同理。
本脚本不生成任何商品数据，只导入 MCP 真实返回。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from space import load_space, save_space, log_event, now_iso, out, fail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SUPPORTED_BE_TYPES = (1, 2, 5)  # 1到店自取 2麦乐送 5得来速；6团餐不在 MVP 范围


def find_participant(space, who):
    for p in space["participants"]:
        if p["userId"] == who or p["nickname"] == who:
            return p
    fail("「%s」不在空间中" % who,
         participants=[p["nickname"] for p in space["participants"]])


def cmd_set_context(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    be_type = args.be_type

    if be_type not in SUPPORTED_BE_TYPES:
        fail("MVP 仅支持 beType 1(到店自取) / 2(麦乐送) / 5(得来速)，不支持 %s" % be_type)
    if be_type == 1 and args.be_code:
        fail("到店自取(beType=1) 不应传 beCode")
    if be_type in (2, 5) and not args.be_code:
        fail("beType=%s 必须传 beCode（外送来自 delivery-query-stores，得来速来自 query-nearby-stores）" % be_type)
    if be_type == 2 and not args.address_id:
        fail("麦乐送(beType=2) 必须传 addressId（来自 delivery-query-addresses / delivery-create-address）")

    order_type = 2 if be_type == 2 else 1
    old_store = (p.get("storeContext") or {}).get("storeCode")
    p["storeContext"] = {
        "beType": be_type,
        "orderType": order_type,
        "storeCode": args.store_code,
        "beCode": args.be_code,
        "storeName": args.store_name,
        "addressId": args.address_id,
        "takeWayCode": None,  # 到店场景结算试算后填
    }
    # 换门店后旧菜单作废，防止拿别家门店的商品下单
    menus = space.setdefault("menus", {})
    if old_store and old_store != args.store_code and menus.get(p["userId"]):
        del menus[p["userId"]]
        menu_note = "门店已更换，旧菜单已作废，需重新 import-menu"
    else:
        menu_note = None

    log_event(space, p["userId"], "set_context", beType=be_type, storeCode=args.store_code)
    save_space(space)
    out({
        "success": True,
        "message": "%s 的取餐上下文已设置：%s（beType=%d, storeCode=%s）" % (
            p["nickname"], args.store_name or "未命名门店", be_type, args.store_code),
        "participant": p["nickname"],
        "storeContext": p["storeContext"],
        "note": menu_note or "下一步：调用 query-meals 拉取该门店菜单，然后 import-menu",
    })


def cmd_import_menu(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    ctx = p.get("storeContext")
    if not ctx:
        fail("%s 尚未设置门店上下文，请先 set-context" % p["nickname"])
    if not os.path.exists(args.file):
        fail("菜单文件不存在: %s" % args.file)

    with open(args.file, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data") or {}
    meals_map = data.get("meals")
    if not meals_map:
        fail("文件不是合法的 query-meals 返回（缺少 data.meals）")

    # 分类归属
    code_to_category = {}
    for cat in data.get("categories", []):
        for m in cat.get("meals", []):
            code_to_category[m.get("code")] = cat.get("name")

    items = {}
    for code, meal in meals_map.items():
        price = None
        if meal.get("currentPrice") not in (None, ""):
            try:
                price = float(meal["currentPrice"])
            except (TypeError, ValueError):
                price = None
        items[code] = {
            "name": meal.get("name"),
            "price": price,
            "image": meal.get("image"),
            "category": code_to_category.get(code),
        }

    menus = space.setdefault("menus", {})
    menus[p["userId"]] = {
        "storeCode": ctx["storeCode"],
        "orderType": ctx["orderType"],
        "beType": ctx["beType"],
        "fetchedAt": now_iso(),
        "itemCount": len(items),
        "items": items,
    }
    log_event(space, p["userId"], "import_menu", storeCode=ctx["storeCode"], itemCount=len(items))
    save_space(space)
    out({
        "success": True,
        "message": "%s 的菜单已导入：门店 %s，共 %d 个商品" % (p["nickname"], ctx["storeCode"], len(items)),
        "participant": p["nickname"],
        "storeCode": ctx["storeCode"],
        "itemCount": len(items),
    })


def cmd_menu(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    menu = space.get("menus", {}).get(p["userId"])
    if not menu:
        fail("%s 的菜单尚未导入，请先 query-meals + import-menu" % p["nickname"])
    grouped = {}
    for code, item in menu["items"].items():
        cat = item.get("category") or "其他"
        if args.category and args.category not in cat:
            continue
        grouped.setdefault(cat, []).append({
            "productCode": code, "name": item["name"], "price": item["price"],
        })
    out({
        "success": True,
        "participant": p["nickname"],
        "storeCode": menu["storeCode"],
        "fetchedAt": menu["fetchedAt"],
        "categories": grouped,
    })


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - 门店上下文与菜单")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("set-context", help="设置成员的取餐方式/门店/地址")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True, help="成员昵称或 userId")
    p.add_argument("--be-type", type=int, required=True, help="1到店自取 2麦乐送 5得来速")
    p.add_argument("--store-code", required=True)
    p.add_argument("--be-code", default=None)
    p.add_argument("--store-name", default=None)
    p.add_argument("--address-id", default=None)
    p.set_defaults(func=cmd_set_context)

    p = sub.add_parser("import-menu", help="导入 query-meals 真实返回（JSON 文件）")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--file", required=True, help="query-meals 原始返回保存的 JSON 文件路径")
    p.set_defaults(func=cmd_import_menu)

    p = sub.add_parser("menu", help="查看成员菜单")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--category", default=None, help="按分类名过滤")
    p.set_defaults(func=cmd_menu)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
