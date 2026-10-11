# 麦当劳 MCP 集成说明

## 1. 项目与 MCP 的关系

「和我一起麦」基于麦当劳 MCP Server 构建，将麦当劳官方的门店、菜单、价格、配送、订单等真实业务能力接入双人互动点餐空间。

项目的核心不是简单地让 AI 帮用户点餐，而是让两个异地的人进入同一个互动空间，共同操作两个独立购物车，在互动过程中完成选餐，并最终分别完成各自的独立订单——餐品各回各家。

麦当劳 MCP Server 负责提供真实的麦当劳业务能力（商品、价格、门店、配送、订单），「和我一起麦」负责将这些能力组织成双人互动体验（空间管理、购物车协同、默契值计算、专属优惠）。

两者职责边界清晰：

| 层     | 负责方          | 能力                                       |
| ----- | ------------ | ---------------------------------------- |
| 业务能力层 | 麦当劳 MCP      | 门店查询、菜单拉取、价格试算、配送地址、优惠券、下单、订单状态          |
| 互动产品层 | 「和我一起麦」Skill | 空间创建/加入、双购物车管理、实时同步播报、交叉编辑、默契值计算、专属优惠券生成 |

## 2. 使用的 MCP 能力

项目根据双人点餐的实际流程调用麦当劳 MCP Server 提供的能力。以下是本项目实际使用的 12 个 MCP Tool，以及每个 Tool 在项目中的具体调用场景：

### 门店与配送相关

| MCP Tool                   | 调用场景                                             | 关键参数                                                      |
| -------------------------- | ------------------------------------------------ | --------------------------------------------------------- |
| `query-nearby-stores`      | A/B 各自查找附近到店自取或得来速门店                             | `beType`（1=到店自取/5=得来速）、`searchType=2` 时需 `city`+`keyword` |
| `delivery-query-stores`    | 外送场景：按 addressId 查询可配送门店，获取 `storeCode`+`beCode` | `addressId`、`beType=2`                                    |
| `delivery-query-addresses` | 查询主账号下的配送地址列表（A 和 B 各选各的）                        | 无参                                                        |
| `delivery-create-address`  | 为 B 创建配送地址（录入主账号地址簿，B 无需麦当劳账号）                   | `city`、`contactName`、`phone`、`address`、`addressDetail`    |

### 商品与菜单相关

| MCP Tool            | 调用场景                                       | 关键参数                                                       |
| ------------------- | ------------------------------------------ | ---------------------------------------------------------- |
| `query-meals`       | 为每位成员拉取其门店的真实菜单（分类+商品+价格+图片），作为购物车的唯一合法商品池 | `storeCode`、`orderType`（1到店/2外送）、`beType`；外送/得来速需 `beCode` |
| `query-meal-detail` | 套餐子项/特调选项查询（按需调用）                          | `code`（商品编码）                                               |

### 价格与订单相关

| MCP Tool          | 调用场景                                             | 关键参数                                                                                              |
| ----------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `calculate-price` | 购物车结算试算（**必须调用**，返回价格为分）；到店场景还用于获取 `takeWayCode` | `storeCode`、`orderType`、`beType`、`items[]`（含 `productCode`+`quantity`）；可选 `couponId`+`couponCode` |
| `create-order`    | 为 A 和 B 分别创建独立订单（调用两次，各走各的门店/地址）                 | `storeCode`、`orderType`、`beType`、`items[]`；到店必传 `takeWayCode`，外送必传 `addressId`                    |
| `query-order`     | 支付后查询订单状态                                        | `orderId`                                                                                         |
| `order-list`      | 查询历史订单（兜底验证）                                     | `page`、`pageSize`                                                                                 |
| `cancel-order`    | 取消订单（兜底）                                         | `orderId`                                                                                         |

### 优惠相关

| MCP Tool              | 调用场景                                        | 关键参数                             |
| --------------------- | ------------------------------------------- | -------------------------------- |
| `query-store-coupons` | 查询门店可用优惠券（返回 `couponId`+`couponCode`，下单时传入） | `storeCode`、`orderType`、`beType` |
| `available-coupons`   | 查看可领取的优惠券列表                                 | 无参                               |
| `auto-bind-coupons`   | 一键领取所有可领券                                   | 无参                               |
| `query-my-coupons`    | 查看卡包中已有券                                    | `page`、`pageSize`                |

### 明确不使用的 MCP Tool

`query-meal-assistance`、`query-promotions`（仅团餐）、`mall-*`（积分商城）、`party-order-create`/`query-party-*`（派对）、`draw-lottery`/`query-lottery-*`（抽奖）、`query-survey-coupon`、`campaign-calendar`、`query-my-account`、`list-nutrition-foods`。

