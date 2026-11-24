# 阶段接口

`POST /api/stages/<stage>` 接收 `multipart/form-data`。字段 `sample=dirty`、`sample=clean` 或 `file=<CSV>` 三选一。阶段为 `intake`、`prepare`、`calculate`、`policy`、`balance`。预览不入库。

`POST /api/runs` 接收同样的输入，执行全批并返回运行报告。`GET /api/runs` 返回最近 50 批。`GET /api/runs/<id>` 查询一批和当前复核状态。

`PATCH /api/runs/<id>` 接收 `revision`、`status` 和 `note`。状态使用 `APPROVED` 或 `HELD`。冲突返回 409，客户端读取新版本后重试。

`GET /api/runs/<id>/evidence` 下载内含运行清单、行号隔离、业务证据和复核信息的 JSON。

## 像元结算账（JSON 接口）

结算输入为 JSON：`region`（`x0/y0/x1/y1`）与 `tiles`（每张瓦片含 `id`、`supplier`、`grade` 非负整数、`x0/y0/x1/y1`，瓦片必须落在区域内）。

| 方法与路径 | 作用 |
|---|---|
| `GET /api/settlement/demo` | 下载四个演示场景（等级优先、争议挂起、迟到重算、覆盖缺口） |
| `POST /api/settlement/ledgers` | 按证据建账，同一证据幂等返回同一账，不覆盖已付款历史 |
| `GET /api/settlement/ledgers/<id>` | 查询账的当前状态、revision 与关账快照 |
| `POST /api/settlement/ledgers/<id>/preview` | 不落库试算已付/可付/争议三桶与守恒结论 |
| `POST /api/settlement/ledgers/<id>/close` | 携带 `revision` 乐观关账；过期返回 409 `STALE_SETTLEMENT`，已关返回 `LEDGER_FINALIZED` |
| `POST /api/settlement/ledgers/<id>/late` | 关账后受理迟到瓦片并重开账；区域不得变更，已付款保留原领款人 |
| `GET /api/settlement/ledgers/<id>/events` | 关账事件审计（每次成功关账一条） |

裁决规则：已付款像元保留原领款人；未付款像元取覆盖瓦片中验收等级最高者，最高等级由多个不同供应商持有时挂起为争议。三桶互不重叠且并集等于有效覆盖（瓦片并集 ∪ 历史已付），否则返回 `PARTITION_BROKEN`，关不了账。
