# 修改计划参考

计划按顺序执行，后续操作会看到之前操作的结果。预览只修改内存，不写盘。
一次计划最多 64 个操作、累计扫描 1000000 个体素；超过时缩小区域或拆成多个另存版本。
`changed_assignments` 是实际变更赋值次数；同一坐标被多次改变会计多次，并非最终不同坐标数量。

## 操作

以下对象均放在 `operations` 数组内；多区域投影每项都要指定 `region`。

精确设置一个位置，替换完整状态：

```json
{"op":"set","region":"main","pos":[1,2,3],"state":"minecraft:observer[facing=east,powered=false]"}
```

填充闭区间：

```json
{"op":"fill","region":"main","box":[0,0,0,4,0,4],"state":"minecraft:smooth_stone"}
```

替换指定方块 ID，保留范围外的方块：

```json
{"op":"replace","region":"main","box":[0,0,0,8,8,8],"match":"minecraft:cobblestone","state":"minecraft:stone_bricks"}
```

只改变中继器延迟、保留朝向等属性：

```json
{"op":"properties","region":"main","box":[0,0,0,8,8,8],"match":"minecraft:repeater","properties":{"delay":"4"}}
```

`replace`、`properties` 必须显式给 `match`；省略 box 表示整个**指定区域**。
`fill` 必须显式给 box。所有 box 必须完全位于区域内，工具不会静默裁剪。

## 匹配

- `stone` 等价于 `minecraft:stone`。
- 无属性时，匹配方块 ID，不关心该方块的属性。
- 方块 ID 可用 shell 风格通配符，例如 `minecraft:*_planks`，必须在 shell 中加引号。
- 带 `[...]` 时是**完整状态精确匹配**，不是属性子集匹配；属性顺序不影响结果。
- 属性名和值都是字符串。工具校验语法，并限制 `properties` 操作只能更新现有属性键；不内置各 Minecraft 版本的方块注册表，不能保证属性值合法。
- 如仅想修改某种朝向，先 query 获取完整状态，再用完整状态作为 match；不要用仅包含 facing 的状态误以为能匹配属性子集。

## 坐标与保护

`Position` 是区域选择锚点，`Size` 可以为负。实际最小坐标为 `position + min(0, size + 1)`，最高坐标为最小值加 `abs(size)-1`。工具统一返回投影坐标，可直接用于 query、inspect 和 edit。

多区域重叠不会合并或自动同步；修改计划必须选择具体区域。region 边界外邻居标记 unknown；不能据此证明支撑缺失。

非目标 NBT 字段、实体、方块实体和计划刻保留；压缩字节和字符串编码不保证逐字节一致。目标区域有计划刻，或目标坐标有方块实体时，工具拒绝实际变化。更复杂的搬移、旋转、容器数据编辑应交给其他游戏感知工具。

应用前检查源 SHA256，写出后重新解析并验证所有区域的方块索引。输出先写临时文件，再以不覆盖现有路径的方式发布；仅成功后返回 `applied=true`。不会覆盖源文件，也不提供原地写入选项。