## 3. MCP 调用流程

完整流程分为六个阶段，MCP 在其中承担真实数据与业务能力，Skill 负责互动编排：

```
阶段 1：创建互动空间（纯 Skill 层）
  用户 A ──→ space.py create ──→ 生成空间口令 MAI-XXXXXX
                                          │
  用户 B ──→ space.py join ──→───────────┘  2人满员，空间激活

阶段 2：设置门店与菜单（MCP + Skill 联动）
  A 选门店 ──→ MCP query-nearby-stores ──→ menu.py set-context（A 的门店上下文）
                                           ──→ MCP query-meals(storeCode_A) ──→ menu.py import-menu（A 的菜单）

  B 选门店 ──→ MCP query-nearby-stores ──→ menu.py set-context（B 的门店上下文）
                                           ──→ MCP query-meals(storeCode_B) ──→ menu.py import-menu（B 的菜单）

  ★ A 和 B 可以在不同城市、不同门店、不同取餐方式，菜单按人隔离

阶段 3：互动点餐（Skill 层为主，MCP 提供数据校验）
  A 加商品 ──→ cart.py add --cart A --by A --code <productCode>
               └── 校验：商品必须存在于 A 门店的 query-meals 菜单中
  B 帮 A 加 ──→ cart.py add --cart A --by B --code <productCode>
               └── 校验同上，商品仍必须在 A 门店菜单中
  A 删 B 的 ──→ cart.py remove --cart B --by A --code <productCode>
               └── 允许，双方可操作任一购物车
  每次操作 ──→ 返回 broadcast 播报 + changelog 记录

阶段 4：结算准备（Skill 层锁定 + MCP 准备入参）
  双方确认 ──→ checkout.py prepare
               ├── 校验双方购物车非空、门店已设、菜单已导入
               ├── 生成双方 calculate-price 入参（items、storeCode、beType）
               └── 空间状态：active → ordering（购物车锁定）

阶段 5：默契值与专属优惠（纯 Skill 层）
  计算默契值 ──→ score.py compute
                  └── Jaccard(productCode 去重集合) × 100 → 0~100 分
  抽取专属券 ──→ reward.py draw
                  └── 按档位概率抽取「和我一起麦」专属优惠券（1~2.5元/单减免）

阶段 6：独立下单（MCP 核心执行）
  A 的订单：
    MCP calculate-price(storeCode_A, items_A) ──→ checkout.py record-price
    → 获取 takeWayCode（到店）/ 确认 addressId（外送）
    MCP create-order(storeCode_A, takeWayCode_A/addressId_A, items_A)
    → checkout.py record-order ──→ 返回支付链接

  B 的订单（完全独立，同理）：
    MCP calculate-price(storeCode_B, items_B) ──→ checkout.py record-price
    MCP create-order(storeCode_B, takeWayCode_B/addressId_B, items_B)
    → checkout.py record-order ──→ 返回支付链接

  ★ 两单串行创建，互不干扰，各自配送
```

### 关键调用时序

```
锁定(prepare) → 默契值(compute) → 抽专属券(draw) → 试算(calculate-price) → 下单(create-order)
```

默契值和专属券在支付前完成，专属券减免金额由产品侧承担，MCP 的 calculate-price 和 create-order 始终按原价执行。

## 4. 双人独立订单的 MCP 实现细节

这是本项目最关键的技术约束与实现方案。

### 单账号模型约束

麦当劳 MCP Server 是**单账号模型**——所有 Tool 操作的都是当前 MCP 用户本人。地址列表、卡包、订单列表都是单账号维度。MCP 层面没有"第二个用户"的概念。

### 解决方案：单账号双订单

- **A（创建者）**= 麦当劳主账号持有者，对应真实场景的小程序转发发起者
- **B（好友）**&#x51ED;空间口令加入，无需麦当劳账号
- 两个订单都在主账号下创建，分别用各自的 `storeCode` / `addressId`
- B 的收货信息在结算时收集，通过 `delivery-create-address` 录入主账号地址簿

```
互动空间
├── 用户 A（主账号持有者）
│   ├── storeContext: storeCode=1400359, beType=1, takeWayCode=eat-in
│   ├── 购物车 A
│   └── 订单 A → create-order(storeCode_A, takeWayCode_A, items_A)
│
└── 用户 B（好友，无麦当劳账号）
    ├── storeContext: storeCode=1400211, beType=1, takeWayCode=take-in-store
    ├── 购物车 B
    └── 订单 B → create-order(storeCode_B, takeWayCode_B, items_B)
                  （B 的地址通过 delivery-create-address 录入主账号地址簿）
```

