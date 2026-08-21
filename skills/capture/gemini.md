# Gemini CLI 会话捕获 skill(v0.1)

> 无内置 adapter;接入 LLM 后由 LLM 按本 skill 现场解析。

## SOURCE
~/.gemini/tmp

## GLOB
**/*.jsonl

## 已知信息(来源:deja-vu / codecast / collector 等 GitHub 项目)
- 会话文件在 `~/.gemini/tmp/<project>/chats/` 下(也可能散在更深的子目录),`.jsonl` 为主,个别 `.json`
- 每个文件是一个会话;文件名可能是 UUID 或可读名
- 每行可能是 JSON 对象,字段如 `id` / `messages` / `role` / `content` / `text` / `timestamp`,但**具体键名请以现场样本为准**,不要假设
- 目标是提取 user / assistant 的文本轮次;工具调用、系统、统计类行视为噪音跳过

## 注意
- 只能输出我要求的 JSON 结构;无法确定的 session_id 用文件名(去扩展名)
- content 只保留人类可读文本,去掉 JSON 外壳
