# AgentTogether 本机部署基线记录

## 2026-09-06（Asia/Shanghai）

### 结论

依赖就绪，前端构建及空间测试通过，现有服务的健康检查和鉴权拒绝检查通过；**后端完整测试未通过，不能宣称原有功能全部正常或基线验收完成**。未改造星系界面。

### 仓库与工作环境

- 仓库：https://github.com/Cyoung-Van/AgentTogether-icle
- Linux 文件系统路径：`/home/administrator/projects/AgentTogether-icle`。
- 本轮开始时目录已经存在，分支 `dev/windows-stellar-ui` 已建立；直接复用，没有重新克隆、删除或覆盖目录。
- 提交：`c8bdc28e35bcccfa8c0c78a60016baf524dc03cd`。
- 提交说明：Complete hierarchical 3D galaxy navigation and workspace sections。
- 本地 `main`、`origin/main` 与开发分支指向同一提交；`origin/HEAD` 指向 `origin/main`。
- 初始工作区干净。应用源码、测试、依赖锁文件均未修改；本轮仅新增本记录，依赖和构建产物属于已忽略目录。没有对本项目执行 commit 或 push。
- Ubuntu 24.04.4 LTS，WSL2，内核 `5.10.16.3-microsoft-standard-WSL2`；`uname -s` 为 Linux；Git 2.43.0（`/usr/bin/git`）。项目命令均在上述仓库或其 `web` 目录中执行。
- 已读 README.md、requirements.lock、web/package.json、web/package-lock.json、web/vite.config.ts，并检查测试中的外部执行路径。
- 根目录及已检查的父目录没有 AGENTS.md；仓库中没有 AgentTogether_Windows_Deployment_Plan.md，未假定其内容。部署隔离要求采用本次用户提供的说明。

### 实际工具版本

| 工具 | 版本 | 路径 / 说明 |
|---|---|---|
| Python | 3.11.16 | `.venv/bin/python`，实际解释器 `/home/administrator/.local/share/uv/python/cpython-3.11.16-linux-x86_64-gnu/bin/python3.11` |
| pip | 26.2.1 | 项目 `.venv` 内 |
| uv | 0.12.10 | `/home/administrator/.local/bin/uv`，Linux x86_64 |
| Node | 22.23.2 | `/home/administrator/.nvm/versions/node/v22.23.2/bin/node`，process.platform=linux |
| npm | 10.9.8 | 同一 nvm 版本目录的 npm |

上述工具和 `.venv` 已存在，未重复安装解释器或工具链。当前非交互终端的默认 PATH 会找到 Windows npm，且没有直接可用的 Linux node/python；执行前端命令时显式加载 `~/.nvm/nvm.sh` 并 `nvm use 22`，后端始终使用 `.venv/bin/python`。后续新终端也需要加载 nvm。未使用 Windows Node 或 Python。

### 逐项验证

| 检查 | 结果 |
|---|---|
| pip install --require-hashes -r requirements.lock | 通过；17 个锁定包均已满足，未更换版本；附加禁用版本检查和网络超时参数。已有包未重新下载，所以不把此结果描述为对已有安装内容重新做哈希审计。 |
| pip check | 通过，No broken requirements found |
| Python / fcntl 导入 | 通过，解释器位于 Linux 项目虚拟环境 |
| PYTHONPATH=src .venv/bin/python -m icle serve --help | 通过；支持 --host、--port、--store、--capture-store |
| npm ci --prefer-offline --no-audit --no-fund --fetch-retries=0 --fetch-timeout=20000 | 通过，安装 44 个包；利用缓存，未执行 npm audit，此项不代表安全审计通过 |
| package.json 与 package-lock.json 根依赖对应 | 通过 |
| npm run lint | 退出码 0，有 1 条既有警告：web/src/i18n.tsx:1825，react(only-export-components) |
| npm run test:space | 29/29 通过，0 跳过 |
| npm run build | 通过，Vite 8.2.1；产物写入 web/dist；主 JS 约 1125.71 kB，超过默认 500 kB 的块大小警告阈值，未改阈值或拆包 |
| Python unittest 全量 | 496 项，474 项通过，6 项失败、16 项报错，退出码 1，运行约 11.167 秒 |

后端实际测试命令：

```bash
env -u ICLE_TOKEN -u ICLE_STORE -u ICLE_CAPTURE_STORE \
  PATH=/home/administrator/projects/AgentTogether-icle/.venv/bin:/usr/bin:/bin \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -q
```

测试使用临时数据、假执行器、mock 或本地模拟接口；没有启动真实 Agent 任务或调用付费 Provider。测试中的临时 Git 仓库会自行创建测试提交，不涉及本项目提交。出现失败后未修改代码、测试或环境中的真实模型配置以制造通过。

### 失败项与只读诊断

