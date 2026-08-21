# Qwen Code 会话捕获 skill(v0.1)

> 无内置 adapter;接入 LLM 后由 LLM 按本 skill 现场解析。

## SOURCE
~/.qwen

## GLOB
**/chats/*.jsonl

## 已知信息(来源:deja-vu / codecast 等 GitHub 项目)
- 会话在 `~/.qwen/projects/*/chats/` 下的 JSONL 文件
- 每行是一个 JSON 对象;**具体键名(role/content/类型)以现场样本为准**,不要假设
- 目标是提取 user / assistant 的文本轮次;工具、系统、统计类行视为噪音跳过

## 注意
- session_id 用文件名(去扩展名);title 可空
- 只输出我要求的 JSON;无法确定的行跳过,不要编造内容
