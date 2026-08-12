# 内部订单查询接口(测试环境)

云购商城的订单数据在内部订单系统里,**不在知识库中**。客服机器人查询订单时,调用本接口。

## 服务地址

- 默认:`http://localhost:8123`
- 机器人通过环境变量 `SUPPORT_API_BASE` 读取地址(未设置时用默认值)。

## 接口

`GET /api/orders/<order_id>`

返回 JSON:

```json
{
  "order_id": "YGO-20260801-0001",
  "customer": "张先生",
  "status": "shipped",
  "progress": 3,
  "items": [{"name": "云购智能手表 YunGo Watch S2", "qty": 1}],
  "shipped_at": "2026-08-01 10:00"
}
```

- `status` 取值与中文含义:

| status | 中文 |
|---|---|
| pending | 待支付 |
| paid | 已支付 |
| shipped | 已发货 |
| delivered | 已送达 |
| cancelled | 已取消 |

- 订单不存在时返回 HTTP 404。
- 测试环境无需鉴权。

## 本地启动(调试用)

```bash
python3 /workspace/mock_api/server.py
```

默认监听 8123 端口,读取 `/workspace/mock_api/data/orders.json`。
