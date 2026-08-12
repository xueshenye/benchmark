# 云服客服工单系统 — HTTP 接口约定(技术团队提供)

客服工单系统是一个内部 HTTP 服务。**请按本约定实现**:字段名、路径、状态码必须一致。

## 服务地址与环境变量

- 端口:环境变量 `PORT`,默认 `8123`。
- 数据文件:环境变量 `TICKET_DB`,默认 `/workspace/data/tickets.db`。**工单数据必须持久化到这个文件**,服务重启后数据不丢。
- 监听地址用 `127.0.0.1` 即可(测试环境无需外网)。

## 健康检查

`GET /api/health` → `200 {"status": "ok"}`

## 工单对象

```json
{
  "id": 1,
  "title": "登录页打不开",
  "description": "用户反馈登录时页面白屏",
  "reporter": "王小明",
  "status": "open",
  "priority": "high",
  "assignee": "李工",
  "created_at": "2026-08-11T10:00:00",
  "resolved_at": null,
  "overdue": false
}
```

- `id`(整数):唯一,创建时自动分配,从 1 递增。
- `status` 取值:`open`(待处理)/ `in_progress`(处理中)/ `resolved`(已解决)/ `closed`(已关闭)。
- `priority` 取值:`high`(高)/ `medium`(中)/ `low`(低);创建时不传默认 `medium`。
- `created_at` / `resolved_at`:ISO-8601 字符串;未解决时 `resolved_at` 为 `null`。
- `overdue`(布尔):是否超时,业务规则另行约定。

## 接口

### 创建工单
`POST /api/tickets`
请求体 JSON(字段均可选,`title` 除外):
```json
{"title": "登录页打不开", "description": "用户反馈登录时页面白屏", "reporter": "王小明",
 "priority": "high", "assignee": "李工", "created_at": "2026-08-01T09:00:00"}
```
- `title` 必填且非空;为空或缺失 → `400`。
- `priority` 默认 `medium`;`created_at` 可选(用于回填历史数据,缺省为当前时间)。
- 返回 `201` + 完整工单对象(含分配的 `id`)。

### 工单列表
`GET /api/tickets`
- 返回 **JSON 数组**(按 `id` 升序)。
- 查询参数(全部可选,可组合):
  - `q`:`title`/`description` 的**大小写不敏感子串匹配**。
  - `status`:`open|in_progress|resolved|closed` 精确匹配。
  - `priority`:`high|medium|low` 精确匹配。
  - `assignee`:指派人工号/姓名精确匹配。
- 无参数时返回全部工单。

### 工单详情
`GET /api/tickets/<id>` → `200` + 工单对象;不存在 → `404`。

### 更新工单
`PATCH /api/tickets/<id>` → `200` + 更新后的工单对象;不存在 → `404`。
- 请求体为部分字段:`status` / `priority` / `assignee` / `description` / `reporter` 任一或组合。
- `status` 状态流转(**必须逐级,不能跳级**):
  - `open → in_progress`
  - `in_progress → open` / `in_progress → resolved`
  - `resolved → closed` / `resolved → in_progress`(重开)
  - `closed → open`(重开)
  - 其他变化(如 `open → resolved`、`open → closed`、`closed → resolved`)或非法取值 → `400`。
- 进入 `resolved` 时,若 `resolved_at` 为空则设为当前时间;重开(离开 `resolved`/`closed`)时 `resolved_at` 置为 `null`。

### 删除工单
`DELETE /api/tickets/<id>` → `200`;不存在 → `404`。
- **删除后工单即被移除**,不再出现在 `GET /api/tickets` 列表中;对已删除工单的 `GET /api/tickets/<id>` 返回 `404`。

### 统计
`GET /api/tickets/stats` → `200` + JSON:
```json
{
  "by_status": {"open": 2, "in_progress": 1, "resolved": 0, "closed": 1},
  "by_priority": {"high": 1, "medium": 2, "low": 1},
  "avg_resolution_hours": 25.5,
  "overdue_count": 1
}
```
- `by_status` / `by_priority`:各状态/优先级的工单数(计数为 0 的键也要存在)。
- `avg_resolution_hours`:所有已解决工单从 `created_at` 到 `resolved_at` 的平均小时数;没有已解决工单时为 `null`。
- `overdue_count`:当前超时工单数。

### 首页页面
`GET /` → `200` 返回 HTML 页面:包含应用名称**云服客服**和工单列表区域。