1. **基线模型探测：3 failures + 16 errors。**
   - `test_baseline.BaselineTargetTests.test_model_without_a_connected_provider_cannot_be_probed` 得到 `agent_model_unknown`，预期 `no_connected_provider_serves_this_model`。
   - `BaselineProbeTests` 和继承它的 `RebuildCStateTests` 中 `test_target_resolves_to_the_configured_provider_model` 失败。
   - 两个类中的以下 7 项均报 `BaselineError: cannot probe kimi: agent_model_unknown`：`test_a_transport_failure_is_not_recorded_as_a_model_failure`、`test_one_probe_is_not_enough_for_a`、`test_probe_evidence_never_enters_the_agent_subject`、`test_probe_records_the_bare_model_form`、`test_probe_refuses_a_spec_it_cannot_match`、`test_probe_skips_a_task_the_agent_never_froze`、`test_two_probes_calibrate_a_and_c`。
   - `RebuildCStateTests.test_rebuild_keeps_the_matrix_readable` 和 `test_rebuild_retires_the_chain_and_replays_current_evidence` 同样报错。
   - 代码证据：`src/icle/baseline.py` 通过 `resolve_model_identity` 识别模型；`src/icle/external_evaluation.py` 从本地配置、显式绑定或已观察记录解析身份。测试虽然写入假 Provider，但没有独立建立 Kimi 身份绑定，假定模型自动已知。初步分类为既有测试对本机配置的依赖 / 空环境可重复性问题，不是缺少 Python 依赖。未读取旧机器数据或为此配置真实 Provider。
2. **报告传递：1 failure。**
   - `test_report_pipeline.ReportPipelineTests.test_j_reaches_the_decision_layer`：预期 J 有值，实际为 None。
   - 测试查询固定 `kimi-for-coding` 身份，而当前模型身份未解析；从读代码看，可能与接受任务时写入的身份不一致。此为待修复验证的初步诊断，未声称完成根因修复。
3. **回放建议：2 failures。**
   - `test_router_active_collab.ActiveReplayTests.test_budget_caps_suggestions`：预期 1 条，实际 0 条。
   - `test_suggest_targets_evidence_gaps`：预期非空，实际空列表。
   - `src/icle/active.py` 从既有证据和可解析的本机 CLI 建候选池，不满 2 个就返回空；这些测试只准备一个 Agent 的证据，没有注入第二个候选。测试进程使用 Linux 最小 PATH，避免触及真实 Agent。初步分类为测试依赖本机 CLI 环境，不能靠安装 / 执行真实 Agent 解决。
4. 测试另有 Starlette 使用 httpx TestClient 的弃用警告；本轮遵守锁文件，未迁移至 httpx2。

### 远程同步与环境问题

- WSL `git ls-remote` 无法完成；有界重试 25 秒超时，首次挂起查询已终止。
- IPv4 HTTPS 探测 GitHub 443 连接在 10 秒超时；域名解析为 `198.18.0.32`。属于当前网络连通性问题，未改 DNS、代理、安全设置或 Git 配置。
- 网页检索能找到该公开仓库，但页面结果为旧抓取，不能用来证明最新远程提交。因此“当前提交仍为远程最新版本”待网络恢复后核验。
- 系统缺少 `ss`；用 Python socket 完成端口探测，无须为此安装系统软件。

### 已运行服务检查

本轮未新启动、重启或停止前后端长期服务。发现独立终端已有服务：

- 后端：Linux Python，仓库根目录，127.0.0.1:8000。
- 前端：Linux nvm Node，仓库 web 目录，127.0.0.1:5173。
- 后端 `--store` 为 `/home/administrator/.local/share/agenttogether/dev/experience-store`。
- **现有后端 `--capture-store` 参数末尾实际含回车字符 `\r`**，即 `capture-store\r`，与预期 `capture-store` 不是同一路径。只检查了进程参数，未读取、移动或删除该目录的数据。用户应在原终端停止后端并重新手工输入下面的干净启动命令；本轮未替用户重启。

实测：

| URL / 条件 | 结果 |
|---|---|
| http://127.0.0.1:8000/api/health | 200，status=ok，version=0.1.0，episodes=0 |
| http://127.0.0.1:5173/api/health | 200，相同健康信息，Vite 代理连通 |
| http://127.0.0.1:8000/api/intelligence-status，无 Token | 401 |

以上是 WSL 内检查。未读取或输出本地 Token。Windows 浏览器自动化工具初始化失败：sandboxCwd is not a local file URI；因此未完成 Windows 浏览器页面、保存 Token、功能页及前进返回验收，不把健康检查等同于完整浏览器验收。

### 下一步启动 / 验收条件

当前端口已有服务，不要重复启动。后端先修正上述路径末尾回车问题；前端继续保留原终端。后续需要重新启动时，使用独立 Ubuntu 终端。

