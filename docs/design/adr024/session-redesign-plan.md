# 会话视图重做：执行计划

2026-10-20，整合 Host。

**依据：**
- [ADR-024](../../decisions/024-console-session-view.md) 第 3、6、7 条 2026-10-20 的修改；
- [调研记录](../../research/2026-10-20-session-view-display-components.md)；
- 设计稿 [session-redesign](session-redesign/)，用三个真实回合的记录画成，深色：
  - Codex 的回合：命令多，中间夹着说明与子代理的标记；
  - Claude Code 的独立审查：读取与搜索多；
  - ZCode 的回合：带黑板工具与有格式的回答。

开发者看过设计稿后定：一行只放图标、对象与状态，照这一版做；看稿与验收先看深色。本文只安排实现，不表示已经实现。

**进度：** B 于 2026-10-20 合入为 `1c575cd2`，A 于同日合入为 `9194f8fd`，都在源码里，尚未安装（[验收记录](../../acceptance/adr024/session-view.md)）；开发者看过真实像素与设计稿的对照后，同日定下与设计稿不同的五处先修好，不等 C：修正微任务同日合入为 `be992386`，也在源码里、尚未安装（见下面“A 的修正”）。C 还没有开始。

## 现状（读 `908384a5` 的源码所得）

- **组件：** `apps/console/src/components/ai-elements/` 是从 `ai-elements@1.9.1` 改写的六个展示部件（消息、推理、工具、计划、终端、代码块），Apache 2.0。
- **视图：** `apps/console/src/session-view/`：
  - `blocks.ts` 把事件合成块：消息按条目与片段合并，工具按调用编号合并；
  - `SessionBlocks.tsx` 逐块画：每次工具调用是一块默认展开的区块，下面再有“工具输入”“结果数据”“输出正文”三层折叠；
  - `use-session.ts` 按页读取，每页 100 条，打开时停在最新处，往上加载更早的页。
- **跨页的调用：** 一页从某次调用的更新开始，而它的开头在更早的一页时，这一块以调用编号为题，类别与状态都显示“未记录”。
- **ZCode 的投影**（`buddy/harnesses/zcode/session_content.py`）只在 `tool.updated` 里找 `arguments`。ZCode 0.16.9 的真实事件里没有这个字段，所以真实回合的工具调用都没有输入。

## 微任务 A：会话视图（控制台）

1. **换组件。**
   - 改从 shadcn/ui 里 AI Elements 一节取组件，用 Radix 的版本，以源码形式按它的分发方式加入：消息滚动容器、消息、标记、步骤链或任务、工具、推理、终端、代码块，用到哪些取哪些。
   - 删掉现在的 `components/ai-elements/` 及其说明与许可文件，不留两套。
   - 加入之前核对许可，在交付里写明。
   - 不引入 AI SDK 的运行时状态。
2. **从块到行的归组。** 写一个纯函数，把合成后的块变成视图里的行：
   - 任务块；
   - 说明：buddy 的消息；
   - 回答：最后一条 buddy 的消息；
   - 调用组：相邻的工具调用；
   - 单次调用：只有一次时不加组头；
   - 黑板动作：黑板自己的工具；
   - 推理：默认收起；
   - 带正文的标记。

   规则如下：
   - 只有类型、没有正文的标记（例如 Codex 的子代理活动）并进所在的调用组，不把组拆开。
   - 黑板自己的工具按名字的后缀认：`hey_my_buddy_checkpoint`、`hey_my_buddy_answer_inquiry`、`hey_my_buddy_finish_turn`。各 harness 加在前面的前缀不同，都要认得。这些调用不进组，显示成一句淡色的话：“查看 Host 的问题”“回答 Host 的问题”“交回结果”。
   - 组的摘要按类别计数，例如“读取 3 个文件 · 执行 7 条命令 · 修改 2 个文件”，英文对应地写。有失败的，在摘要后写失败的次数，用失败色。
3. **一行写什么。** 只有图标、对象与状态；没有对象时写动作的名字。对象按类别从工具输入里取：
   - 读取与修改：`file_path`、`path`、`notebook_path`；
   - 执行：`command`，去掉外层的 shell 包装与引号，只取第一行；
   - 搜索：`pattern`，后面跟 `glob`；
   - 获取：`url`；
   - 其余：`locations` 的第一个路径，再没有就用标题。

   路径显示成相对检出根目录的样子，主目录写成 `~`。状态：完成是淡色的勾，失败是失败色的叉，进行中用动效。