### 各回各家的保障机制

菜单按人隔离是核心保障：

```python
# cart.py 的加购校验逻辑
owner = owner_nickname(space, cart)
menu = space.get("menus", {}).get(cart["ownerId"])
entry = menu["items"].get(args.code)
if not entry:
    fail("商品 %s 不在 %s 的门店菜单中（storeCode=%s），禁止加入" % (code, owner, storeCode))
```

- A 的购物车只能加 A 门店菜单里的商品
- B 的购物车只能加 B 门店菜单里的商品
- 即使 B 帮 A 加商品，校验的仍是 A 的门店菜单
- 商品名称、价格、图片一律以该门店 `query-meals` 返回为准

## 5. MCP 调用的关键参数与约束

### 商品唯一标识

点餐场景没有独立 SKU 概念，商品的唯一定位符就是 `productCode`（如 `1440`=麦辣鸡腿汉堡、`9900008754`=经典麦旋风）。下单时以 `productCode` + `quantity` 传入 items 数组。

### 价格单位

- `query-meals` 的 `currentPrice`：**元**（字符串）
- `calculate-price` 所有价格字段：**分**（整数，如 4900 = 49 元）
- 展示时需做分→元换算；结算一律以 `calculate-price` 为准

### takeWayCode 的获取

到店下单时 `create-order` 必须传 `takeWayCode`，**该值只能从 `calculate-price` 返回的 `takeWayList[].code` 中获取**（实测返回 `eat-in` 堂食 / `take-in-store` 外带），不能猜测或硬编码。

### 外送完整链路

```
delivery-query-addresses → 选/建 addressId
  → delivery-query-stores(addressId, beType=2) → 得 storeCode + beCode
    → query-meals(orderType=2, beCode=...) → 得菜单
      → calculate-price → create-order(orderType=2, addressId, beCode)
```

### 优惠券使用方式

在 `calculate-price` / `create-order` 的 items 中传 `couponId` + `couponCode`，来源于 `query-store-coupons` 的返回。

### 两单串行创建

虽然 MCP 没有禁止并发下单，但稳妥起见建议两单串行创建，避免并发歧义。

## 6. MCP 带来的业务价值

通过接入麦当劳 MCP Server，项目将「互动」与「真实点餐」连接起来：

**传统 AI 点餐**：

```
Prompt → 推荐餐品 → 下单
```

**「和我一起麦」**：

```
Prompt → 互动空间 → 双人共同选择 → 购物车互动 → 默契值 → 真实点餐
```

MCP 能力不再只是一个单人点餐接口，而成为双人关系互动的基础设施。MCP 提供的真实商品数据让"默契值"有了客观基础——不是基于虚构商品，而是基于两个人在真实菜单中做出了多少相同的选择。

## 7. 麦麦默契值

### 计算公式

```
SA = 购物车A 的顶层 productCode 去重集合（不看数量、不看特调）
SB = 购物车B 的顶层 productCode 去重集合

麦麦默契值 = round( |SA ∩ SB| / |SA ∪ SB| × 100 )    // 0~100 整数
```

示例：A={麦辣鸡腿堡(1440), 薯条(4810), 可乐(9900008751)}，B={麦辣鸡腿堡(1440), 薯条(4810), 雪碧(3010)}

- 交集 = {1440, 4810}，|交集| = 2
- 并集 = {1440, 4810, 9900008751, 3010}，|并集| = 4
- 默契值 = 2/4 × 100 = **50 分**

### 计算时机

**锁定后、支付前**。正确时序：

```
checkout.py prepare（锁定购物车）
  → score.py compute（计算默契值）
    → reward.py draw（抽取专属优惠券）
      → MCP calculate-price（试算原价）
        → MCP create-order（按原价下单）
```

### 为什么用 productCode

- 以商品编码比对，不以名称（避免同一商品不同叫法导致的误判）
- 特调/套餐子项不影响比对，只看购物车顶层商品
- 数量不影响（A 买 1 份薯条、B 买 3 份薯条，仍算相同商品）
- 所有 productCode 来自 `query-meals` 真实返回，不伪造

### 默契值 → 专属优惠券

默契值不仅用于展示，还决定了本次订单的优惠力度。但优惠券**不是麦当劳现有券**，而是「和我一起麦」产品自有的专属虚拟券：

| 档位   | 默契值范围  | 抽中概率 | 每单减免 | 两单合计 |
| ---- | ------ | ---- | ---- | ---- |
| 无默契  | 0~39   | 0%   | —    | —    |
| 普通默契 | 40~69  | 30%  | ¥1   | ¥2   |
| 高级默契 | 70~89  | 60%  | ¥1.5 | ¥3   |
| 默契拉满 | 90~100 | 90%  | ¥2.5 | ¥5   |

