# ADR-025 第二步：新格式的真实 ZCode 冒烟

2026-10-06，源码固定为 `ace3e1dbb641005b081718347c7e75dbaa1a851d`（2-C1 已整合，2-C2 在隔离 worktree 实施）。这是本步计划内的新格式真实冒烟，不是微任务执行回合，也不是完整检查。之前 `GLM-5.3` 的不可重试限流记录保留；本次基于 2-C2 路由选中的 `GLM-5.3-Flash` 已实际运行并产生工具活动这一新事实，采用已可用的 `zcode/zai-api/GLM-5.3-Flash/max` 做最小核对，没有重试受限的 GLM-5.3。

使用已安装 ZCode `0.16.9`，真实模型调用 **1 次**，没有纠正重跑；程序还执行原有版本与能力检查。通过公共 `FastPreparation → start_router_preparation → roles.run_controller → registered native_run.run` 路径运行，工具范围 none，输入仅要求返回 `{"answer":"ADR025_OK"}`，原生总期限 60 秒。控制器实际将 RunRequest 编码到私有文件并重新解码，把原生结果编码到 stdout；持有方按当前模型解码并核对完整执行身份。本次没有黑板任务凭据或会话工具服务。

结果 status=ok，进程退出码 0，模型已开始，实际答案与给定结构、指定值均相符。请求和原生核对配置一致，纠正次数 0，原生事件计数 11；已记录 toolAllowlist=[]、titleGenerationEnabled=false、streamEof=true，工具调用为 0。RunResult 的原生 group_state=gone，持有方确认控制器停止，组合 shutdownConfirmed=true；总耗时 8.718 秒。此核对证明真实无工具运行经过新请求/结果，不据此声称只读、完成工具、续接、活动或问询的原生冒烟也已执行。

请求帧 SHA-256 为 `c32ac42bffb79e79640777b16446ff4fe2f04ae103fb6bcef1f99169475cfb86`，结果帧为 `c33cc98c86393916a58a458caae37f044d85df9caaeafd4bb8f34ad79130eb10`。脚本、运行摘要与两份非秘密帧保存在本步被忽略的 `tmp/`，其中源码为该固定 Git 提交的独立副本，解释器只读复用实施检出的锁定环境。没有安装或升级运行时、修改用户配置或登录，也未由 Host 打开凭据文件内容；原有运行路径使用自己的私有 provider 快照、会话数据库、存储与日志，关闭遥测。临时运行目录在创建时登记于 Host 已有任务根内部，未手动删除，后续按确切根回收；包含私有 provider 快照的运行目录不作为公开产物归档。
