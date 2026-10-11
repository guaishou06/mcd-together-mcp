#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - 双人购物车 CLI
负责：两个独立购物车的增删改查 / 双方互查互改 / 变更播报（changelog）
规则：
- 双方均可操作任一购物车（共享可见 + 均可编辑）
- waiting / active 状态可编辑；ordering / completed 锁定
- 商品信息（code/名称/单价/图片）必须由调用方从 mcd-mcp query-meals 真实返回中取得，本脚本只负责存储与同步，不生成商品数据
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from space import load_space, save_space, log_event, now_iso, out, fail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

EDITABLE_STATUS = ("waiting", "active")


def resolve_cart(space, cart_arg):
    key = cart_arg.strip().lower()
    mapping = {"a": "cart-A", "b": "cart-B", "cart-a": "cart-A", "cart-b": "cart-B"}
    if key not in mapping:
        fail("无效的购物车标识: %s（可用 A / B / cart-A / cart-B）" % cart_arg)
    return mapping[key]


def resolve_actor(space, who):
    for p in space["participants"]:
        if p["userId"] == who or p["nickname"] == who:
            return p
    fail("「%s」不在空间中，无法操作" % who,
         participants=[p["nickname"] for p in space["participants"]])


def owner_nickname(space, cart):
    for p in space["participants"]:
        if p["userId"] == cart["ownerId"]:
            return p["nickname"]
    return "（虚位以待）"


def find_item(cart, code):
    for item in cart["items"]:
        if item["productCode"] == code:
            return item
    return None


def check_editable(space):
    if space["status"] not in EDITABLE_STATUS:
        fail("空间当前状态为 %s，购物车已锁定（结算中或已完成）" % space["status"])


def subtotal(cart):
    total = 0.0
    for item in cart["items"]:
        if item["unitPrice"] is None:
            return None  # 有商品缺单价，需 MCP 试算
        total += item["unitPrice"] * item["quantity"]
    return round(total, 2)


def refresh_total(cart):
    cart["estimatedTotal"] = subtotal(cart)
    cart["updatedAt"] = now_iso()


def cart_view(space, cart_id):
    cart = space["carts"][cart_id]
    return {
        "cartId": cart_id,
        "owner": owner_nickname(space, cart),
        "itemCount": sum(i["quantity"] for i in cart["items"]),
        "estimatedTotal": cart["estimatedTotal"],
        "items": cart["items"],
        "updatedAt": cart["updatedAt"],
    }


def cmd_add(args):
    space = load_space(args.space)
    check_editable(space)
    actor = resolve_actor(space, args.by)
    cart_id = resolve_cart(space, args.cart)
    cart = space["carts"][cart_id]
    if not args.code.strip():
        fail("商品编码不能为空")
    if args.qty < 1:
        fail("数量必须大于等于 1")

    # 商品校验：只能加「购物车主人自己门店菜单」里的商品（各回各家）
    owner = owner_nickname(space, cart)
    menu = space.get("menus", {}).get(cart["ownerId"])
    if not menu:
        fail("%s 的门店菜单尚未加载：先 menu.py set-context 设置门店，再 query-meals + import-menu 导入真实菜单" % owner)
    entry = menu["items"].get(args.code)
    if not entry:
        fail("商品 %s 不在 %s 的门店菜单中（storeCode=%s），禁止加入——请从 query-meals 真实菜单中选择"
             % (args.code, owner, menu.get("storeCode")))

    # 商品信息一律以该门店菜单为准，防止伪造
    name, price, image = entry["name"], entry["price"], entry["image"]

    item = find_item(cart, args.code)
    if item:
        item["quantity"] += args.qty
        item["unitPrice"] = price
        item["updatedAt"] = now_iso()
        action_desc = "add_more"
    else:
        item = {
            "productCode": args.code,
            "productName": name,
            "quantity": args.qty,
            "unitPrice": price,
            "image": image,
            "modification": None,
            "roundList": None,
            "couponId": None,
            "couponCode": None,
            "addedBy": actor["userId"],
            "updatedAt": now_iso(),
        }
        cart["items"].append(item)
        action_desc = "add"

    refresh_total(cart)
    owner = owner_nickname(space, cart)
    log_event(space, actor["userId"], action_desc, cartId=cart_id,
              productCode=args.code, productName=item["productName"], quantity=args.qty)
    save_space(space)
    out({
        "success": True,
        "broadcast": "%s 往 %s 的购物车里加了「%s」×%d" % (actor["nickname"], owner, item["productName"], args.qty),
        "carts": {"cart-A": cart_view(space, "cart-A"), "cart-B": cart_view(space, "cart-B")},
    })


