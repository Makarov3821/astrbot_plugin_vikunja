# AstrBot Vikunja 私人待办秘书

![astrbot_plugin_vikunja](https://count.getloli.com/@astrbot_plugin_vikunja)

面向单一用户的 AstrBot 插件。QQ 官方机器人私聊和微信个人号 ClawBot（`weixin_oc`）是同一个 Vikunja 工作空间的两个入口：都能查看全部项目和任务，也都可以接收提醒。插件不响应群聊中的待办命令，LLM Tool 在群聊中也会拒绝读写。

## 使用模型

- 一个 AstrBot 实例、一个 Vikunja API Token、一个真实用户。
- QQ 和微信不分别绑定项目，只登记为提醒渠道。
- 两个入口共享全部项目、子项目、任务和完成状态。
- 未指定项目的日常琐事进入默认 `Inbox`。
- 工作事项使用完整项目路径，例如 `fudan-work/SMX/paper`。
- 每个入口可独立执行 `/todo remind on|off`；默认都会收到提醒。

## 秘书模式

插件不仅提供命令，还注册了项目树、创建任务、完成任务和查询任务四个 LLM Tool，并向私聊会话加入稳定的秘书规则。因此可以直接说：

需要为该会话启用支持 Tool Calling 的模型和 AstrBot Agent；如果模型未启用工具调用，自然语言仍会被普通聊天处理，但不会实际写入 Vikunja，斜杠命令不受影响。

```text
提醒我明天下午六点前交周报。
这周要把 SMX 那篇 paper 的引言重写一下。
我今天还有什么没做？
刚才那个买牛奶的任务完成了。
```

模型在写入前应检查信息是否充分。代码层还有第二道保护：

- 用户说“提醒我”但没有明确日期和时间：不创建，先追问。
- 明显属于论文、研究、项目或工作的事项却没有项目归属：先展示/查询项目树并追问。
- 明显长期的事项没有截止时间、计划时长或频率：不创建，先追问。
- 标题含“每天、每周、每月、定期”但没有确认重复规则：不创建，先追问。
- 修改或完成目标不唯一时，模型应先查询并让用户确认任务 ID。

这是“规则 + 工具保护”的实现，不依赖模型一次性猜对所有字段。最终是否创建以工具返回结果为准。

## 安装与配置

将本目录放入 AstrBot 的 `data/plugins/astrbot_plugin_vikunja`，安装 `requirements.txt` 后重载插件。AstrBot 最低版本为 `4.22.1`，建议使用当前最新版。

在 WebUI 中配置：

1. `vikunja_url`：Vikunja 地址，插件自动补全 `/api/v1`。
2. `api_token`：具有项目读取、任务读取和任务写入权限的 API Token。
3. `default_project`：默认 `Inbox`，也可填写完整路径或项目 ID。
4. `allowed_qq_sender_ids`：可选，仅限制 QQ 官方机器人私聊发送者；`weixin_oc` 不检查此项。
5. `timezone`、提前提醒分钟数、轮询间隔和列表长度。

插件通过 AstrBot KV 存储已登记的 QQ/微信私聊渠道、自定义提醒阈值和提醒去重记录。Token 不会经过聊天传输。

## 命令

命令主要用于精确操作和排障；日常使用可以直接自然语言交流。

```text
/todo projects
/todo add 买牛奶 --due "明天 18:00"
/todo add 修改论文引言 --project "fudan-work/SMX/paper" --due "周日 20:00" --priority 4
/todo add 每日复盘 --project personal-project --due "今天 22:00" --repeat daily --remind 1h
/todo done 123
/todo today
/todo list week
/todo list overdue --project "fudan-work/SMX/paper"
/todo list all
/todo remind off
/todo help
```

项目选择器支持项目 ID、唯一名称或完整路径。时间支持 `明天9点`、`周日 20:00`、`2小时后`、`2026-07-12 18:00`。带空格的参数需使用引号。

## 私聊限制

`/todo` 指令组使用 AstrBot 的 `PRIVATE_MESSAGE` 过滤器。所有执行读写的内部方法还会检查 `event.get_group_id()` 和平台类型，防止 LLM Tool 或其他调用路径绕过过滤器。QQ 官方机器人额外检查 `allowed_qq_sender_ids`；微信 `weixin_oc` 私聊始终放行，不受 QQ 白名单影响。

QQ 群即使安装了插件也不能读取或修改 Vikunja。如果同一个 QQ 官方机器人还承担其他功能，其他插件仍可正常处理群聊。

QQ 白名单默认留空。需要时可用 AstrBot 内置 `/sid` 和日志中的 `get_sender_id()` 确认 QQ UID。即使只填写你的 QQ UID，微信 `weixin_oc` 仍可正常使用。

## 项目与查询

`/todo projects` 会根据 Vikunja 的 `parent_project_id` 展示完整层级，例如：

```text
📁 Vikunja 项目树
• Inbox (#1)
• fudan-work (#2)
  • SMX (#3)
    • paper (#4)
• personal-project (#5)
```

`today`、`week`、`overdue` 和 `all` 默认跨所有项目查询，结果包含项目路径，并按优先级降序、截止时间升序排列。使用 `--project` 可以限定到具体项目。

## Vikunja API

实现依据所附 Vikunja OpenAPI v2.3.0：

- `GET /projects` 获取项目和父子关系
- `PUT /projects/{id}/tasks` 创建任务
- `GET /tasks` 跨项目查询并分页
- `GET /tasks/{id}`、`POST /tasks/{id}` 完成任务
- `repeat_after`、`repeat_mode` 设置重复规则

## 测试

```bash
python -m unittest discover -s tests -v
```