后端（已有非空本地 Token 文件时）：

```bash
cd /home/administrator/projects/AgentTogether-icle
source .venv/bin/activate
export ICLE_TOKEN="$(cat "$HOME/.config/agenttogether/dev.token")"
PYTHONPATH=src python -m icle serve \
  --host 127.0.0.1 \
  --port 8000 \
  --store "$HOME/.local/share/agenttogether/dev/experience-store" \
  --capture-store "$HOME/.local/share/agenttogether/dev/capture-store"
```

若 Token 文件不存在 / 为空，先按用户原始说明创建隔离目录及本地 Token；不要把内容发到聊天或写入报告。保留已有非空 Token。

前端（仅在原前端终端已停止时）：

```bash
cd /home/administrator/projects/AgentTogether-icle/web
source "$HOME/.nvm/nvm.sh"
nvm use 22
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Windows 浏览器打开 http://127.0.0.1:5173。用户自行在本地输入已有 Token，验证功能页、前进返回和无持续 API 错误。首页未出现口令框时进入数据功能页触发 401 提示；代码中以 X-ICLE-Token 发送保存的本地口令。

下一轮在允许修改测试 / 源码后，先解决上述 22 个失败或报错用例并重新验收；本轮不进行视觉改造、不升级框架、不重写锁文件、不迁移真实数据、不配置真实 Provider、不建公网隧道。

## 2026-09-06 21:18 +08:00 — 环境修复与基线复验

本节更新前面的未完成状态：**网络和运行环境问题已修复，后端 496 项测试全部通过**。仍未修改星系界面、应用业务源码或依赖锁文件；未 commit / push。

### 本轮已解决

1. **Linux Node/npm 加载**：原 `.bashrc` 在非交互终端提前返回，导致 `.profile` 没有加载 nvm。已为 `.profile` 补充受条件保护的 nvm 初始化；新登录终端自动使用 Linux Node 22.23.2 / npm 10.9.8，不再误用 Windows npm。Python 仍通过项目 `.venv` 使用，不改系统 Python。
2. **WSL 网络**：Windows FlClash 正常运行，但代理仅监听 Windows `127.0.0.1:7890`；WSL DNS 返回代理 Fake-IP，直接访问 GitHub/npm/PyPI 会超时。真实 GitHub 地址直连可以访问，确认是代理/DNS路径问题。
   - 增加一条 Windows TCP portproxy：`172.28.176.1:17890 -> 127.0.0.1:7890`，仅绑定 WSL 虚拟网卡地址，没有绑定 0.0.0.0、局域网实体网卡或公网地址。
   - 没有关闭防火墙、修改安全规则、变更系统 DNS 或建立公网隧道。
   - Linux `~/.config/agenttogether/network.env` 保存 HTTP/HTTPS 代理环境，localhost / 127.0.0.1 / ::1 / WSL 网关加入 NO_PROXY；`.profile` 和 `.bashrc` 加载该文件。
   - 新增 `~/.local/bin/agenttogether-network`，用于 WSL 网关变化后重新建立专用映射并验证代理。遇到同端口不同目标的既有映射会停止，不覆盖其他规则。
   - GitHub、npm registry、PyPI 索引 HEAD、files.pythonhosted.org HEAD 均为 200。首次以 GET 下载整个约 45 MB PyPI 索引在收到 200 后超出 20 秒下载限时；改用 HEAD 进行连通性验证通过，此非依赖安装失败。
3. **远程版本核对**：恢复网络后 `git ls-remote --symref` 成功，GitHub 默认分支为 main，远程 HEAD 及 main 均为 `c8bdc28e35bcccfa8c0c78a60016baf524dc03cd`，与本地开发起点一致。不需要合并或覆盖工作区。
4. **后端路径**：核对旧进程确属该仓库后正常终止，复用已有本地 Token，以正确的 `capture-store` 路径重新启动；参数末尾的回车已消除。原错误目录未删除或迁移。当前后端由本次任务启动并持续运行，127.0.0.1:8000；原前端继续运行于 127.0.0.1:5173。

### 测试环境依赖修复

只修改以下 3 个测试文件，总计 20 行新增、3 行删除：

- `tests/test_baseline.py`：在临时 store 内显式绑定假的 Kimi 模型身份，mock 掉开发机本地配置读取并注册清理。保留未知模型、未连接 Provider、模型一致性等所有断言。
- `tests/test_report_pipeline.py`：使用与查询 ROUTE 相同的临时模型绑定，隔离本机配置读取；保留 J 传递的原有断言。
- `tests/test_router_active_collab.py`：两项建议排序/预算测试显式传入两个候选 Agent，不再假定本机安装了额外 CLI。候选只是算法输入，没有真实执行。

没有删除测试、跳过失败项、降低断言、伪造业务返回值，也没有为测试配置真实 Provider。没有修改 `src/`、`web/` 或锁文件。

复验命令与上轮相同，使用 Linux 最小 PATH、移除 ICLE_TOKEN/ICLE_STORE/ICLE_CAPTURE_STORE 的继承值：

```text
Ran 496 tests in 11.882s
OK
```

前端本轮没有变更；沿用上一轮已通过的 29 项空间测试、lint 和 build 结果，未无理由重复安装和构建。Starlette/httpx 弃用警告、前端 Fast Refresh 警告和构建体积提示仍保留，均不阻塞当前启动。shell 配置及网络恢复脚本语法检查、git diff --check 均通过。

### 运行验证

| 检查 | 结果 |
|---|---|
| WSL 后端 `/api/health` | 200，status=ok，episodes=0 |
| WSL 前端 `/api/health` 代理 | 200，status=ok，episodes=0 |
| 前端代理受保护接口，无 Token | 401 |
| 前端代理受保护接口，复用已有本地 Token | 200；仅程序内用于本地请求，未输出 Token 或接口正文 |
| Windows 自带 curl 访问 `http://127.0.0.1:5173/` | 200 |
| Windows 自带 curl 访问前端 `/api/health` | 200，status=ok，episodes=0 |

