# CertPilot · 证券从业刷题系统

单页刷题与学习进度工具 → 卡密授权的付费产品。**已上线**：https://www.cyronlabs.space:8443/

## 结构
```
web/index.html        内容母版（含 422 题）——只用于生成，不部署
web/shell.js          壳层：授权闸门 / API 客户端 / 上报队列
web/admin.html        后台页（发卡 / 卡密管理 / 运营数据），直接部署无需构建
dist/index.html       构建产物，54 KB，部署的就是它（不含任何题目）
api/app.py            FastAPI 后端，单文件
scripts/
  extract.py          母版 → data/content.json（带 422 题完整性断言）
  build_frontend.py   母版 + shell.js → dist/index.html（每条替换带计数断言）
  phase0_clean.py     Phase 0：改名 + 去溯源
data/
  content.json        灌库用
  _original_*.html.bak 清洗前原件，故意不入 git
```

## 改内容的流程
改 `web/index.html` 母版 → `python3 scripts/extract.py` →
`python3 scripts/build_frontend.py` → 上传 `data/content.json` 与 `dist/index.html`
→ 服务器上 `app.py import data/content.json` → `systemctl restart sec14-api`。
`import` 是 upsert，按 qkey 匹配，改解析不会丢用户的答题记录。

## 服务器（阿里云韩国 <演示环境地址>）
| | |
|---|---|
| 应用 | `/opt/sec14`，服务 `sec14-api`，uvicorn 单 worker，127.0.0.1:8000，占用 ~30 MB |
| 库 | `/opt/sec14/data/sec14.db`（SQLite WAL），每天 04:17 备份到 `backups/`，留 14 天 |
| 前端 | `/var/www/html/index.html` + `admin.html`，nginx 8443（**443 被 xray 占用，勿动**） |
| 后台 | https://www.cyronlabs.space:8443/admin.html — 发货取码、发卡、导出、运营数据 |
| 密钥 | `/opt/sec14/.env`，640 root:sec14 |
| 归档 | `/opt/sec14/archive/` — 旧版备份，**绝不能放回 webroot** |

## 常用命令（服务器上）
日常发卡与发货都在后台页完成，不需要 SSH。命令行只在应急时用：

```bash
cd /opt/sec14
.venv/bin/python app.py mint 20 season xianyu   # 发卡：plan ∈ trial7/d14/season/life
.venv/bin/python app.py admin <邮箱>             # 建/改管理员（交互输入密码，不留历史）
journalctl -u sec14-api -f                       # 看日志
```

## 已定架构决策
- **内容在服务端**：HTML 只剩壳，登录后拉 API。转发文件的人拿到空壳。
- **不接支付宝 API**：个人拿不到商户资质；淘宝/闲鱼代收款 + 卡密发货。
- **不做 RBAC**：`users.role` 两值 + `licenses.expires_at` 控访问 + 同时 2 台设备。
- **不用 Redis/MQ**：100 人在线约 3–5 写/秒，SQLite WAL 的上限高两个数量级。
- **错题本不建表**：`attempts` 上的窗口函数查询，答对自动移出。

## 卡密的三个状态
`licenses` 表永久保存所有卡密，状态由两个字段推出：

| 状态 | 判据 | 含义 |
|---|---|---|
| 库存 | `status='unused'` 且 `dispatched_at IS NULL` | 还没发给任何人，可导出/可取用 |
| 已发出 | `status='unused'` 且 `dispatched_at IS NOT NULL` | 已发给买家，等待激活 |
| 已激活 | `status='activated'` | 买家已注册，`user_id` 指向账号 |

`POST /admin/licenses/dispatch` 在一个 IMMEDIATE 事务里取码并盖发货戳，
并发下不会把同一个码发给两个买家（已用 8 线程并发验证）。

## 内容合规底线
产品不含任何第三方出版物内容。
- 科目二用法条原文（《著作权法》第五条：官方文件不受著作权保护）
- 真题只留题干与答案
- **422 道题的 `explain` 需全部自编 — 尚未完成，这是上架前唯一的硬阻塞**

## 待办
- [x] Phase 0 内容清洗与改名
- [x] Phase 1 卡密 + 账号
- [x] Phase 2 内容上云 + 错题本/进度同步 + 设备限制 + 水印
- [ ] **证书 2026-11-28 到期且无自动续期** — 必须装 certbot，否则考季当中断站
- [ ] 证书 SAN 只含 www，裸域 cyronlabs.space 会报错
- [ ] 重写 422 道题的 explain
- [x] Phase 3 后台页面
