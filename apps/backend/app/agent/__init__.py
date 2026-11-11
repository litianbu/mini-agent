"""agent 核心：与 HTTP、与数据库都无关。

这是整个项目最值得读的部分。它可以被搬去任何地方运行 ——
CLI、Web、甚至一个定时任务 —— 因为它只依赖两样东西：
    输入：messages
    输出：AsyncIterator[AgentEvent]
"""