def cmd_remove(args):
    space = load_space(args.space)
    check_editable(space)
    actor = resolve_actor(space, args.by)
    cart_id = resolve_cart(space, args.cart)
    cart = space["carts"][cart_id]
    item = find_item(cart, args.code)
    if not item:
        fail("该购物车里没有商品 %s" % args.code)

    cart["items"] = [i for i in cart["items"] if i["productCode"] != args.code]
    refresh_total(cart)
    owner = owner_nickname(space, cart)
    log_event(space, actor["userId"], "remove", cartId=cart_id,
              productCode=args.code, productName=item["productName"])
    save_space(space)
    out({
        "success": True,
        "broadcast": "%s 从 %s 的购物车里删除了「%s」" % (actor["nickname"], owner, item["productName"]),
        "carts": {"cart-A": cart_view(space, "cart-A"), "cart-B": cart_view(space, "cart-B")},
    })


def cmd_set_qty(args):
    space = load_space(args.space)
    check_editable(space)
    actor = resolve_actor(space, args.by)
    cart_id = resolve_cart(space, args.cart)
    cart = space["carts"][cart_id]
    if args.qty < 0:
        fail("数量不能为负数")
    item = find_item(cart, args.code)
    if not item:
        fail("该购物车里没有商品 %s" % args.code)

    owner = owner_nickname(space, cart)
    if args.qty == 0:
        cart["items"] = [i for i in cart["items"] if i["productCode"] != args.code]
        action_desc, text = "remove", "删除了「%s」" % item["productName"]
    else:
        item["quantity"] = args.qty
        item["updatedAt"] = now_iso()
        action_desc, text = "set_qty", "把「%s」数量改为 ×%d" % (item["productName"], args.qty)

    refresh_total(cart)
    log_event(space, actor["userId"], action_desc, cartId=cart_id,
              productCode=args.code, productName=item["productName"], quantity=args.qty)
    save_space(space)
    out({
        "success": True,
        "broadcast": "%s 在 %s 的购物车里%s" % (actor["nickname"], owner, text),
        "carts": {"cart-A": cart_view(space, "cart-A"), "cart-B": cart_view(space, "cart-B")},
    })


def cmd_clear(args):
    space = load_space(args.space)
    check_editable(space)
    actor = resolve_actor(space, args.by)
    cart_id = resolve_cart(space, args.cart)
    cart = space["carts"][cart_id]
    cart["items"] = []
    refresh_total(cart)
    owner = owner_nickname(space, cart)
    log_event(space, actor["userId"], "clear", cartId=cart_id)
    save_space(space)
    out({
        "success": True,
        "broadcast": "%s 清空了 %s 的购物车" % (actor["nickname"], owner),
        "carts": {"cart-A": cart_view(space, "cart-A"), "cart-B": cart_view(space, "cart-B")},
    })


def cmd_show(args):
    space = load_space(args.space)
    out({
        "success": True,
        "spaceId": space["spaceId"],
        "status": space["status"],
        "participants": [
            {"userId": p["userId"], "nickname": p["nickname"], "role": p["role"], "cartId": p["cartId"]}
            for p in space["participants"]
        ],
        "carts": {"cart-A": cart_view(space, "cart-A"), "cart-B": cart_view(space, "cart-B")},
    })


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - 双人购物车")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="向任一购物车加商品（自动从该车主的门店菜单校验并取真实信息）")
    p.add_argument("--space", required=True)
    p.add_argument("--cart", required=True, help="A / B / cart-A / cart-B")
    p.add_argument("--by", required=True, help="操作者昵称或 userId")
    p.add_argument("--code", required=True, help="productCode，必须存在于车主的门店菜单中")
    p.add_argument("--qty", type=int, default=1)
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="从购物车删除整个商品")
    p.add_argument("--space", required=True)
    p.add_argument("--cart", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--code", required=True)
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("set-qty", help="设置商品数量（0 = 删除）")
    p.add_argument("--space", required=True)
    p.add_argument("--cart", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--qty", type=int, required=True)
    p.set_defaults(func=cmd_set_qty)

    p = sub.add_parser("clear", help="清空购物车")
    p.add_argument("--space", required=True)
    p.add_argument("--cart", required=True)
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_clear)

    p = sub.add_parser("show", help="查看双方购物车")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_show)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
