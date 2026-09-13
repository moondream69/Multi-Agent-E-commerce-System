"""合成试运行数据生成器(确定性,stdlib only)。

生成三份 CSV 到 docs/demo-data/(不覆盖演示集 products.csv),列契约对齐
core/imports.py 的行级校验:商品 sku/title/price/category 必填、订单 sku/total_amount
必填、买家 name/email 必填。规模 = 500 商品 / 200 买家 / 2000 订单(seed 固定,可重生成)。

用法(在 python-backend/ 下):
    uv run python scripts/gen_synth_data.py            # 默认规模,写 docs/demo-data/
    uv run python scripts/gen_synth_data.py --out <dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

SEED = 20260913
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "docs" / "demo-data"

# 类目:代码 → (中文名, 商品名词池, 描述短语池, 价格区间 CNY 量级对齐的美元定价)
CATEGORIES = {
    "HM": (
        "家居厨房",
        [
            "便携咖啡机",
            "手冲咖啡壶套装",
            "硅胶铲勺五件套",
            "折叠收纳箱",
            "竹木砧板",
            "真空保温杯",
            "香薰蜡烛礼盒",
            "双层沥水篮",
            "电子厨房秤",
            "真空保鲜盒",
            "陶瓷磨刀器",
            "隔热硅胶手套",
            "桌面收纳架",
            "玻璃调味瓶组",
            "可折叠沥水架",
        ],
        ["一键萃取 易清洗", "食品级材质 安心使用", "小户型友好 省空间", "耐用耐高温 日常必备"],
        (8.9, 79.9),
    ),
    "CE": (
        "消费电子",
        [
            "无线蓝牙耳机",
            "磁吸充电宝",
            "USB-C 快充头",
            "便携蓝牙音箱",
            "智能手环",
            "桌面手机支架",
            "无线静音鼠标",
            "机械键盘",
            "网络摄像头",
            "车载充电器",
            "HDMI 分线器",
            "无线充电板",
            "主动降噪耳机",
            "便携显示器",
            "智能插座",
        ],
        ["低延迟 长续航", "快充协议全兼容", "轻巧便携 随行随用", "即插即用 免驱动"],
        (15.9, 299.9),
    ),
    "OS": (
        "户外运动",
        [
            "折叠露营椅",
            "超轻睡袋",
            "防水登山包",
            "便携气炉",
            "户外保温壶",
            "感应头灯",
            "碳纤维登山杖",
            "防潮野餐垫",
            "速干毛巾",
            "骑行手套",
            "高倍望远镜",
            "折叠水桶",
            "露营灯串",
            "运动腰包",
            "防滑溯溪鞋",
        ],
        ["轻量化设计 背负重", "防风防泼水 全天候", "一秒收纳 说走就走", "耐磨抗撕裂 经久耐用"],
        (12.9, 159.9),
    ),
    "PT": (
        "宠物用品",
        [
            "自动喂食器",
            "宠物饮水机",
            "瓦楞猫抓板",
            "狗狗牵引绳",
            "宠物航空箱",
            "四季通用猫窝",
            "宠物毛发清理器",
            "慢食防噎碗",
            "宠物外出背包",
            "逗猫棒套装",
            "宠物指甲剪",
            "除臭猫砂盆",
            "宠物雨衣",
            "磨牙洁齿玩具",
            "宠物凉席",
        ],
        ["静音运行 不扰眠", "易拆洗 好打理", "环保材质 啃咬无忧", "贴合身形 佩戴舒适"],
        (5.9, 59.9),
    ),
    "BT": (
        "美妆个护",
        [
            "声波电动牙刷",
            "家用美容仪",
            "化妆刷套装",
            "硅胶洁面仪",
            "便携卷发棒",
            "指甲护理套装",
            "精油香薰机",
            "男士电动剃须刀",
            "面膜加热仪",
            "LED 化妆镜",
            "头皮按摩梳",
            "冷喷补水仪",
            "修眉套装",
            "旅行分装瓶组",
            "恒温足浴盆",
        ],
        ["温和不刺激 敏感肌友好", "小巧便携 旅行装", "多档调节 随心切换", "深层清洁 焕亮肌肤"],
        (6.9, 49.9),
    ),
    "AP": (
        "服饰配件",
        [
            "帆布托特包",
            "针织围巾",
            "防晒冰袖",
            "真皮卡包",
            "渔夫帽",
            "运动腰包",
            "美丽诺羊毛袜",
            "真丝方巾",
            "偏光太阳镜",
            "头层牛皮皮带",
            "刺绣棒球帽",
            "触屏保暖手套",
            "防泼水双肩包",
            "创意钥匙扣",
            "全自动折叠伞",
        ],
        ["百搭基础款 四季可用", "亲肤透气 不闷汗", "防泼水 小雨无忧", "轻盈不压身"],
        (9.9, 69.9),
    ),
    "KB": (
        "母婴玩具",
        [
            "婴儿安抚巾",
            "硅胶围兜",
            "大颗粒儿童积木",
            "软底学步鞋",
            "可折叠婴儿浴盆",
            "双语早教机",
            "婴儿背带",
            "重力球学饮杯",
            "木质拼图套装",
            "儿童雨衣",
            "婴儿指甲护理套装",
            "手抓摇铃玩具",
            "儿童餐椅坐垫",
            "绘本收纳架",
            "三轮儿童滑板车",
        ],
        ["母婴级材质 啃咬安全", "圆角设计 防磕碰", "可拆洗 常保洁净", "适龄设计 助力成长"],
        (7.9, 89.9),
    ),
    "CA": (
        "汽车配件",
        [
            "车载吸尘器",
            "重力车载支架",
            "座椅缝隙收纳盒",
            "车载香薰",
            "应急启动电源",
            "数显胎压计",
            "前挡遮阳挡",
            "后备箱收纳箱",
            "车载冷暖箱",
            "全包围脚垫",
            "真皮方向盘套",
            "车载逆变器",
            "洗车工具套装",
            "无骨雨刮器",
            "车充扩展坞",
        ],
        ["专车专用 贴合原车", "车规级用料 耐高低温", "无损安装 即装即用", "大功率 稳定输出"],
        (9.9, 129.9),
    ),
    "OF": (
        "办公文具",
        [
            "桌面收纳盒",
            "无钉订书机",
            "速干中性笔套装",
            "亚克力文件架",
            "电动橡皮擦",
            "便携标签打印机",
            "皮面笔记本套装",
            "人体工学腕托",
            "护眼台灯",
            "白板贴纸",
            "磁吸便签盒",
            "曲别针收纳器",
            "太阳能计算器",
            "手账印章套装",
            "面单打印机",
        ],
        ["桌面清爽 效率翻倍", "静音设计 不扰同事", "大容量 够用一学期", "顺滑书写 不断墨"],
        (3.9, 59.9),
    ),
    "TL": (
        "工具五金",
        [
            "家用工具套装",
            "锂电电动螺丝刀",
            "数显测电笔",
            "自锁卷尺",
            "棘轮扳手",
            "强光手电筒",
            "迷你水平仪",
            "热熔胶枪",
            "重型美工刀",
            "三件套钳子",
            "螺丝分类收纳盒",
            "加厚折叠梯",
            "砂纸打磨机",
            "电烙铁套装",
            "多层工具箱",
        ],
        ["家用维修 一套搞定", "防滑手柄 安全省力", "高硬度钢 耐磨损", "收纳有序 随取随用"],
        (7.9, 149.9),
    ),
}

COLORS = ["雾灰", "奶油白", "深空黑", "薄荷绿", "原木色", "珍珠白", "海军蓝", "焦糖棕"]
PLATFORMS = ["amazon", "temu", "shopee", "tiktok", "ebay", "aliexpress"]
CURRENCIES = ["USD", "USD", "USD", "USD", "USD", "EUR", "EUR", "GBP", "CNY"]  # 美元主导
LOCALES = ["zh-CN"] * 11 + ["en-US"] * 3 + ["ja-JP"] * 2 + ["de-DE"] * 2 + ["fr-FR", "es-ES", "en-GB"]
SURNAMES = [
    "王",
    "李",
    "张",
    "刘",
    "陈",
    "杨",
    "赵",
    "黄",
    "周",
    "吴",
    "徐",
    "孙",
    "胡",
    "朱",
    "高",
    "林",
    "何",
    "郭",
    "马",
    "罗",
    "梁",
    "宋",
    "郑",
    "谢",
    "韩",
    "唐",
    "冯",
    "于",
    "董",
    "萧",
]
GIVEN = [
    "伟",
    "芳",
    "娜",
    "敏",
    "静",
    "磊",
    "洋",
    "强",
    "倩",
    "勇",
    "军",
    "杰",
    "涛",
    "明",
    "超",
    "秀兰",
    "晓峰",
    "雨欣",
    "子涵",
    "宇航",
    "若曦",
    "嘉豪",
    "思远",
    "梦琪",
    "浩然",
    "诗雨",
    "俊杰",
    "紫萱",
    "天佑",
    "欣然",
    "志强",
    "淑华",
    "建国",
    "丽娟",
    "文博",
    "欣怡",
    "子墨",
]
TAGS = ["家居", "数码", "户外", "宠物", "美妆", "服饰", "母婴", "汽车", "办公", "工具"]

PRODUCT_COUNT = 500
CUSTOMER_COUNT = 200
ORDER_COUNT = 2000


def _money(rng: random.Random, low: float, high: float) -> str:
    """两位小数定价,尾数偏 .90/.99/.50,形似真实挂牌价。"""
    whole = rng.randint(int(low), int(high))
    cents = rng.choice([90, 99, 50, 90, 99])
    return f"{whole}.{cents:02d}"


def _stock_for(rng: random.Random, threshold: int) -> int:
    """按五档文案判据(stock/threshold)反推库存:<0.3 严重不足、<0.6 偏低、<1 接近安全线。"""
    tier = rng.choices(
        ["out", "critical", "low", "near", "healthy"],
        weights=[8, 10, 12, 15, 55],
    )[0]
    if tier == "out":
        return 0
    if tier == "critical":
        return max(1, int(threshold * rng.uniform(0.1, 0.29)))
    if tier == "low":
        return max(1, int(threshold * rng.uniform(0.3, 0.59)))
    if tier == "near":
        return max(1, int(threshold * rng.uniform(0.6, 0.99)))
    return int(threshold * rng.uniform(1.2, 20))


def gen_products(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    used_titles: set[str] = set()
    codes = list(CATEGORIES)
    for index in range(1, PRODUCT_COUNT + 1):
        code = codes[(index - 1) % len(codes)]  # 类目轮转,分布均匀
        category, nouns, blurbs, (low, high) = CATEGORIES[code]
        noun = rng.choice(nouns)
        title = f"{noun} {rng.choice(COLORS)}款" if rng.random() < 0.6 else noun
        suffix = 1
        while title in used_titles:  # 同名消歧:追加色号/序号,确定性
            suffix += 1
            title = f"{noun} {rng.choice(COLORS)}款 {suffix}"
        used_titles.add(title)
        threshold = rng.choices([10, 5, 20], weights=[85, 10, 5])[0]
        rows.append(
            {
                "sku": f"SYN-{code}-{index:03d}",
                "title": title,
                "price": _money(rng, low, high),
                "category": category,
                "currency": rng.choice(CURRENCIES),
                "platform": rng.choice(PLATFORMS),
                "stock": str(_stock_for(rng, threshold)),
                "alert_threshold": str(threshold),
                "description": rng.choice(blurbs),
            }
        )
    return rows


def gen_customers(rng: random.Random) -> list[dict]:
    rows: list[dict] = []
    for index in range(1, CUSTOMER_COUNT + 1):
        locale = rng.choice(LOCALES)
        name = f"{rng.choice(SURNAMES)}{rng.choice(GIVEN)}"
        if locale.startswith("en"):
            name = f"{name} (Synth {index:03d})"  # 英文买家保留拼音名形态
        tags = rng.sample(TAGS, k=rng.randint(1, 2))
        rows.append(
            {
                "name": name,
                "email": f"synth-cust-{index:03d}@example.com",
                "locale": locale,
                "preferences": json.dumps({"language": locale, "tags": tags}, ensure_ascii=False),
            }
        )
    return rows


def gen_orders(rng: random.Random, products: list[dict], customers: list[dict]) -> list[dict]:
    # 幂律热度:前 20% 商品承接约一半订单,便于演报表/低库存聚合的头部效应
    weights = [1.0 / (rank + 1) ** 0.8 for rank in range(len(products))]
    statuses = ["delivered", "shipped", "processing", "confirmed", "pending", "cancelled", "returned"]
    status_weights = [500, 100, 80, 70, 100, 100, 50]
    rows: list[dict] = []
    for index in range(1, ORDER_COUNT + 1):
        product = rng.choices(products, weights=weights)[0]
        qty = rng.choices([1, 2, 3], weights=[80, 15, 5])[0]
        total = f"{float(product['price']) * qty:.2f}"
        rows.append(
            {
                "sku": product["sku"],
                "total_amount": total,
                "currency": product["currency"] if rng.random() < 0.85 else rng.choice(CURRENCIES),
                "status": rng.choices(statuses, weights=status_weights)[0],
                "customer_email": (rng.choice(customers)["email"] if rng.random() < 0.92 else ""),
                "reference": f"SYN-ORD-{index:05d}",
                "platform": rng.choice(PLATFORMS),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="合成试运行数据生成器(确定性)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录(默认 docs/demo-data/)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(SEED)
    products = gen_products(rng)
    customers = gen_customers(rng)
    orders = gen_orders(rng, products, customers)

    for name, rows in [
        ("synth-products.csv", products),
        ("synth-customers.csv", customers),
        ("synth-orders.csv", orders),
    ]:
        write_csv(args.out / name, rows)
        print(f"{name}: {len(rows)} 行")

    # 确定性自检:幂等键唯一(csv 导入按此判重)
    assert len({p["sku"] for p in products}) == len(products), "SKU 重复"
    assert len({c["email"] for c in customers}) == len(customers), "邮箱重复"
    assert len({o["reference"] for o in orders}) == len(orders), "reference 重复"
    skus = {p["sku"] for p in products}
    assert all(o["sku"] in skus for o in orders), "订单引用了不存在的 SKU"

    tiers = {"out": 0, "critical": 0, "low": 0, "near": 0, "healthy": 0}
    for product in products:
        ratio = int(product["stock"]) / int(product["alert_threshold"])
        if ratio <= 0:
            tier = "out"
        elif ratio < 0.3:
            tier = "critical"
        elif ratio < 0.6:
            tier = "low"
        elif ratio < 1:
            tier = "near"
        else:
            tier = "healthy"
        tiers[tier] += 1
    print(f"库存五档分布: {tiers}")
    status_counts: dict[str, int] = {}
    for order in orders:
        status_counts[order["status"]] = status_counts.get(order["status"], 0) + 1
    print(f"订单状态分布: {status_counts}")


if __name__ == "__main__":
    main()
