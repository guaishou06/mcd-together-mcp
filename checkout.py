#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - 双独立订单编排 CLI（Step 9-10）
负责：结算前校验 / 生成双方 calculate-price 入参 / 记录试算结果 / 选定取餐方式 /
      绑定配送地址 / 记录独立订单 / 订单状态跟踪

铁律：
- 两个购物车 → 两个独立订单 → 两个独立门店/地址，绝不合并
- 每单只使用「该成员自己的 storeContext 与 address」
- 本脚本不下单，仅编排与记录；真实 create-order 由 Agent 经 mcd-mcp 调用
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from space import load_space, save_space, log_event, now_iso, out, fail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ORDER_KEY = {"user-A": "orderA", "user-B": "orderB"}
WHO_FROM_ORDER = {"orderA": "user-A", "orderB": "user-B"}


def find_participant(space, who):
    for p in space["participants"]:
        if p["userId"] == who or p["nickname"] == who:
            return p
    fail("「%s」不在空间中" % who)


def build_items(cart):
    items = []
    for it in cart["items"]:
        entry = {"productCode": it["productCode"], "quantity": it["quantity"]}
        if it.get("modification"):
            entry["modification"] = it["modification"]
        if it.get("roundList"):
            entry["roundList"] = it["roundList"]
        if it.get("couponId"):
            entry["couponId"] = it["couponId"]
        if it.get("couponCode"):
            entry["couponCode"] = it["couponCode"]
        items.append(entry)
    return items


def validate_member(space, p):
    """校验单个成员的结算条件，返回 items payload"""
    cart = space["carts"][p["cartId"]]
    if not cart["items"]:
        fail("%s 的购物车是空的，无法结算" % p["nickname"])
    ctx = p.get("storeContext")
    if not ctx:
        fail("%s 尚未设置门店上下文（menu.py set-context）" % p["nickname"])
    menu = space.get("menus", {}).get(p["userId"])
    if not menu:
        fail("%s 的门店菜单尚未加载（menu.py import-menu）" % p["nickname"])
    if menu["storeCode"] != ctx["storeCode"]:
        fail("%s 的菜单与当前门店不一致，请重新 query-meals + import-menu" % p["nickname"])
    for it in cart["items"]:
        if it["productCode"] not in menu["items"]:
            fail("商品 %s(%s) 已不在 %s 的门店菜单中，请删除后重选"
                 % (it["productCode"], it["productName"], p["nickname"]))
    # 外送场景必须有配送地址（各回各家：用各自的地址）
    if ctx["orderType"] == 2:
        addr = (p.get("address") or {}).get("addressId") or ctx.get("addressId")
        if not addr:
            fail("%s 是外送订单但没有配送地址，请先 attach-address" % p["nickname"])
    return build_items(cart)


def cmd_prepare(args):
    space = load_space(args.space)
    if space["status"] == "waiting":
        fail("空间只有 1 人，等好友加入后再结算")
    if space["status"] == "completed":
        fail("空间已完成，无法重新结算")
    if len(space["participants"]) < 2:
        fail("需要 2 位成员才能结算")

    tasks = []
    checkout = space.setdefault("checkout", {})
    for p in space["participants"]:
        items = validate_member(space, p)
        ctx = p["storeContext"]
        entry = checkout.setdefault(p["userId"], {})
        entry["items"] = items
        entry["storeContext"] = dict(ctx)
        entry["preparedAt"] = now_iso()
        call = {
            "storeCode": ctx["storeCode"],
            "orderType": ctx["orderType"],
            "beType": ctx["beType"],
        }
        if ctx.get("beCode"):
            call["beCode"] = ctx["beCode"]
        call["items"] = items
        tasks.append({
            "谁": p["nickname"],
            "userId": p["userId"],
            "店铺": ctx.get("storeName") or ctx["storeCode"],
            "取餐方式": {1: "到店自取", 2: "麦乐送", 5: "得来速"}.get(ctx["beType"], str(ctx["beType"])),
            "配送地址": (p.get("address") or {}).get("fullAddress") or ("到店（无地址）" if ctx["orderType"] == 1 else ctx["addressId"]),
            "calculate_price入参": call,
            "后续": [
                "调用 score.py compute --space %s 计算默契值" % space["spaceId"],
                "调用 reward.py draw --space %s 抽取「和我一起麦」专属优惠券" % space["spaceId"],
                "调用 mcd-mcp calculate-price（上面入参）→ 把原始返回存 JSON",
                "checkout.py record-price --space %s --who %s --file <试算JSON>" % (space["spaceId"], p["nickname"]),
                "（到店/得来速）checkout.py select-takeway --space %s --who %s --code <takeWayList中的code>" % (space["spaceId"], p["nickname"]),
                "调用 create-order（到店传 takeWayCode / 外送传 addressId，items 同上）",
                "checkout.py record-order --space %s --who %s --order-id <订单号> --amount <元>" % (space["spaceId"], p["nickname"]),
            ],
        })

    if space["status"] == "active":
        space["status"] = "ordering"
        log_event(space, "system", "status_change", **{"from": "active", "to": "ordering"})
    log_event(space, "system", "checkout_prepared",
              participants=[p["nickname"] for p in space["participants"]])
    save_space(space)
    out({
        "success": True,
        "message": "结算已就绪：两个购物车锁定。下一步：计算默契值 → 抽专属券 → 下单",
        "spaceId": space["spaceId"],
        "status": space["status"],
        "tasks": tasks,
    })


