# AstrBot Vikunja 秘书大脑

![astrbot_plugin_vikunja](https://count.getloli.com/@astrbot_plugin_vikunja)

面向单一用户的 AstrBot 插件。把自建 Vikunja 当作唯一事实源，在 QQ 官方机器人私聊或微信
`weixin_oc` 私聊里记录、安排、改期和提醒；同时在 Vikunja 网页上留下一套能直接看的跨项目
看板与甘特图。插件不响应群聊，LLM Tool 在群聊中也会拒绝读写。

## 它解决什么

Vikunja 里的 project 只显示自己的任务，父项目不会聚合子项目，所以多个课题、多个实习项目加上
生活杂事放进不同 project 之后，网页上看不到一张全局的大图。这个插件把三个维度分开使用：

| 维度 | 用途 | 谁来维护 |
| --- | --- | --- |
| project | 这件事属于谁：课题、实习项目、生活杂事 | 你，偶尔调整 |
| label | 什么场景能做、要多久：`@深度` `@碎片` `@外出` `@要找人` `@等待中`、`est15`~`est4h` | 秘书自动带上 |
| 日期 | `due_date` 只写真死线；`start_date`/`end_date` 是"打算什么时候做"的时间块；`reminders` 决定什么时候响铃 | 秘书每天改 |

跨项目的大图由 **saved filter** 提供，它在 Vikunja 里是虚拟项目，自带 List / Table / Kanban /
Gantt 四个视图。`/todo setup` 会一次性建好：

- `☀️ 今天`：今天到期、已逾期，或今天排了时间块 —— 建议在 Vikunja 设置里设为首页过滤器
- `⏰ 逾期`、`🗓 本周`、`⏳ 等待中`、`🚶 出门顺手`、`📥 没排期`
- `🧭 总看板`：Kanban 视图，按 `等待中 / 今天 / 本周 / 以后 / 没排期` 自动分列（filter buckets，不用手动拖）
- `📈 时间线`：Gantt 视图，跨项目时间轴，子任务分组、依赖关系显示为箭头

## 秘书能做的事

- 记录：「这周要把课题一的引言重写一下」→ 建任务，不强迫设死线
- 安排：「帮我排一下今天」「我下午有两小时」→ 读今日候选、按 `est*` 估时贪心排块，写回 `start_date`/`end_date`，网页甘特图立刻可见，已排的块不会被占两次
- 改期：「来不及了，挪到周三」→ 改 `due`/`start` 并可在任务评论里留一句原因，而不是新建一条重复任务
- 拆解：「把这篇论文拆一下」→ 建子任务（`parenttask` 关系），必要时用 `blocked`/`precedes` 表达依赖
- 等待：「等师兄给数据」→ 打 `@等待中`，它就不占今天的清单，但会留在等待中看板里
- 提醒：任务自带的 `reminders`（含"相对 due 提前 N 分钟"）会推送到聊天里；**你在 Vikunja 网页上设的提醒同样会推**
- 早报：每天一次（默认 07:30）推送今日议程，可选用模型润色成人话；其余时间你主动问就行

LLM 工具：`vikunja_list_projects`、`vikunja_create_project`、`vikunja_create_task`、
`vikunja_update_task`、`vikunja_complete_task`、`vikunja_delete_task`、`vikunja_query_tasks`、
`vikunja_agenda`、`vikunja_plan_day`、`vikunja_add_subtask`、`vikunja_link_tasks`、
`vikunja_comment`。需要为该会话启用支持 Tool Calling 的模型；模型未启用工具调用时自然语言不会
写入 Vikunja，斜杠命令不受影响。

Vikunja 写入失败时（网络中断、Token 失效），创建请求会落到本地 KV 作为降级缓冲（`L` 开头的 ID），
并明确告知这条还没进 Vikunja；用 `/todo local` 查看，恢复后需要补录。

## 安装

1. 把本目录放到 AstrBot 的 `data/plugins/astrbot_plugin_vikunja`。
2. 安装 `requirements.txt`（只有 `aiohttp`）。
3. 重载插件。AstrBot 最低版本 `4.22.1`。

Docker 部署示例（把源码目录同步到宿主机的 AstrBot 数据目录）：

```bash
rsync -a --delete \
  --exclude '.git' --exclude '__pycache__' --exclude '.ruff_cache' --exclude 'tests' \
  /home/zyl/project/astrbot_plugin_vikunja/ \
  /home/zyl/data1/astrbot/data/plugins/astrbot_plugin_vikunja/
```

然后在 AstrBot WebUI 的插件页重载 `astrbot_plugin_vikunja`（或重启容器）。

## 配置

WebUI 插件配置项：

| 配置 | 说明 |
| --- | --- |
| `vikunja_url` | 例如 `https://vkj.example.com`，插件自动补 `/api/v1` |
| `api_token` | 需要项目、任务、标签、过滤器读写权限；不要在聊天里发送 |
| `default_project` | 默认 `Inbox`，杂事的落点 |
| `allowed_qq_sender_ids` | 仅限制 QQ 官方机器人私聊发送者，`weixin_oc` 不检查 |
| `timezone` | 同时作为 Vikunja 服务端 `now/d` 这类相对日期的 `filter_timezone` |
| `reminder_minutes` | 只对"有截止时间但没单独设提醒"的任务生效 |
| `briefing_enabled` / `briefing_time` / `briefing_use_llm` | 早报开关、时间（默认 07:30）、是否用模型润色 |
| `work_windows` | 排块默认可用时段，默认 `09:00-12:00,14:00-18:00,19:30-22:00` |
| `default_block_minutes` | 没有 `est*` 标签时的默认块长，默认 30 |
| `poll_interval_seconds` / `request_timeout_seconds` / `max_list_items` | 轮询、超时、列表长度 |

## 第一次使用

```text
/todo diag            # 确认能连上、版本、结构是否齐全
/todo setup full      # 建标签 + 8 个跨项目看板 +（full 时）项目骨架 PhD/实习/生活
/todo board           # 拿到各看板的网页链接
/todo guide           # 三个维度速查
```

`/todo setup` 是幂等的，可以重复执行；已存在的标签、过滤器会跳过。`full` 会额外创建
`Inbox`、`PhD/课题一二三`、`实习/项目一二`、`生活/购物与家务、长期与学习`，不需要就用
`/todo setup`。建好后建议在 Vikunja 设置里把 `☀️ 今天` 设为首页过滤器。

## 命令

```text
/todo agenda                            今日议程（逾期/时间块/到期/等人/没排期）
/todo plan 14:00-18:00 --apply          按可用时间排块并写回；不加 --apply 只给建议
/todo add 买牛奶 --label @外出
/todo add 写引言 --project "PhD/课题一" --start "2026-09-21T09:00" --end "2026-09-21T11:00" --label est2h,@深度
/todo add 交周报 --due "2026-09-21T18:00" --remind 30m --priority 4
/todo add 每日复盘 --due "今天 22:00" --repeat daily
/todo done 123
/todo today
/todo list week|overdue|unscheduled|waiting|scheduled [--project P]
/todo projects
/todo local | /todo snooze Lxxx 30分钟后 | /todo pause Lxxx
/todo remind off
/todo help
```

时间推荐 ISO `2026-09-20T18:00`，也支持 `明天9点`、`下周三下午3点`、`今晚8点半`、`2小时后`、
`9/20 18:00`。带空格的参数要加引号。项目选择器支持 ID、唯一名称或完整路径。

## 私聊限制

`/todo` 使用 AstrBot 的 `PRIVATE_MESSAGE` 过滤器；所有读写方法还会再检查
`event.get_group_id()` 和平台类型，防止 LLM Tool 绕过。QQ 官方机器人额外检查
`allowed_qq_sender_ids`（留空表示不校验），微信 `weixin_oc` 私聊始终放行。

## 主动推送的限制

早报与提醒通过 `context.send_message` 主动下发。AstrBot 侧对 `weixin_oc` 直接按用户下发；
QQ 官方机器人私聊也允许主动消息，但腾讯对主动消息有配额与审核限制，实际到达率需要你实机验证。
早报只在设定时间后两小时内补发一次，错过不追；提醒不重放 12 小时以前的时刻，避免停机后刷屏。
`/todo remind off` 会同时关闭当前入口的提醒与早报。

## Vikunja API 使用范围

对照你自己服务器的 `GET /api/v1/docs.json`（已在 v2.5.0 上核对）：

- `GET /projects`（含负 ID 的 saved filter 伪项目）、`PUT /projects`
- `GET /tasks`（服务端 `filter` + `filter_timezone` + date math，`per_page=50`）
- `PUT /projects/{id}/tasks`、`GET|POST|DELETE /tasks/{id}`
- `GET|PUT /labels`、`PUT|DELETE /tasks/{id}/labels`
- `PUT /tasks/{id}/relations`、`PUT /tasks/{id}/comments`
- `PUT|POST /filters`、`GET /projects/{id}/views`、`POST /projects/{id}/views/{view}`
- `GET /info`（版本与分页上限探测）

saved filter 的伪项目 ID 关系是 `project_id = filter_id * -1 - 1`（`models/saved_filters.go`），
插件按此换算来配置它的 Kanban 分列。

## 已知的不确定点

- `percent_done` 按 0–1 小数写入（前端按百分比显示）。如果你的实例显示成 `0.5%`，
  改 `main.py` 里 `changes["percent_done"]` 的除以 100 即可。
- `📥 没排期` 与总看板的"没排期"列依赖 `filter_include_nulls` 取空值的技巧，
  不同版本表现可能不同；如果不准，在网页上改这一条过滤器即可，其他不受影响。

## 测试

```bash
python -m unittest discover -s tests -v
```

65 个用例覆盖时间解析、议程分组、贪心排块、bootstrap 幂等性、客户端请求构造、提醒去重，
以及工具层的端到端流程（用内存假 Vikunja）。
