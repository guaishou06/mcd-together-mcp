#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
和我一起麦 - Shared Space 状态管理 CLI
负责：空间创建 / 加入 / 查询 / 状态机 / 本地持久化
数据存放在 <workspace>/.workbuddy/space/{spaceId}.json
"""
import argparse
import json
import os
import random
import string
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SPACE_DIR = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "..", "space"))
CN_TZ = timezone(timedelta(hours=8))

STATUS_FLOW = ["waiting", "active", "ordering", "completed"]


def now_iso():
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def space_path(space_id):
    return os.path.join(SPACE_DIR, space_id.upper() + ".json")


def out(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(0 if payload.get("success") else 1)


def fail(message, **extra):
    payload = {"success": False, "error": message}
    payload.update(extra)
    out(payload)


def load_space(space_id):
    path = space_path(space_id)
    if not os.path.exists(path):
        fail("空间不存在: %s" % space_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_space(space):
    os.makedirs(SPACE_DIR, exist_ok=True)
    path = space_path(space["spaceId"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(space, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写入，防止半写状态


def gen_space_id():
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(20):
        code = "MAI-" + "".join(random.choice(alphabet) for _ in range(6))
        if not os.path.exists(space_path(code)):
            return code
    fail("无法生成唯一空间口令，请重试")


def empty_cart(cart_id, owner_id):
    return {
        "cartId": cart_id,
        "ownerId": owner_id,
        "items": [],
        "estimatedTotal": None,
        "updatedAt": now_iso(),
    }


def log_event(space, actor, action, **detail):
    entry = {"at": now_iso(), "actor": actor, "action": action}
    entry.update(detail)
    space["changelog"].append(entry)


def make_participant(user_id, nickname, role, cart_id):
    return {
        "userId": user_id,
        "nickname": nickname,
        "role": role,
        "cartId": cart_id,
        "storeContext": None,  # 结算前由用户选定门店/地址后填充
    }


def cmd_create(args):
    nickname = args.nickname.strip()
    if not nickname:
        fail("昵称不能为空")
    space_id = gen_space_id()
    space = {
        "spaceId": space_id,
        "creatorId": "user-A",
        "status": "waiting",
        "createdAt": now_iso(),
        "participants": [make_participant("user-A", nickname, "creator", "cart-A")],
        "carts": {
            "cart-A": empty_cart("cart-A", "user-A"),
            "cart-B": empty_cart("cart-B", None),
        },
        "orders": {
            "orderA": {"orderId": None, "status": None, "amount": None},
            "orderB": {"orderId": None, "status": None, "amount": None},
        },
        "finalResult": {
            "compatibilityScore": None,
            "commonItems": [],
            "rewardTier": None,
            "rewardCoupon": None,
        },
        "changelog": [],
    }
    log_event(space, "user-A", "create_space", nickname=nickname)
    save_space(space)
    out({
        "success": True,
        "message": "空间已创建，把口令转发给好友即可一起点麦当劳",
        "spaceId": space_id,
        "status": space["status"],
        "inviteHint": "好友对我说「加入空间 %s」即可进入" % space_id,
    })


def cmd_join(args):
    space = load_space(args.space)
    nickname = args.nickname.strip()
    if not nickname:
        fail("昵称不能为空")
    if len(space["participants"]) >= 2:
        fail("空间已满员（2人），无法加入", spaceId=space["spaceId"])
    if space["status"] != "waiting":
        fail("空间当前状态为 %s，无法加入" % space["status"], spaceId=space["spaceId"])
    existing_names = [p["nickname"] for p in space["participants"]]
    if nickname in existing_names:
        fail("昵称「%s」已被占用，请换一个" % nickname)
    guest = make_participant("user-B", nickname, "guest", "cart-B")
    space["participants"].append(guest)
    space["carts"]["cart-B"]["ownerId"] = "user-B"
    space["status"] = "active"
    log_event(space, "user-B", "join_space", nickname=nickname)
    save_space(space)
    out({
        "success": True,
        "message": "%s 已加入空间，双人点餐开始！" % nickname,
        "spaceId": space["spaceId"],
        "status": space["status"],
        "participants": [
            {"userId": p["userId"], "nickname": p["nickname"], "role": p["role"], "cartId": p["cartId"]}
            for p in space["participants"]
        ],
    })


def cmd_show(args):
    space = load_space(args.space)
    out({"success": True, "space": space})


def cmd_list(_args):
    os.makedirs(SPACE_DIR, exist_ok=True)
    spaces = []
    for name in sorted(os.listdir(SPACE_DIR)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(SPACE_DIR, name), "r", encoding="utf-8") as f:
            s = json.load(f)
        spaces.append({
            "spaceId": s["spaceId"],
            "status": s["status"],
            "createdAt": s["createdAt"],
            "participants": [p["nickname"] for p in s["participants"]],
        })
    out({"success": True, "count": len(spaces), "spaces": spaces})


def cmd_set_status(args):
    space = load_space(args.space)
    current = space["status"]
    target = args.status
    if STATUS_FLOW.index(target) < STATUS_FLOW.index(current):
        fail("状态不允许回退: %s -> %s" % (current, target))
    space["status"] = target
    log_event(space, "system", "status_change", **{"from": current, "to": target})
    save_space(space)
    out({"success": True, "spaceId": space["spaceId"], "status": target})


def main():
    parser = argparse.ArgumentParser(description="和我一起麦 - Shared Space 管理")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create", help="创建空间（主账号持有者）")
    p.add_argument("--nickname", required=True, help="创建者昵称")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("join", help="凭口令加入空间")
    p.add_argument("--space", required=True, help="空间口令，如 MAI-XXXXXX")
    p.add_argument("--nickname", required=True, help="加入者昵称")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("show", help="查看空间完整状态")
    p.add_argument("--space", required=True)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("list", help="列出所有空间")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set-status", help="推进空间状态机")
    p.add_argument("--space", required=True)
    p.add_argument("--status", required=True, choices=STATUS_FLOW)
    p.set_defaults(func=cmd_set_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