4. **细节只在点开一行时显示，不再层层折叠。**
   - 命令：完整命令，只在行里被截断时才显示，下面是输出；
   - 其他调用：输入逐项写成“键 值”，路径照上一条处理，下面是结果。
   - 长输出先显示前 14 行，“展开其余 N 行”显示全部。内容不截断（ADR-024 第 2 条）。
5. **任务块。**
   - 显示任务的标题与开头两行，从微任务的任务正文取（详情页的读取里已有 `task.task`）；
   - “展开发给 buddy 的全文（含角色说明与交回格式）· N 字”显示第一条用户消息的全文。
6. **页。**
   - 照旧打开时停在最新处，往上加载更早的页；
   - 一次调用的开头还在没读到的更早一页时，这一块先不画，读到再画（待办“跨页被截开的调用先显示成‘未记录’”）。
7. **样式照设计稿。**
   - 尺寸：内容最宽 820px，正文最宽 700px，正文 14.5px、行高 1.72；
   - 调用行高 30px，组内缩进，左边一条 1px 的引导线；
   - 输出块用 `--sunk` 的底色。
   - 颜色只用 `tokens.css` 里已有的；图标用已有依赖 lucide。
   - 不加圆角盒子套盒子（ADR-029）。深色先做好，浅色也要对。
8. **可访问性：** 组头与行都是按钮，带 `aria-expanded`，键盘可达，焦点可见。
9. **文字：** 新的文字都走 i18n，中英文都有。
10. **测试与预览。**
    - 删掉钉着旧结构的测试；
    - 归组函数、行的对象、黑板动作、跨页规则与摘要文字各有单元测试；
    - 浏览器检查覆盖深浅两色、长会话、失败、展开的细节与加载更早的页；
    - 合成预览的会话照四个 harness 的真实形状重写：
      - Codex：命令与输出，夹着标记；
      - Claude Code：读取与搜索，带输入；
      - ZCode：带输入，另有一个没有输入的旧回合；
      - DSH：只有问答。
11. **构建产物：** 重建 `src/hey_my_buddy/console/assets/`。

**写入范围：** `apps/console/`（除 `MacroTasks.tsx`、`macro-task-display.ts` 与 i18n 的 `tasks.ts`），`src/hey_my_buddy/console/assets/`，合成预览的数据 `tests/probes/macro_task_console_preview.py`，以及 `tests/python/cli/test_host_preview.py`。

**检查：**
- `npm --prefix apps/console run typecheck`、`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`；
- 检查运行器跑 `tests/python/console/` 与 `tests/python/cli/test_host_preview.py`。

## 微任务 B：ZCode 的工具输入（harness）

本机安装的 ZCode 0.16.9 里，工具的输入有两条路：

- **流式推送：** 模型流式产出的每次工具调用，发一条 `model.streaming` 事件，载荷的 `kind` 为 `tool_call`，带 `toolCallId`、`toolName` 与完整的 `input`。
- **`tool.updated`：** 随后 `kind` 为 `scheduled` 的 `tool.updated` 事件里，已经流式发过输入的调用，`input` 被去掉，换成 `inputOmitted: true` 与 `inputByteLength`；没有流式发过的，`input` 留在这里。

要做的：

1. 投影从这两处取输入，作为 `tool_call` 的 `rawInput`；不再找 `arguments`。
2. 测试与合成事件改成真实的两种形状：先流式后 `scheduled` 去掉输入，以及只有 `scheduled` 带输入。
3. 改写 `session_omitted.py` 的说明，写明工具输入的来源与核对依据。

不调用真实的 ZCode。

**写入范围：** `src/hey_my_buddy/buddy/harnesses/zcode/`，`tests/python/buddy/harnesses/zcode/`。

**检查：** 检查运行器跑 `tests/python/buddy/harnesses/zcode/` 与 `tests/python/buddy/harnesses/test_harness_conformance.py`。

## 微任务 C：宏任务列表的排序与时间轴上被改派的尝试（A 合入之后）

1. **排序。** 开发者 2026-10-20 提出：宏任务列表可以按最近更新或创建时间排序，各有升序与降序；默认照旧，按最近更新降序。
   - 排序在服务里做：`macro_task_list` 加排序的键与方向，分页的游标跟着排序走。宏任务的创建时刻已经存在 `macro_tasks.created_at`。
   - 控制台在列表头上放一个排序控件。
   - 这改了 Host 可见的方法参数，契约随之移动。
