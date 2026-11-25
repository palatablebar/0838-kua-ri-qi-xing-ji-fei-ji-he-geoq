# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `POST /api/refunds/batches`：退款日结入账，同批重跑幂等，同号不同内容返回 409。
- `POST /api/refunds/vouchers`：登记补到的原扣款凭据，认领匹配的暂挂款。
- `POST /api/refunds/batches/{id}/issue`：签发日结批次，重复签发返回 409。
- `GET /api/refunds/workbench`：退款工作台，核对暂挂与已认领、日结汇总与对账检查。
- `GET /api/refunds/sample`：退款样例（日结批次与晚到凭据）。
- `GET /healthz`：健康检查。
