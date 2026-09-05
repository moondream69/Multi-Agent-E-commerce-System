"""模拟流量场景:买家画像 + 行为模板(确定性文案,不烧 LLM 生成话术)。"""

from __future__ import annotations

# 买家邮箱池(与 seed.py 的 CUSTOMERS 对应;下单时 store 路由按 email 幂等复用/创建买家)
BUYER_EMAILS = [
    "lina@example.com",
    "james.chen@example.com",
    "emily.w@example.com",
    "wangfang@example.com",
    "liuyang@example.com",
    "tanaka@example.jp",
    "sakura@example.jp",
    "michael.s@example.com",
    "sarah.j@example.com",
    "hans.m@example.de",
    "anna.s@example.de",
    "pierre.d@example.fr",
    "marie.l@example.fr",
    "david.lee@example.com",
    "sato@example.jp",
    "huangxm@example.com",
    "zhoujie@example.com",
    "chenming@example.com",
]

# 客服咨询模板(自然语言,经 IntentParser 路由到客服 Agent)
SERVICE_QUESTIONS = [
    "我的订单 #{order_id} 到哪里了?",
    "这款商品还有优惠吗?",
    "怎么查询关税和清关费用?",
    "支持哪些支付方式?",
    "发货需要多久?",
    "能帮我查一下订单 #{order_id} 的物流信息吗?",
    "你们有尺码表吗?",
    "如何申请发票?",
    "这个商品保修多久?",
]

# 售后/投诉模板(负面情绪,触发 sentiment_analysis + escalate_ticket 链)
COMPLAINTS = [
    "我要退货,东西破了!",
    "物流太慢了,我要投诉!",
    "收到的商品和描述不符,要求退款!",
    "等了一个月还没到,太糟糕了!",
]

# 下单后催单(把订单事件链与客服通知跑活)
FOLLOW_UP_QUESTIONS = [
    "我的订单 #{order_id} 为什么还没发货?",
    "订单 #{order_id} 什么时候能到?",
]

# 运营指令(默认关闭,--include-ops 开启;烧 DeepSeek token 较多)
OPS_COMMANDS = [
    "分析一下便携咖啡机在美国市场的选品机会",
    "看看哪些商品库存需要补货",
    "分析一下最近退货率高的品类",
]
