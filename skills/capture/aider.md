# Aider 会话捕获 skill(v0.1)

> 无内置 adapter;接入 LLM 后由 LLM 按本 skill 现场解析。

## SOURCE
~/

## GLOB
**/.aider.chat.history.md

## 已知信息(来源:deja-vu / codecast / context-bridge-cli 等 GitHub 项目)
- aider 把会话历史写在**项目根目录的 dotfile** `.aider.chat.history.md`(不是全局目录)
- 内容是 Markdown,消息之间有分隔;文本经过转义(反引号包裹、行内 code 会被转义),请先还原成人类可读文本
- 角色标记形式随版本变化(常见如 `#### user` / `#### assistant`,也可能用其他标记),**以现场样本为准**
- 每个 `.aider.chat.history.md` 是一个会话;session_id 用文件所在目录名(父目录名)

## 注意
- SOURCE=~ 扫描面很大:先只在用户主目录的 Git 项目根下找,超过 30 个文件就只取前 30 个并注明
- 只输出我要求的 JSON;无法确定的行跳过,不要编造内容
