# 立体星系导航：实现与验证

2026-09-06。工作副本：`/Users/funcyoung/Desktop/icle`。此次覆盖旧螺旋、固定散点及穿球交互；没有修改 API、权限、QueryClient、查询缓存、业务页面逻辑或页面地址。

## 真实层级

`web/src/space/hierarchy.ts` 的一棵节点树同时生成星系、入口和父级。只有已有的分组节点有星系视图，叶子直接进入原 DOM 工作区。

| 当前层 | 直接入口 | 中心作用 |
| --- | --- | --- |
| `/` | 今天 `/space/today`；任务工作台 `/space/tasks`；评估档案 `/space/agents`；会话 `/space/sessions`；设置 `/space/settings` | AgentTogether／当前所在，无返回动作 |
| `/space/tasks` | 全部任务 `/tasks`；新建任务 `/tasks/new`；经验记录 `/episodes`；协作 `/collabs` | 任务工作台／当前所在／返回：主页 |
| `/space/agents` | 评估档案 `/agents`；本地 Agent `/agents/local`；推荐 `/recommend` | 评估档案／当前所在／返回：主页 |

| `/space/today` | 今日总览 `/today`；待处理 `/today#needs-attention`；继续工作 `/today#active-work`；资源与开销 `/today#resources` | 今天／当前所在／返回：主页 |
| `/space/sessions` | 浏览与详情 `/sessions#browse`；创建任务准备 `/sessions#task-preparation`；分析与任务提案准备 `/sessions#analysis-preparation` | 会话／当前所在／返回：主页 |
| `/space/settings` | 通用与智能 `/settings#general`；Provider `/providers`；价格目录 `/settings#pricing`；预算与回放 `/settings#budgets`；数据与安全 `/settings#data` | 设置／当前所在／返回：主页 |

叶子保留业务工作区。任务详情返回 `/tasks`，经验详情返回 `/episodes`，对比返回对应经验详情，Agent 详情返回 `/agents`。`/providers` 返回设置星系；`/cost` 返回 `/settings#budgets`。原 `/today`、`/sessions`、`/settings` 继续直接显示完整工作区。

首个验证闭环是 `/` → `/space/tasks` → `/tasks` → `/space/tasks` → `/`。其中 `/tasks` 使用工作区的 DOM 返回按钮；不为叶子伪造无子功能的星系。

## 空间与轨道

复用现有 Three.js 渲染器、PerspectiveCamera、SphereGeometry、统一光照和程序化球面纹理。渲染器生命周期与稳定场景宿主绑定，业务路由变化不重新创建渲染器。球体保存在有限的对象缓存中，切层只改变角色、可见性和目标尺寸，材质、纹理、对象 UUID、自转姿态连续。

所有星光、雾和少量尘埃都在同一场景中。移除了二维星空绘制器和未使用的螺旋布局。背景使用固定种子，不随切层重新采样，也不把星点推离相机或围绕新中心重新摆放。

轨道参数集中在 `engine/orbits.ts`。同一父层中按固定入口顺序选择槽位：

| 槽位 | a | b/a | 倾角 inc（rad） | lan（rad） | arg（rad） | phase（rad） | 周期（s） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2.35 | 0.98 | 1.20 | 0.12 | 0.20 | 0.35 | 216 |
| 1 | 3.35 | 0.98 | 1.34 | -0.18 | 0.35 | 1.80 | 232 |
| 2 | 4.35 | 0.98 | 1.13 | 0.25 | -0.22 | 3.00 | 248 |
| 3 | 5.35 | 0.98 | 1.39 | -0.32 | 0.16 | 4.25 | 268 |
| 4 | 6.35 | 0.98 | 1.26 | 0.18 | -0.12 | 5.40 | 292 |

每个入口维护自己的局部 orbitTime。位置由独立旋转后的闭合椭圆计算，保留 x/y/z，轨道没有任何线或尾迹。分离的径向范围避免球体相交。FOV 固定 48°，相机距离由完整轨道包络和视口长宽比计算；调整窗口不会重写轨道参数。

中心位于导航画布投影中心。画布在顶部工具栏与星系标题下方，避免“场景中心正确，但可用区域中心错误”。待机漂移只小幅改变相机位置，始终看向中心。磁吸最大偏移 0.06 世界单位；悬停或聚焦降低入口局部速度，离开后平滑恢复，不追赶墙钟时间。中心只响应材质和焦点，不吸附位移。

