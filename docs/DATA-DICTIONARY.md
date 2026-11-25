# 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `trip_id` | text | 主键 |
| `bike_id` | text | 业务字段 |
| `start_at` | datetime | 业务字段 |
| `end_at` | datetime | 业务字段 |
| `paused_minutes` | int | 业务字段 |
| `distance_m` | int | 业务字段 |
| `wallet_debit` | money | 业务字段 |
| `plan` | text | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。

## 退款日结字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `batch_id` | text | 日结批次号，同号重跑幂等 |
| `paid_on` | date | 银行出款日，决定所属结算日 |
| `refund_id` | text | 银行退款流水号，主键 |
| `amount_cents` | int | 出款金额，正整数分 |
| `voucher_ref` | text | 原扣款凭据号，可空 |
| `voucher_id` | text | 原扣款凭据号，主键 |
| `ride_id` | text | 凭据对应骑行单号 |
| `charged_cents` | int | 凭据实扣金额，正整数分 |
| `received_on` | date | 凭据到达日，认领时记录为认领日 |
| `status` | text | `PENDING` 暂挂 / `CLAIMED` 已退 |
| `reason` | text | `NO_VOUCHER`、`VOUCHER_LATE`、`OVER_REFUND`、`VOUCHER_MATCHED` |
| `claimed_on` | date | 认领日，仅已退条目有值 |