Windows 主机至 WSL 网页服务的 HTTP 路径已确认。**浏览器渲染、用户输入 Token、功能页操作及前进返回仍未手动验收**；之前桌面自动化工具的路径错误属于工具问题，本轮没有通过更改本机系统配置绕过。

### 后续使用与恢复

- 现在可以从 Windows 浏览器打开 http://127.0.0.1:5173。原前端继续运行，后端已由本次任务重启，无需再启动一份。
- 网络通路依赖 Windows FlClash 继续运行并监听 7890；普通项目启动和离线测试不需要公网服务。
- 已打开的 Ubuntu 终端若仍无代理，执行：`source ~/.config/agenttogether/network.env`。新终端自动加载。
- WSL 关闭/重启后若虚拟网卡网关发生变化，执行 `~/.local/bin/agenttogether-network`，成功后再加载上面的 env 文件；新增 Windows 映射需要当前 Windows 会话具有管理员权限。脚本仅添加当前 WSL 地址上的专用映射，不清理其他映射。FlClash 代理端口若改变，应先核对端口再调整脚本。
- 原 profile/bashrc 备份位于本次任务 `work/environment-backups/`。环境配置位于用户目录，未混入应用代码。
- 撤销本次网络映射（当前地址）可运行：`/mnt/c/Windows/System32/netsh.exe interface portproxy delete v4tov4 listenaddress=172.28.176.1 listenport=17890`，并移除 `.profile` / `.bashrc` 中 AgentTogether network.env 加载块。不要删除其他映射或整个 shell 配置。

到此可作为后续开发的已通过自动检查起点；完整用户界面验收仍需完成后再开始视觉改造。

## 2026-09-06 — C「极简天体馆」视觉应用

已将用户选择的 C 风格应用到实际 `web/` 前端，而非 5174 概念预览。

- 默认浅色；工具栏新增浅色/深色按钮，选择保存在独立的 `agenttogether.theme` 本地键中，刷新后保留。没有主题偏好时，即使操作系统为深色，也默认浅色。口令存储键和鉴权代码未改。
- 统一背景、面板、表单、菜单、代码输出、状态色和焦点颜色；浅色采用白色/浅灰与蓝色强调，深色采用石墨灰/银白。原有表单、列表、工作区布局和功能保留。
- 天体改为柔和的珍珠/银白/浅蓝材质，降低金属反光、发光与背景粒子强度。仅更新现有场景材质、颜色和灯光，不重建场景。
- `web/src/theme.ts` 是唯一新增的前端模块；其余前端修改在共享样式、工具栏、主题初始化及 Three.js 视觉代码中。
- `src/` 后端、`web/src/api/`、`web/src/pages/`、路由定义、SpaceNav、hierarchy、orbits、motion 和依赖锁文件均未修改；本轮没有真实任务执行、Provider 修改、数据迁移、commit 或 push。此前已存在的 Python 测试修复继续保留。

验证：