标签为普通 DOM 按钮，位置来自最终相机投影，具有屏幕朝向、可见焦点和 44px 最小点击高度。标签避开球体与其他标签；窄屏出现投影接近时寻找附近空白位置，不移动三维球体、不隐藏整个功能入口。

## 转场与路由

进入约 880ms：冻结点击球体的实际世界位置（包括微小磁吸偏移）；其他入口在最初约 100ms 降速后保存父层时钟。前 33% 调整相机方向和位置，保持与目标的距离，不提前推进；之后沿轻微弧线靠近并持续看向目标。旧层在 25%—52% 淡出，新子功能从 52% 起在各自轨道的 90% 半径处显现并展开，不从同一点喷射。

目标对象原地成为新中心，不穿过球面。导航结束时更新路由和中心动作，期间按钮锁定。叶子在推进末段将视觉交给原 DOM 工作区。

返回约 820ms：子功能轻微收敛并淡出，恢复父层锚点、各入口局部时间及磁吸偏移，相机回到父级视角，原中心对象重新成为外围入口。恢复后速度从零平滑上升。快照同时保存相机位置和视线；窗口尺寸变化时仅重新计算取景。

React Router 是唯一应用位置来源。视觉快照按路由存储，不代替路由。进入记录已知的父级 history key；返回仅在可证明前一条是合法父级时执行应用内后退，否则导航到父级地址。外部路由变化（包括 POP、直接链接和同路径新 history key）取消旧动画与令牌，旧完成回调不能覆盖新位置。没有快照的刷新/深链接直接使用确定性状态，不补演虚构的进入动画。调整窗口或启用 reduced motion 会结束过时的相机过渡并解析到正确路由。

减少动态效果停止持续公转、相机漂移、自转和大推进，直接使用最终尺寸。暂停按钮停止持续运动。document.hidden 时不推进时钟，恢复时丢弃长时间帧间隔。卸载统一取消 RAF、ResizeObserver、媒体及指针监听，并释放全部缓存球体、纹理、几何和渲染器。

`SpaceNavContext.ts` 将 Context 与 Provider 分开，修复了开发热更新时消费者和 Provider 获取不同 Context 的错误。

## 修改文件

- `web/src/App.tsx`：稳定的场景宿主放在真实导航可用区域内。
- `web/src/space/hierarchy.ts`、`types.ts`：统一层级树，删除固定二维布局字段。
- `web/src/space/engine/orbits.ts`：独立闭合轨道、周期、球体尺寸与相机包络。
- `web/src/space/engine/SpaceController.ts`：三维对象复用、相机转场、父层快照、局部时钟、磁吸、标签投影、释放和调试记录。
- `web/src/space/SpaceNav.tsx`、新增 `SpaceNavContext.ts`：React Router 协调、父级返回、令牌取消和稳定 Context。
- `web/src/space/SpaceScene.tsx`、`SpaceChrome.tsx`、`SpaceFallback.tsx`：进入/返回可访问语义和统一 Context。
- `web/src/space/SpaceBackground.tsx`、`space.css`、`web/src/i18n.tsx`：统一空间背景、窄屏布局、焦点及文案。
- 删除 `web/src/space/engine/layout.ts`、`web/src/space/skyPaint.ts`：取消的螺旋实现及二维星空。
- `web/package.json`、新增 `web/tests/register.mjs`、`ts-loader.mjs`、`space.test.mjs`：测试入口，无新增运行时依赖。

## 已完成验证

`cd web && npm run test:space`：18 项通过。包含三个星系的实际父子映射、完整闭合周期、0—300 秒每 0.25 秒的碰撞/透视检查、320×380 / 462×446 / 1440×762 导航画布的视野包络及中心遮挡检查，以及每 0.5 秒的标签布局采样。还覆盖同对象跨层、两次返回的冻结相位、居中先于推进、相机不穿球、取消旧回调、深链接、减少动态效果最终尺寸、暂停、快速重复点击、后台恢复、全部入口和资源释放。

测试使用真实 Three.js 数学与场景对象；Node 场景测试用替代渲染器验证控制器，不能将其等同于 GPU 真机截图。

实浏览器（本地 Vite + 实际 API，Codex 内置浏览器）完成：

