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

**进度：** B 于 2026-10-20 合入为 `1c575cd2`，A 于同日合入为 `9194f8fd`，（[验收记录](../../acceptance/adr024/session-view.md)）；开发者看过真实像素与设计稿的对照后，同日定下与设计稿不同的五处先修好，不等 C：修正微任务同日合入为 `be992386`（见下面“A 的修正”）。A、B 与修正同日随 0.49.0 安装（[安装记录](../../acceptance/installs/0.49.0.md)）。批次 C 的 C1 与 C2 同日合入为 `986f4b03` 与 `dc640eab`（[验收记录](../../acceptance/contract-050/acceptance.md)），同日随 0.50.0 安装（[安装记录](../../acceptance/installs/0.50.0.md)）。

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

## 批次 C：契约 0.50.0（0.49.0 装好之后）

0.49.0 装好之后，原来的微任务 C 拆成两个依次做的微任务：C1 收掉剪枝各步留下的公开字段并改删除的错误码，C2 做列表排序、时间轴与叫法。两者都改 Host 可见的契约，一起装成 0.50.0；C2 在 C1 封存的输出上开工。两个都不改存储形式：列表的查询本来就先物化全部分组再分页，按创建时刻排序不需要新的索引。

### 微任务 C1：接口剪枝与删除契约

1. **删除中的错误码。** `MACRO_TASK_DELETED` 现在只在删除进行中出现：改名为 `MACRO_TASK_DELETING`，细节里去掉恒为常量的 `state` 与 `deletedAt`；HTTP 映射与控制台的文字随之改。
2. **公开视图里恒定、重复或能推出的字段，从接口删掉**（剪枝各步的审查与交接列出，Host 核实过；[剪枝一的审查](../schema-audit/review-m1.md)、[剪枝二的审查](../schema-audit/review-m2.md)、[验收记录](../../acceptance/schema-audit/pruning.md)）：
   - 微任务的 `acceptanceVerdict`（由 `acceptedAt` 推出），消息的 `correlationId`（恒等于 `inquiryId`）；
   - helper 视图的 `autoContinue`（恒为 false），Host 结论的 `evidence`（恒为空）与 `integrationId`（恒为 null），审计视图的 `spec`（与 `microTask` 读同一份），连同只为它留着、还吞掉解析异常的 `_original_spec`；
   - 冲突的 `delivery`：`state`、`kind`、`turnOutcome` 恒定，`action/artifactId/attemptId/turnId/commandId` 与外层字段重复；
   - 整合：`notRequired` 由 `strategy` 推出；`verification` 里 `verified`、`unrecordedPaths`、`summary` 恒定，`artifactAncestor` 由 `strategy` 推出，`adjustments` 由 `differingPaths` 与 `missingPaths` 推出，`strategy/target/source…/before…/after…` 与外层字段重复；
   - 清理：`retention.commandReceipts` 恒定（回收与清理的回答里同样），`result` 的 `removed` 恒为 true，`path/repositoryId/checkoutId/retention` 与外层字段重复；
   - 选择记录的 `kind`（恒为 `select`）与 `selection.list` 只接受 `select`、什么也不筛的 `kind` 输入；
   - harness 视图的 `available`（等于 `status` 为 `ready`）。Router 健康里的 `available` 是另一回事，不动。
   列表里用来认出一条记录的身份字段不算重复。控制台读到的（例如 `parseIntegration` 读 `summary`）与合成预览随之改。
3. **删除的契约在预览与控制台里一致：** 合成预览删完之后，预览与再删都回答 `NOT_FOUND`，`test_host_preview.py` 里钉着旧行为的断言随之改；两个浏览器同时删除时，后一个得到 `NOT_FOUND`，按已删完处理，不显示成错误。
4. **顺带**（待办“剪枝三、四的独立审查留下的几件小事”里属于这一块的）：`workspace.integration_verify` 没有调用者的 `adjusted_paths` 参数连同分支删掉；`routing/decision.py` 讲 `kind` 过滤的过时说明随 `kind` 一起去掉；`CatalogView` 的 `recorded_at` 参数与两种读法收成一种；`_conflict_view` 对可信证据的 `isinstance` 与 `or []`、`_resolution_delivery_result` 对固定键用的 `.get` 删掉。
5. **版本：** `CONTRACT_VERSION`、钉着它的签名表、`pyproject.toml` 与 `uv.lock` 改为 0.50.0；README 由 Host 改。

**写入范围：** `src/hey_my_buddy/`（不含 `install/`），`apps/console/`，`tests/probes/macro_task_console_preview.py`，`tests/python/`，`pyproject.toml`，`uv.lock`。

**检查：** `npm --prefix apps/console run typecheck`、`npm --prefix apps/console test`、`npm --prefix apps/console run test:browser`；检查运行器跑 `tests/python/blackboard/`、`tests/python/console/`、`tests/python/protocol/`、`tests/python/cli/`、`tests/python/buddy/roles/`。`tests/python/install/test_first_install.py` 核对 README 里的版本，README 由 Host 改，它在 Host 改之前不过。

### 微任务 C2：宏任务列表的排序、时间轴上被改派的尝试与“适应宽度”