def cmd_record_price(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    with open(args.file, "r", encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("data") or {}
    if "price" not in data:
        fail("文件不是合法的 calculate-price 返回（缺少 data.price）")

    entry = space.setdefault("checkout", {}).setdefault(p["userId"], {})
    entry["price"] = {
        "price": data.get("price"),                      # 分
        "originalPrice": data.get("originalPrice"),
        "discount": data.get("discount"),
        "productPrice": data.get("productPrice"),
        "deliveryPrice": data.get("deliveryPrice"),
        "packingPrice": data.get("packingPrice"),
        "productList": data.get("productList"),
        "enjoyed": data.get("enjoyed"),
        "enjoyable": data.get("enjoyable"),
    }
    takeway = data.get("takeWayList") or []
    entry["takeWayOptions"] = takeway
    if takeway and not entry.get("selectedTakeWayCode"):
        entry["selectedTakeWayCode"] = takeway[0]["code"]  # 默认第一项，可由 select-takeway 覆盖
    log_event(space, p["userId"], "record_price", price=data.get("price"))
    save_space(space)
    out({
        "success": True,
        "谁": p["nickname"],
        "现价": round((data.get("price") or 0) / 100, 2),
        "原价": round((data.get("originalPrice") or 0) / 100, 2),
        "优惠": round((data.get("discount") or 0) / 100, 2),
        "已选方式": entry.get("selectedTakeWayCode"),
        "可用取餐方式": takeway,
        "productList": data.get("productList"),
    })


def cmd_select_takeway(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    entry = space.setdefault("checkout", {}).setdefault(p["userId"], {})
    options = [o["code"] for o in entry.get("takeWayOptions", [])]
    if options and args.code not in options:
        fail("取餐方式 %s 不在 calculate-price 返回的 takeWayList 中（可选：%s）" % (args.code, options))
    entry["selectedTakeWayCode"] = args.code
    log_event(space, p["userId"], "select_takeway", takeWayCode=args.code)
    save_space(space)
    out({"success": True, "谁": p["nickname"], "selectedTakeWayCode": args.code})


def cmd_attach_address(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    p["address"] = {
        "addressId": args.address_id,
        "contactName": args.contact_name,
        "phone": args.phone,
        "fullAddress": args.full_address,
    }
    if p.get("storeContext"):
        p["storeContext"]["addressId"] = args.address_id
    log_event(space, p["userId"], "attach_address", addressId=args.address_id)
    save_space(space)
    out({
        "success": True,
        "message": "%s 的配送地址已绑定：%s" % (p["nickname"], args.full_address or args.address_id),
        "谁": p["nickname"],
        "address": p["address"],
    })


def cmd_record_order(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    entry = space.setdefault("checkout", {}).setdefault(p["userId"], {})
    if not entry.get("items"):
        fail("请先 prepare 再记录订单")

    order_id = args.order_id
    pay_url = args.pay_url
    amount = args.amount
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            raw = json.load(f)
        d = raw.get("data") or raw
        order_id = order_id or d.get("orderId") or d.get("orderNo") or (d.get("order") or {}).get("orderId")
        pay_url = pay_url or d.get("payUrl") or d.get("payLink") or (d.get("order") or {}).get("payUrl")
        if amount is None:
            amt = d.get("price") or d.get("realTotalAmount") or d.get("totalAmount")
            if amt is not None:
                amount = (amt / 100) if isinstance(amt, (int, float)) and amt > 1000 else amt
    if not order_id:
        fail("缺少订单号（--order-id 或 --file 中的 orderId）")

    key = ORDER_KEY[p["userId"]]
    space["orders"][key] = {
        "orderId": order_id,
        "status": "已下单待支付",
        "amount": amount,
        "payUrl": pay_url,
        "storeCode": p["storeContext"]["storeCode"],
        "storeName": p["storeContext"].get("storeName"),
        "orderType": p["storeContext"]["orderType"],
        "addressId": (p.get("address") or {}).get("addressId") or p["storeContext"].get("addressId"),
        "createdAt": now_iso(),
    }
    log_event(space, p["userId"], "create_order", orderId=order_id, amount=amount)
    save_space(space)
    out({
        "success": True,
        "message": "%s 的独立订单已记录" % p["nickname"],
        "谁": p["nickname"],
        "订单": space["orders"][key],
    })


def cmd_record_order_status(args):
    space = load_space(args.space)
    p = find_participant(space, args.who)
    key = ORDER_KEY[p["userId"]]
    if not space["orders"][key].get("orderId"):
        fail("%s 还没有订单记录" % p["nickname"])
    space["orders"][key]["status"] = args.order_status
    space["orders"][key]["statusUpdatedAt"] = now_iso()
    log_event(space, p["userId"], "order_status", orderStatus=args.order_status)
    save_space(space)
    paid = [k for k in ("orderA", "orderB") if space["orders"][k].get("status") in ("订单已完成", "已完成", "已支付", "制作中", "配送中")]
    out({
        "success": True,
        "谁": p["nickname"],
        "订单状态": args.order_status,
        "双方都已推进": len(paid) == 2,
        "提示": "双方订单都已支付/完成时，可执行 space.py set-status --status completed，再 score.py compute 出默契值" if len(paid) == 2 else "继续等待另一位成员的订单状态",
    })


def cmd_summary(args):
    space = load_space(args.space)
    rows = []
    for p in space["participants"]:
        cart = space["carts"][p["cartId"]]
        key = ORDER_KEY[p["userId"]]
        ctx = p.get("storeContext") or {}
        rows.append({
            "谁": p["nickname"],
            "购物车": "%d 件 / 小计 %s 元" % (
                sum(i["quantity"] for i in cart["items"]),
                cart["estimatedTotal"] if cart["estimatedTotal"] is not None else "待试算"),
            "门店": "%s (%s)" % (ctx.get("storeName") or "-", ctx.get("storeCode") or "-"),
            "取餐方式": {1: "到店自取", 2: "麦乐送", 5: "得来速"}.get(ctx.get("beType"), "-"),
            "送达": (p.get("address") or {}).get("fullAddress") or ("到店自取" if ctx.get("orderType") == 1 else "未绑定地址"),
            "订单号": space["orders"][key].get("orderId") or "未下单",
            "订单状态": space["orders"][key].get("status") or "-",
            "实付": space["orders"][key].get("amount"),
        })
    out({
        "success": True,
        "spaceId": space["spaceId"],
        "status": space["status"],
        "finalResult": space["finalResult"],
        "订单总览": rows,
    })


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - 双独立订单编排")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("prepare", help="结算前校验 + 生成双方下单任务（锁定购物车）")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("record-price", help="记录 calculate-price 真实试算结果")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_record_price)

    p = sub.add_parser("select-takeway", help="选定取餐方式（到店/得来速必选）")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--code", required=True)
    p.set_defaults(func=cmd_select_takeway)

    p = sub.add_parser("attach-address", help="绑定该成员的配送地址（各回各家）")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--address-id", required=True)
    p.add_argument("--contact-name", default=None)
    p.add_argument("--phone", default=None)
    p.add_argument("--full-address", default=None)
    p.set_defaults(func=cmd_attach_address)

    p = sub.add_parser("record-order", help="记录该成员的独立订单")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--order-id", default=None)
    p.add_argument("--amount", type=float, default=None, help="实付金额（元）")
    p.add_argument("--pay-url", default=None)
    p.add_argument("--file", default=None, help="create-order 原始返回 JSON（可选）")
    p.set_defaults(func=cmd_record_order)

    p = sub.add_parser("record-order-status", help="记录订单最新状态（query-order 结果）")
    p.add_argument("--space", required=True)
    p.add_argument("--who", required=True)
    p.add_argument("--order-status", required=True)
    p.set_defaults(func=cmd_record_order_status)

    p = sub.add_parser("summary", help="双方订单总览")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
