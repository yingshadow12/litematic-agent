# Litematic Agent

给 AI Agent 使用的 Minecraft Litematica 投影查看与修改工具，面向建筑和红石机器分析。默认返回有界 JSON、区域切片与修改摘要，避免把整个投影塞进模型上下文。

Python ≥3.10，**运行时零第三方依赖**。目前只处理 Java 版 `.litematic`，不处理 `.schem` / `.schematic`。

## 直接使用

无需安装包，在仓库根目录运行：

```bash
python .agents/skills/litematic-agent/scripts/litematic_tool.py --help
python .agents/skills/litematic-agent/scripts/litematic_tool.py summary /path/to/build.litematic
```

也可 `python -m pip install -e .`，之后用 `litematic-agent` 和 `litematic-mcp`。构建安装需要 setuptools；直接运行脚本不需要 pip、联网或构建工具。

下面用 `litematic-agent` 表示命令；未安装时换成上面的 `python .../litematic_tool.py`。
示例的文件、区域名和坐标需换成你的投影实际值。

| 命令 | 用途 |
| --- | --- |
| `summary FILE` | SHA256、区域、尺寸、边界，区域列表可分页 |
| `analyze FILE --region main --box 0 0 0 16 8 16` | 材料、红石元件/朝向、各层密度摘要 |
| `query FILE --region main --category redstone --limit 16` | 有界红石元件搜索 |
| `query FILE --region main --match 'minecraft:*piston'` | 方块 ID 通配符或完整状态搜索 |
| `inspect FILE --region main --pos 1 2 3` | 单方块、六邻居、方块实体存在性 |
| `slice FILE --region main --axis y --at 2 --box 0 0 0 16 8 16` | ASCII 平面 + 状态图例 |
| `edit FILE --plan plan.json` | 预览修改计数和最多 12 个样例，不写盘 |
| `edit FILE --plan plan.json --output improved.litematic --apply` | 校验后另存，拒绝覆盖现有文件 |
| `validate FILE` | 遍历验证所有方块索引与 NBT 结构 |

JSON 修改计划示例：

```json
{
  "source_sha256": "先运行 summary 获取真实 SHA256",
  "operations": [
    {"op":"properties","region":"main","box":[0,0,0,16,8,16],"match":"minecraft:repeater","properties":{"delay":"4"}}
  ]
}
```

支持 `set`、`fill`、`replace` 和 `properties`。后者只修改现有属性键，保留其他属性；前三者替换完整状态。
详细说明见 [修改计划参考](.agents/skills/litematic-agent/references/editing.md)。

## 给 Agent 安装技能

仓库已经包含可供兼容 Agent 发现的 [.agents/skills/litematic-agent/SKILL.md](.agents/skills/litematic-agent/SKILL.md)。它引导 Agent 先看摘要，再按目标读局部，最后生成修改计划并验证。

迁移到其他机器或 Agent 时，导出包含完整 Python 工具的自包含技能包：

```bash
python scripts/package_skill.py
```

将 `dist/litematic-agent-skill.zip` 解压到目标 Agent 的技能目录（例如个人 Codex 技能目录 `~/.agents/skills/`），得到 `litematic-agent/SKILL.md` 及其 scripts。导出的包无需原仓库或 pip 安装即可使用。相应 Agent 若不自动发现技能，可直接读这个 SKILL.md 或配置以下 MCP。

## MCP

提供 **stdio MCP**，无 HTTP 服务或网络端口。支持初始化、工具发现、调用与 ping。

```json
{
  "mcpServers": {
    "litematic": {
      "command": "python",
      "args": ["/absolute/path/to/fantastic-succotash/.agents/skills/litematic-agent/scripts/litematic_tool.py", "mcp", "--root", "/absolute/path/to/schematics"]
    }
  }
}
```

这是常见客户端配置示例，具体配置文件位置由客户端决定；没有自动修改你的 Agent 全局设置。
`--root` 必须存在，所有投影输入/输出均限制在其内（包括解析符号链接后）；相对路径以 root 为基准。不要把整个用户主目录设为 root。

7 个工具：`litematic_summary`、`litematic_query`、`litematic_inspect`、`litematic_slice`、`litematic_analyze`、`litematic_edit`、`litematic_validate`。参数与 CLI 对应，`plan` 直接传 JSON 对象，写入时 `apply: true`。

## 上下文和数据限制

- 搜索默认返回最多 32 项，最多可设 128；默认只扫描 200000 个体素，每次至多 1000000。继续搜索要使用 `next_cursor`，保留相同筛选条件并确认源 SHA256 未变。
- 分析的 `complete=false` 表示只统计已扫描部分，不能当作整个建筑的材料用量。类别列表使用 `omitted_keys` 标出未显示类别。
- 切片最多 4096 格、每边 128 格；每个工具文本响应最多约 24KB。超限会返回 `response_limited`，应缩小选择范围。
- 文件最多 64 MiB，解压 NBT 最多 256 MiB，总体素最多 1600 万。NBT 深度和标签总数也受限。暂不面向超大型整图。
- 支持 Litematica NBT 格式版本 4–7 的连续位打包布局；方块 ID/属性值不依赖固定 Minecraft 注册表，因此语法有效不等于游戏中有效。

## 修改边界

工具能给 Agent 提供分析和修改能力，**不包含 Minecraft 红石模拟器或自动优化算法**。红石性能、延迟、更新顺序、QC 和装置功能仍要在对应游戏版本验证。

不移动/旋转机器、不编辑容器物品或实体 NBT。修改触及方块实体，或目标区域有计划刻时，会拒绝改变方块。原文件永不覆盖；输出写入后会重新解析并验证。未知 NBT 字段与非目标数据保留，但压缩结果不保证逐字节一致。多区域重叠不自动合并。

## 开发验证

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

测试生成投影夹具，覆盖位打包、负尺寸、读写往返、属性修改、源 SHA256、实体/计划刻保护、分页和真实 CLI/MCP 子进程。测试不会访问用户投影，不需要第三方依赖。

本轮验证结果及独立 Litemapy 交叉检查见 [验证记录](docs/validation.md)。
