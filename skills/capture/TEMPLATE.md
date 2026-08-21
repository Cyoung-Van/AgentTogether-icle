# <Agent> 会话捕获 skill 模板(v0.1)

> 复制本文件为 `<agent_type>.md` 后填写。无内置 adapter 的小众 agent,
> 接入 LLM 后由 LLM 按本 skill 现场解析原始会话。

## SOURCE
<原始会话根目录,如 ~/.gemini/tmp>

## GLOB
<相对 SOURCE 的 glob,如 **/*.jsonl>

## 已知信息(来源:注明 GitHub 项目/文档,便于核对)
- 会话文件路径与命名规则
- 每行/每文件的 JSON 结构要点(键名、角色字段、内容字段)
- 哪些是噪音(工具调用、系统、统计)
- 格式随版本变化的地方,标注"以现场样本为准"

## 注意
- 只能输出调用方要求的 JSON 结构;session_id 无法确定时用文件名
- content 只保留人类可读文本;无法确定的行跳过,不要编造