2. **被改派的尝试：** 由服务的宏任务时间轴在片段上标出 `rejected`，删掉微任务页 `TaskDetail.tsx` 里那段前端拼接（待办“宏任务时间轴把被改派的那次尝试画成失败”）。
3. **“适应宽度”：** 统一成一种叫法（待办同名一条）。
4. **剪枝一的审查分来的几条**（[审查记录](../schema-audit/review-m1.md)，Host 核实过）：
   - 错误码 `MACRO_TASK_DELETED` 现在只在删除进行中出现：改名为表达“删除中”的码，去掉细节里恒为常量的 `state` 与 `deletedAt`，HTTP 映射与控制台文字随之改；
   - 公开视图里由别的字段推出的 `acceptanceVerdict` 与 `correlationId` 从接口删掉，控制台里 `acceptanceVerdict === "failed"` 的死分支一并删；
   - 合成预览按新的删除契约回答：删完之后预览与再删都是 `NOT_FOUND`，`test_host_preview.py` 里钉着旧行为的断言随之改；
   - 两个浏览器同时删除时，后一个得到 `NOT_FOUND`，按已删完处理，不显示成错误。
5. **剪枝二的验收与审查分来的几条**（[审查记录](../schema-audit/review-m2.md)，Host 核实过）：公开视图里恒为常量的字段从接口删掉——helper 视图的 `autoContinue`（恒为 false），Host 结论的 `evidence`（恒为空）与 `integrationId`（恒为 null）；审计视图的 `spec` 与 `microTask` 读的是同一份，删 `spec` 与只为它留着、还吞掉解析异常的 `_original_spec`。
6. **剪枝三分来的几条**（[验收记录](../../acceptance/schema-audit/pruning.md)）：冲突、整合与清理视图里恒定或重复的公开字段，从接口删——
   - 冲突：`delivery` 的 `state`、`kind`、`turnOutcome` 恒定，`action/artifactId/attemptId/turnId/commandId` 与外层字段重复；
   - 整合：`verification` 的 `verified`、`unrecordedPaths`、`summary` 恒定，`artifactAncestor` 由 `strategy` 推出，`adjustments` 由 `differingPaths` 与 `missingPaths` 推出，`strategy/target/source…/before…/after…` 与外层字段重复，`notRequired` 由 `strategy` 推出；
   - 清理：`retention.commandReceipts` 恒定，`result` 的 `removed` 恒为 true，`path/repositoryId/checkoutId/retention` 与外层字段重复。
   控制台读到的（例如 `parseIntegration` 读 `summary`）随之改。

写入范围与检查在 A 合入之后定。

## A 的修正（开发者 2026-10-20 定，先于 C）

A 验收时 Host 用真实会话重拍像素，看到五处与设计稿不同（[验收记录](../../acceptance/adr024/session-view.md)）。A 已验收，修正是一个新的微任务，标为修正 A：

1. **点开一次调用，结果只显示一块。** Host 核对过真实记录：Claude Code 的结果数据是与正文逐字相同的字符串；Codex 命令的 `aggregatedOutput`、ZCode 的 `content` 与正文逐字相同；Codex 修改文件的 `changes[].diff` 就是正文里的差异；ZCode 另有的几项计数里，`truncated` 在所有真实记录里都是 false。所以有输出正文时只显示正文；没有正文时才显示结果数据，照输入的样子逐项写成“键 值”，值为 null 的不写（例如没有输出的命令只写退出码与用时）。
2. **命令的完整内容去掉外层的 shell 包装**，与行里一致，保留全部行。
3. **只有类型的标记有自己的名字**：Codex 的 `subAgentActivity` 是“子代理活动”，`contextCompaction` 是“上下文压缩”，走 i18n，摘要里按类型分别计数，组里写它的名字；不认得的类型才用通称，带上原生类型名。
4. **调用组的摘要行右端有状态**：全部完成是淡色的勾，有失败时失败次数之后是失败色的叉，进行中用动效。
5. **搜索一行的模式加引号**，后面跟 `glob`；引号走 i18n。

写入范围与检查同 A。

## Host 的验收

- **A：**
  - 从日常看板只读地取四个 harness 的真实会话页，用新的解析与视图在预览里渲染；
  - 把实际像素与设计稿并排对照，先深色后浅色，列出与设计稿不同的地方；
  - 前端全量与浏览器检查在最终源码上跑。
- **B：** 读差异、跑受影响的测试；装上之后用第一个真实的 ZCode 回合核对工具输入。
- **版本：** A 与 B 只改控制台与投影，不改契约；C 改了契约。装的时候按合在一起的改动定版本号。