1. **排序。** 开发者 2026-10-20 提出：宏任务列表可以按最近更新或创建时间排序，各有升序与降序；默认照旧，按最近更新降序。
   - 排序在服务里做：`macro_task_list` 加排序的键与方向，分页的游标带着排序，换了排序的游标拒绝。宏任务的创建时刻已经存在 `macro_tasks.created_at`，单独的微任务分组用它的 run 的创建时刻。
   - 控制台在列表头上放一个排序控件，选择跟着地址走。
2. **被改派的尝试：** 由服务的宏任务时间轴在片段上标出 `rejected`，删掉微任务页 `TaskDetail.tsx` 里那段前端拼接（待办“宏任务时间轴把被改派的那次尝试画成失败”）。
3. **“适应宽度”：** 统一成一种叫法（待办同名一条）。

写入范围与检查在 C1 合入之后定。

### C2 之后：按创建时间排序时显示创建时间（开发者 2026-10-20 定）

C2 验收时看到：按创建时间排序，每行显示的仍是最近活动的时刻，顺序看上去是乱的。0.50.0 装好之后，开发者定为显示创建时间。

1. 按创建时间排序时，每行的时间是分组的创建时刻（列表回答里已有的 `createdAt`）；按最近更新排序时照旧是最近活动的时刻。读屏文字说明是哪一个时间。
2. 只改控制台，不改接口与存储形式：版本 0.50.1，契约仍是 0.50.0；版本号与 README 由 Host 在验收时改。

写入范围是 `apps/console/`、重建的 `src/hey_my_buddy/console/assets/`、合成预览与 `tests/python/console/`；检查是前端全量、浏览器检查，以及检查运行器跑 `tests/python/console/` 与 `tests/python/cli/test_host_preview.py`。Host 验收时在最终源码上跑前端全量，并在日常看板的副本上看像素。

进度：同日合入为 `bba32d56`，尚未安装，与下面的排序控件重做一起装成 0.50.1。

### 排序控件重做与只有图标的按钮（开发者 2026-10-20 至 21 定）

0.50.0 装好之后，开发者在日常环境里看到 C2 的排序控件做得不好：用的是原生下拉框，弹出系统菜单，选中项是系统的橙色，与控制台里表示等待的橙色撞了；鼠标点完也出焦点框，右边一截被方向框盖住；两个整宽的框单占一行，压在搜索框上面。C2 的说明没有规定形态与位置，Host 验收时看过像素却没有打回。Host 出了设计稿（真实控制台的合成预览上注入），开发者看过之后认可，并提出第 3、4 条；方向用分段控件与选择后浮层不关是 Host 补的细节，开发者没有另选：

1. **排序收成一个图标按钮，**放在“宏任务”标题行右侧，搜索框回到标题下面。图标用通行的排序符号：新的在前时是长短递减的横线加向下的箭头，旧的在前时上下翻转。不是默认排序（最近更新、新的在前）时图标用正文色，悬停提示写出当前怎样排。
2. **点开是控制台自己样式的浮层：**上面两项“最近更新 / 创建时间”，当前项打勾；分隔线下面是方向，用设置页已有的分段控件“新的在前 | 旧的在前”，两种状态都写出名字，不用“升序 / 降序”。选择立即生效，浮层不关。
3. **空出来的位置加“全部折叠”，**图标照 VS Code 的“全部折叠”（两个叠着的方框带减号），把所有项目分组折起来；与 VS Code 一样只做折叠。
4. **只有图标的按钮统一一种样式：**不带框，同一尺寸与同一线宽，平时淡色，悬停时出底色；焦点框只在键盘操作时出现，并且完整画出。设计稿里无框的面板按钮看着比另外两个暗，Host 量过是线宽不同（它是 1.5，另外两个是 2），同一线宽之后一致。这一条用到控制台里所有只有图标的按钮，不只这一行。

只改控制台，不改接口与存储形式；与上面的“显示创建时间”一起装成 0.50.1，版本号与 README 由 Host 在验收时改。写入范围与检查同上一节。Host 验收时在最终源码上跑前端全量与浏览器检查，在日常看板的副本上拍深浅两色的像素，与设计稿并排对照，列出不同之处。

### 会话自动读更早的页并释放（开发者 2026-10-21 定）

开发者在日常使用中嫌会话里要一次次点“加载更早内容”。定下的做法记在 ADR-024 第 2 条的补充里：

1. 滚到顶部附近时自动读更早的一页，不设按钮；每页 256 条，是服务端读取的上限。读取时顶部有一行安静的“正在读取更早内容…”。
2. 离开会话，即切到概述、关掉详情或换一个微任务，就释放已读的页；回来与第一次打开一样从最新处开始，不为恢复旧位置把更早的页重读一遍。原先记在浏览历史里的阅读位置与展开状态随之删除。

只改控制台，与上面两节一起装成 0.50.1。Host 验收时打回过一次：短的运行中会话（没有更早的页）一打开，顶部的触发点就在视野里，把“读者在底部”改成了否，之后新内容不再跟随，而是出“有新内容”；Host 用同一个浏览器检查在基线与候选上对照复现。修正后触发点只在还有更早的页时存在，并补了这一场景与内容只比视口略高的两项浏览器检查；同一轮删掉了位置恢复之后已无人读取的 `data-session-focus`。

进度：2026-10-21 合入为 `69089b62`，尚未安装。

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