- `npm run lint` 通过，仅有原有 i18n Fast Refresh 警告。
- 最终 `npm run test:space`：29 项全部通过，0 跳过。
- 最终 `npm run build` 通过，更新 `web/dist`；原有大文件体积提示仍存在。
- 专项场景检查：主题切换前后保持相同网格、材质、纹理对象，以及相机、轨道、路由和进行中的转场状态。
- Windows Edge 浏览器连接真实 `http://127.0.0.1:5173/` 验证：默认浅色、深浅切换、刷新保留、暂停后的天体 UUID/轨道时间保持、键盘进入主功能和任务页、返回及浏览器前进返回。
- 只读检查 `/today`、`/tasks`、`/episodes`、`/agents`、`/providers`、`/cost`、`/settings#general` 共 7 个工作区；实际后端响应正常。
- 390px 手机窄屏无横向溢出，主题按钮可见可用。
- 该浏览器检查中 JS 错误为 0、失败 API 响应为 0、API 写请求为 0。现有开发 Token 只在临时浏览器会话内用于本地鉴权，未写入报告或输出。
- `git diff --check` 通过。后端代码没有变化，本轮未重复执行已通过的 496 项后端测试。

当前实际应用：http://127.0.0.1:5173/ 。5174 仍为此前概念预览，不代表实际服务。

## 2026-09-06 — 修复 Token 保存无反馈

用户反馈在 `http://127.0.0.1:5173` 点击保存后按钮没有反应、页面未刷新。本轮按该问题扩展到前端鉴权交互修复；后端鉴权规则未放宽。

现象诊断：旧点击处理直接写 localStorage 并刷新，没有异常处理，也没有验证候选口令。存储写入异常会中断处理而无界面反馈；这类失败已通过浏览器模拟复现并验证修复，但尚不能证明用户当前内嵌浏览器一定是该原因。

改动：

- 按钮改为“验证并保存”，支持 Enter 提交和验证中状态。
- 通过只读受保护 `/api/intelligence-status` 验证口令后再保存；空输入、字符异常、不匹配、网络失败、异常服务响应都有明确提示，不覆盖原有效口令。
- 保存后刷新活跃查询，不再刷新整个页面；旧请求迟到的 401 不会在新口令保存后重新弹出口令栏。
- 浏览器拒绝存储或静默丢弃写入时，使用当前页内存暂存已验证口令，显示“当前窗口连接，刷新需重输”；不会谎称持久保存成功。
- 读取受限或格式损坏的旧存储也会正常引导输入，不再因未捕获存储异常而失效。
- 所有口令验证只发往本地原有接口，没有输出真实口令，没有关闭鉴权、改数据目录或调用真实 Provider。

验证：

- 新增 `web/tests/auth.test.mjs` 和 `npm run test:auth`，12 项测试全部通过；使用模拟数据验证成功/失败、存储异常、超时、异常 JSON/HTML、旧 GET/POST/DELETE 的迟到 401，以及当前口令失效时仍然提示登录。
- Windows Edge 实际 5173：空输入/错误口令/非法字符/网络失败均有可见反馈；Enter 提交正确本地口令成功；无整页刷新；正常存储刷新后继续登录；模拟禁止口令存储时当前页登录正常，无未捕获 JS 错误。
- lint 通过（仅原有 i18n Fast Refresh 警告），build 通过（原有大包提示保留）。无依赖版本或锁文件变更。

## 2026-09-06 22:35 +08:00 — 恒星系 / 行星系空间与转场升级

按用户最新要求扩展视觉范围：加入真实三维轨道及相机旋转推进，替换一、二级之间的透明度淡出方案。实际地址仍为 http://127.0.0.1:5173/ ，浅色默认、深浅切换及此前口令修复保留。

### 本轮实现

- 主页以暖白恒星为中心、主功能为行星；第二级显示所选行星及其功能卫星。增加实际轨道线与窄轨道面、不同倾角、真实深度遮挡及近大远小。
- 目标行星保留同一网格、世界坐标和物理半径。第二级围绕它建立更小的本地卫星系统，放大来自相机距离变化，而非把行星尺寸动画拉大。
- 一级进入二级采用绕目标的三维旋转加推进。根据目标与恒星的位置计算终点角度，恒星始终保持不透明、位置不变，最终被真实相机视锥排除。父级恒星没有参与透明度动画。
- 返回时相机反向绕行并恢复父级视角、轨道时刻和目标位置。旧卫星细节在返回恒星尺度后清理，避免它们停留在旧世界位置、与恢复运行的行星脱离。
- 朝向使用随视线旋转的相机坐标系，修复特定竖屏角度出现的相机滚转翻转。
- 新增 SystemVisuals：轨道与天体共享几何参数；父级轨道作为背景参考降低强度，当前系统轨道保持清晰，切换与销毁管理 GPU 资源。
- 新增 planetSurface：确定性的球面冰层/地貌/气态纹理和中性陨石坑卫星纹理，无外部图片或依赖。恒星使用暖光照明和专用表面，加入电影式色调映射避免高光过曝。
- 工作区业务页面、后端接口、数据与鉴权规则未改变；本轮没有真实 Provider 请求、任务执行、依赖升级、commit 或 push。
- 遵循系统“减少动态效果”设置时保留直接导航，不强制播放飞行。

### 验证