- 首页 → 任务星系 → 全部任务工作区 → 父级星系 → 首页。进入记录 53 个实际渲染帧，返回记录 50 帧。进入前后对象 UUID 相同，进入 33% 后目标投影 x/y 接近数值零，实际 draw calls 中 lines = 0。
- 父级冻结时任务入口 orbitTime 为 0.482871s；返回结束读数 0.483s。其他入口约 0.507498s，恢复第一帧读数 0.509s（已经开始平滑续转）。没有按在子层逗留的真实时间追赶。
- 两次浏览器前进分别恢复任务星系、任务工作区。刷新 `/tasks` 后返回合法父级，未补演进入动画。
- 在评估档案进入动画约 1.9% 时点击顶部“新建任务”，最终保持 `/tasks/new`，旧动画不回写路由，返回按钮可用。
- 窄屏正常窗口及 1440×900 宽屏查看，中心投影位于导航画布中心。临时视口已恢复。
- 键盘 Tab 聚焦新建任务，局部速度平滑趋近零，焦点保持可见。
- 修改 Context 后重新进行热更新，没有新增 Context 错误。

`npm run build` 通过；Vite 仍提示单个包超过 500kB。`npm run lint` 通过，仅保留既有 `i18n.tsx` Fast Refresh 导出警告。

开发模式画布的 `data-space-state` 和 `data-space-transition` 提供实时状态及最近一次过渡的实际帧记录，便于复核；生产构建不写这些诊断属性。

## 验证边界

完整轨道周期使用确定性的数学/控制器采样完成，未逐颗进行 5 分钟以上的真人点击观察。没有在 iOS/Android 真机、各类低端 GPU、Safari 或屏幕阅读器上测试；系统 reduced-motion、后台恢复及资源释放主要由控制器测试覆盖，未进行浏览器强制 GPU context loss。没有重跑后端全套测试，也没有创建、执行或修改业务任务数据。小屏幕的极端多行标签和长时间独立悬停造成的相位组合仍值得真机使用验证。


## 今天、会话、设置下级补齐

这三个原叶子现为真实分组星系，保留原球体 id、材质和颜色身份。沿用统一进入、中心返回、局部时钟和对象缓存。

分组工作区使用已登记的 URL fragment 表示位置。`navigationPath` 只识别节点树中的 fragment，未登记 fragment 与 `?learn=1` 等既有参数不改变原有工作区规则。`SpaceNav` 和面包屑都使用这一地址，因此同为 `/settings` 的不同分组不会互相覆盖导航身份；刷新和直接打开 fragment 时可以定位内容并返回所属星系。

新增 `useWorkspaceSection.ts` 在原查询成功、内容渲染后滚动并聚焦目标 section，不增加业务请求。今天的待处理与继续工作始终有真实目标 section，无记录时显示空状态。设置以 wrapper 组织原来的通用/智能、回放/预算、数据/安全分组，保留原表单与处理函数；保存按钮固定在工作区底部。会话使用三个普通链接切换浏览/任务准备/提案准备，不重置已选择的会话；原预览、创建和提取函数、禁用条件及确认提示保留。

本轮改动涉及 `hierarchy.ts`、`SpaceNav.tsx`、`SpaceChrome.tsx`、`useWorkspaceSection.ts`、`Overview.tsx`、`Sessions.tsx`、`Settings.tsx`、`i18n.tsx`、`space.css`。新中心尺寸在 `orbits.ts` 统一为 1.02。扩展全部六个星系的完整周期测试后，修复了设置星系在 320px 画布下的罕见标签碰撞：仅细化 DOM 标签空白位置搜索，不改变球体轨道。

本轮 `npm run test:space` 共 29 项通过（新增三个分组的 RED 测试均先在缺少 `/space/...` 层级时失败，再实现；还新增 fragment 父级/面包屑、同 pathname 不同 section 的回返测试）。全部六个星系都纳入完整周期碰撞、视野和标签采样。构建通过；lint 仅保留原 `i18n.tsx` Fast Refresh 警告。

实浏览器验证设置 → 预算与回放定位/焦点/保存入口 → 设置中心 → 首页，以及今天 → 待处理事项 → 今天中心 → 首页。会话任务准备、提案准备及其禁用条件另行通过界面检查；没有触发创建、提案生成、刷新价格或保存配置。