专属优惠券的关键属性：

- 标题明确标注 `和我一起麦专属优惠券（普通默契）`
- **不走 MCP couponId/couponCode 体系**，MCP 的 `calculate-price` 和 `create-order` 始终按原价执行
- 减免金额由「和我一起麦」产品侧承担
- 两笔订单各自独立享受同等减免

### 完整链路

```
真实菜单（MCP query-meals）
  → 双人互动选餐（Skill 购物车协同）
    → 共同点餐完成
      → 购物车商品重合度（productCode 集合 Jaccard 相似度）
        → 麦麦默契值（0~100）
          → 专属优惠券（档位概率抽取，1~2.5元/单减免）
            → 真实下单（MCP create-order）
```

这让一次点餐行为从单纯的交易行为变成了一次可以产生关系反馈的互动体验。

## 8. 脚本与 MCP 的映射关系

项目通过 6 个 Python CLI 脚本编排 MCP 调用，每个脚本负责明确的职责：

| 脚本            | 职责                  | 是否直接调用 MCP   | 依赖的 MCP Tool                     |
| ------------- | ------------------- | ------------ | -------------------------------- |
| `space.py`    | 空间管理（创建/加入/状态机）     | 否            | —                                |
| `cart.py`     | 双人购物车（增删改查/同步播报/互改） | 否            | —                                |
| `menu.py`     | 门店上下文 + 菜单管理        | 否，但导入 MCP 数据 | `query-meals` 的返回 JSON           |
| `checkout.py` | 双独立订单编排             | 否，但生成 MCP 入参 | `calculate-price`、`create-order` |
| `score.py`    | 麦麦默契值计算             | 否            | —                                |
| `reward.py`   | 默契值→专属优惠券           | 否            | —                                |

**MCP 调用由 Skill 编排层（即 Agent 对话）执行**，脚本负责状态管理和业务校验。例如：

1. 用户说"看看有什么吃的" → Agent 调用 MCP `query-meals` → 将返回 JSON 保存为文件 → 调用 `menu.py import-menu --file <路径>` 导入空间
2. 用户说"加个麦辣鸡腿堡" → Agent 调用 `cart.py add --code 1440`（脚本自动从已导入的菜单校验商品合法性并填充名称/价格/图片）
3. 用户说"下单" → Agent 调用 `checkout.py prepare` 获取入参 → 调用 MCP `calculate-price` → 调用 `checkout.py record-price` → 调用 MCP `create-order` → 调用 `checkout.py record-order`

这种设计确保了：脚本不直接依赖 MCP 运行时，MCP 调用与状态管理解耦，便于测试和维护。

## 9. MCP 数据在项目中的流转

```
MCP query-meals 返回
  └─→ 保存为 JSON 文件
      └─→ menu.py import-menu 导入 space.menus[userId]
          └─→ cart.py add 从 menus 中校验商品 + 自动填充名称/价格/图片
              └─→ space.carts.cart-A.items[] 存储 productCode/productName/unitPrice/image
                  └─→ checkout.py prepare 从 carts 构建 calculate-price 入参
                      └─→ MCP calculate-price 返回价格(分) + takeWayList
                          └─→ checkout.py record-price 记录试算结果
                              └─→ MCP create-order 使用 items + takeWayCode/addressId
                                  └─→ checkout.py record-order 记录订单号 + 支付链接

MCP query-nearby-stores / delivery-query-stores 返回
  └─→ menu.py set-context 写入 participant.storeContext
      └─→ checkout.py prepare 读取 storeContext 构建下单入参
          └─→ MCP calculate-price / create-order 使用 storeCode + beType + beCode
```

所有 MCP 数据的写入和读取都经过脚本校验，不存在裸调 MCP 跳过校验的路径。

## 10. 已知限制与后续演进

| 限制        | 当前处理                             | 后续方向                  |
| --------- | -------------------------------- | --------------------- |
| MCP 单账号模型 | 单账号双订单，B 无需麦当劳账号                 | 双账号各自 MCP 环境独立下单      |
| 购物车实时同步   | 基于操作后返回 broadcast 文案 + changelog | WebSocket 双端实时推送      |
| 状态持久化     | 本地 JSON 文件（原子写入）                 | 云数据库 + 多端同步           |
| 专属优惠券     | 产品侧虚拟券，MCP 按原价下单                 | 对接真实补贴通道或 MCP 优惠券体系   |
| 外送双地址     | B 的地址录入主账号地址簿                    | B 侧自有 MCP 会话直接使用自己的地址 |