- `npm run test:space` 现包含原空间测试及新增飞行测试：59 项全部通过，0 跳过。
- 新增的 30 个飞行案例覆盖 5 个主页入口 × 桌面/竖屏 × 3 个轨道时刻；独立计算 Three.js 视锥与真实球体，验证恒星 opacity=1、出画后才可隐藏、行星尺寸/坐标不变、镜头旋转和推进、视角连续、返回与缓存清理。
- 原测试中“前 1/3 转场保持相机距离”和“第二级中心球增长至固定大尺寸”改为新的物理飞行约束；目标对准、相机不穿越表面、碰撞、完整轨道可见性、标签可访问性和导航断言保留。
- `npm run test:auth`：12 项全部通过。本轮合计 71 项前端测试通过。
- lint 通过（仅已有 i18n Fast Refresh 警告），最终 build 通过（原有大包提示仍保留），git diff --check 通过。
- Windows Edge 连接真实 5173：5 个入口进入/返回均成功；实测父恒星离开视锥时仍不透明，横竖屏、浅深色和进入原任务工作区正常；JS 错误 0、失败 API 响应 0。
- 浏览器检查使用独立会话，只访问现有只读 API；真实开发 Token 没有输出。

已保存实际页面截图和四个转场阶段截图到本次任务 outputs，便于比较恒星系、行星系和镜头经过画面边缘的过程。

## 2026-09-06 — 轨道视觉改为渐隐轨迹

针对“轨道线缺少星际感”的反馈，本轮只更改轨道表现，不改天体轨道、相机飞行、路由或业务接口。

- 去掉完整闭合圆环及环形条带，改成约 100–128° 的开放弧段，尾部自然消散，边缘柔化。
- 使用相机空间深度控制明暗，近处略清楚，远处更淡；浅色为淡灰蓝，深色为克制的冰蓝。
- 每条轨迹附少量不闪烁的细尘点，没有新增独立旋转或装饰动画。
- 轨迹读取天体实际 orbitTime、orbitScale 与 opacity，暂停和返回恢复跟随原有状态，不自行计时。
- 进入第二级时隐藏父级辅助轨迹，消除此前横穿整屏的背景斜线。恒星和行星本体、旋转推进与实体出画逻辑保留。
- Shader 与几何均纳入原有资源释放流程，无新增依赖或外部资源。

验证：59 项空间/飞行测试通过；lint 通过（原有 i18n 警告保留）；Windows Edge 实际 WebGL 渲染、深浅色、恒星系/行星系、手机和返回检查通过，无浏览器或着色器错误。更新后的实际截图保存在 outputs/orbital-trails-*.png 与 moon-trails-*.png。

## 2026-09-06 — 修复进入“今天”后的多余星体

已在真实 5173 页面复现并截图：进入“今天”后，背景中无标签的额外星体是主页的 `settings` 行星。它被加入 `departures` 参与转场，但旧收尾逻辑只在返回主页时清理退场层，进入行星系时没有清理。

修复：进入和返回行星系结束时均隐藏退场层天体并清空该层，保留 bodyCache 供返回复用；不修改天体材质、透明度或原来的旋转推进路径。调试状态增加实际可见天体 ID，避免只检查当前节点列表而漏掉仍在绘制的缓存对象。

回归验证：现有 30 个飞行案例增加“整个缓存中的可见对象只能属于目标系统”的断言；完整空间测试 59/59 通过，构建及 diff 检查通过。实际浏览器鼠标进入“今天”后确认只显示 today、today-overview、today-needs、today-active、today-resources 共 5 个天体；切换深浅色仍为 5 个，返回主页恢复 6 个，再次进入仍为 5 个，无 JS 错误。恒星保持不透明、离开视野后再隐藏的既有断言仍通过。

复现与修复截图：outputs/today-before-cleanup.png、outputs/today-after-cleanup.png。

## 2026-09-07 — 撤回进入末尾隐藏，改为动态镜头角度

按用户要求，已撤回上一次新增的 `enter-galaxy` 完成时隐藏父级星体、清空 departures 的逻辑。父级恒星及其他主页行星在进入过程中和完成后均保持 group.visible=true、opacity=1，继续存在于世界坐标中。原先返回主页时处理局部卫星细节的逻辑未扩展；没有新增透明度或强制隐藏兜底。

新增 departureView.ts：根据全部父级球体的位置、半径、镜头宽高比和视野角扫描旋转方向，优先选择留有边缘余量、改动较小的干净构图。替换只围绕恒星计算的固定旋转角度，不改变目标行星尺寸或主轨道。

验证：
- 59 项空间测试通过；30 个飞行案例现在逐帧验证所有离场父级星体均未隐藏、未淡出，并独立确认这些样例最终全部离开相机视锥。
- 实际 Windows Edge 在 5173 通过鼠标进入今天，确认 home、task-studio、agents、sessions、settings 全部仍启用且不透明，但均在视锥外；整段进入轨迹没有改它们的可见标志。返回、再次进入通过，无浏览器错误。
- 构建、lint、diff 检查通过；原有 i18n 和大包警告保留。
- 实际截图：outputs/today-camera-angle.png。
- 环境切换后已恢复原有 8000 后端与 5173 前端，复用原开发数据目录和 Token，没有输出口令。

边界：另做 1510 个覆盖 30 分钟轨道时间的静态取景采样。某些极近邻重叠位置在固定镜距、固定视野且目标居中的约束下无法仅凭角度排除所有背景球体。当前实现选择最大几何余量，绝不改用强制隐藏；未宣称这些扩展取景样例全部通过。如后续要求所有时刻均排除背景天体，需要继续联合规划镜距与构图，而非重新加回隐藏补丁。

## 2026-09-07 — 定位并优化层级切换停顿

通过 Windows Edge 真实 5173 页面收集 requestAnimationFrame 间隔与 CPU 采样，区分渲染停顿和布局跳变。

测得两个问题：
- 首次进入约 50% 进度时才创建下一级纹理和材质并首次上传 GPU。本次首次进入最长动画帧 25.1 ms，再次进入已有缓存时最长约 4.3 ms；采样包含 createPlanetSurface / makePlanet 等热点。
- 860px 窗口中，路由切换后的工具栏换行使画布高度从 742px 变为 721px，切换结束产生一次重新构图。此为视觉跳变，不只是 GPU 帧时间问题。

实现：
- 使用浏览器空闲时段逐个预生成未进入系统的卫星纹理和材质，提前上传纹理并准备着色器；悬停/聚焦的目标优先。每次只处理一个资源，转场进行时暂停后台准备。
- 仅预先准备尚未显示的卫星资源；没有恢复进入末尾强制隐藏父级星球的逻辑。原有相机角度、推进路径与父级实体保留规则不变。
- 激活资源时复用网格和纹理并应用当前实际尺寸；取消剩余空闲任务并统一释放资源，避免卸载后的后台工作或泄漏。
- 转场中不再计算原本不可见的 DOM 标签布局；正常显示时先批量读尺寸再写样式，减少重复布局。
- 开发模式转场轨迹记录限制到约 60 次/秒，避免高刷新率下无必要的调试数据开销。
- 中等宽度窗口固定两行工具栏和导航高度，避免进入第二级才改变画布高度。

复验：
- 本次 1360px 测试首次进入最长动画帧约 8.4 ms，重复进入约 4.3 ms；转场中的 CPU 采样不再出现纹理生成热点。
- 860px 首次/重复进入最长动画帧均约 4.3 ms；切换前后画布高度保持 705px，结束后每帧相机位移约 0.00007 世界单位，未再出现之前的布局重算跳变。
- 上述为本机自动化采样，不代表所有设备或负载的性能保证。
- 新增 2 项资源预热回归测试，验证可见系统不变、资源复用、飞行期间不执行后台准备、卸载取消及资源释放。空间测试共 61 项通过；lint、build、diff 检查通过，原有 i18n 和体积提示保留。
- 后端、Provider、业务接口、数据与鉴权规则未变；没有真实 Agent 执行或付费 API 调用，没有输出 Token。

## 2026-09-07 — 背景纵深与整体构图细化

在用户认可主体系统后，本轮只调整视觉层次与空间分配，保留已有轨道、相机路径、动态角度取景、资源预热、鉴权和业务功能。

- 宽屏改为左侧说明、右侧星系的构图，说明区垂直接近主体；提高标题层级并整理说明文字和暂停按钮，消除标题与主体脱节的上方空区。
- 窄屏与手机保持紧凑上下布局；英文手机标题预留额外高度，保证文字不覆盖星系。
- 所有星系层级使用相同画布边界，不因进入第二级修改画布大小；保留前一轮防止工具栏换行引起跳变的规则。
- 新增 DepthBackdrop：432 个微粒分布在三个远景深度范围，配合 8 个柔和的远景雾区；相机运动产生真实视差，没有新增动画循环。
- 全页增加克制的冷暖明暗层次：浅色仍为银白/浅蓝灰，深色为蓝炭色。功能工作区背景降低强度，保证数据和表单可读。
- 仅对远景雾的画布边缘做柔和融合，消除矩形接缝；不对天体或整个画布做透明遮罩，不改变恒星/行星的实体出画规则。
- 为中心标签增加避开附近天体的候选位置，避免小屏幕中文字压在卫星上。
- 关闭旧远星层的重复显示，背景资源固定创建并统一释放，无新依赖、网络图片或真实 Provider 调用。

验证：
- 61 项空间与飞行/预热测试通过；lint、build、diff 检查通过，已有 i18n 与包体积提示保留。
- Windows Edge 实际 5173 覆盖 1440、1024、860、390、320px 五档窗口；深浅色、主页/第二级、英文手机及工作区均验证。标题与画布无重叠、无横向溢出；主页到第二级的画布边界完全一致，无 JS 或 WebGL 错误。
- 本机 1360px 帧时间复测：首次/重复进入最长动画帧均约 4.3ms；此为本次采样结果，不作所有设备性能保证。
- 实际截图：outputs/composition-light-1440.png、composition-dark-1440.png、composition-light-390.png、composition-dark-390.png、composition-planetary-dark.png。

## 2026-09-07 — 增大星系占比，在中央星体上显示信息

按用户“在星体上加入”的明确要求，信息直接投影在中央星体表面，不新增旁侧面板。

- cameraDistance 改为按真实倾斜轨道的全相位投影包围范围取景，保留标签和天体边缘余量。桌面主页线性占比约增大 15.3%，4 卫星系统约增大 11.7%；窄屏主页约增大 8.9%，紧凑系统按安全边界适配。未改变天体物理半径、轨道、FOV 或转场算法。
- 新增 SystemInfo：主页恒星显示真实导航树中的主功能数量；第二级中央行星显示名称、工作入口数量及已有智能层状态。未知或不可用状态明确标注，不虚构任务数、费用或其他数据。
- 状态通过 enabled:false 订阅已有 intelligence-status 查询缓存，没有增加轮询或新的后台接口请求。
- 信息按中央天体实时投影定位和实际屏幕尺寸排版；深浅色使用可读对比，保留地貌和光照。转场及路由内容未同步完成时暂不显示信息，避免错层文字闪现。
- 信息层 pointer-events:none，保持点击星体返回的既有操作。外侧重复名称收起，保留当前位置/返回提示。

验证：61 项空间/飞行/预热测试通过，lint 与构建通过（原有警告保留）。Windows Edge 实际 5173 覆盖1440、860、390、320px：文字中心与真实3D投影中心误差不足1px、信息尺寸在星体范围内、深浅色、英文以及点击穿透返回均通过，无 JS/WebGL 错误；网络请求仅为已有 health/intelligence-status GET。

实际截图：outputs/system-info-home-1440.png、system-info-today-1440.png、system-info-today-dark.png、system-info-home-390.png、system-info-today-390.png。


## 2026-09-07 — 丰富星体信息、字体与排版

继续遵循“在星体上加入”的要求，信息仍投影在中央星体表面。

- 主页恒星显示真实导航树中的 5 个主功能、19 个工作入口，以及本地连接状态。入口总数递归统计叶节点，不冒充任务数量。
- 较大的第二级中央行星采用层级标识、名称、双列数字、功能说明与辅助状态排版。显示工作入口数、明确标注的全局 AI 活动数量、智能状态、连接状态及轨道状态；已配置模型时显示模型名称。
- 状态只订阅原有 intelligence-status / health 查询缓存，不增加请求或轮询。未知 AI 数量为破折号，未配置 Provider 明确说明。连接状态仅表示本地健康检查，不等同于模型可用或授权成功。
- 使用容器尺寸选择展开、紧凑和极窄三种排版；小星体保留核心信息。文字使用独立字号、字重、数字对齐和细分隔线，保留天体材质与光照。
- 中文使用 Noto Sans SC，数字和英文标题使用 Space Grotesk。27 个官方 WOFF2 Unicode 子集共 1,278,568 字节，以本地静态资源加载；保留 OFL 许可和来源说明。当前静态 UI 字符由本地子集覆盖，其他字符可回退系统字体；运行时不请求 Google Fonts。
- 减少动画偏好下收起轨道运转文案，避免与渲染器已停止轨道的状态不符。信息层继续允许点击穿透到星体。

验证：
- 61 项空间、飞行和预热测试通过；lint、生产构建及 diff 检查通过。已有 i18n Fast Refresh 和较大 JS 包体积提示保留。
- Windows Edge 实际 5173 页面完成 1440、860、390、320px 的深浅色和字体检查；CDP 确認 Noto Sans SC、Space Grotesk 均使用实际自定义字体，字体请求全部指向本机。
- 另对主页和五个行星系的中英文在桌面与两档手机宽度检查，共 36 种布局：可见文字范围位于中央球体内，无横向溢出；暂停/恢复文案、减少动画模式、点击中央星体返回均通过，无 JS 或 WebGL 错误。
- 未执行真实 Agent 或付费 API，未变更后端、数据或依赖锁文件；未提交或推送代码。

实际截图：outputs/rich-star-info-home-1440.png、rich-star-info-today-1440.png、rich-star-info-dark.png、rich-star-info-home-390.png、rich-star-info-today-390.png。
