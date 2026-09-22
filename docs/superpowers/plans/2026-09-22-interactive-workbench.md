# 交互式画像工作台

基线：e18fbaf。分支：codex/interactive-profile-workbench。用户要求可以实际操作和验收的 GUI，本轮交付运行中的本地工作台。

## 范围

同源静态页面连接真实数据库和冻结的 GET /v1/profile。战队搜索、精确版本、截止日期、查询与重试、选手切换、双人比较、英雄池、五维百分位、英雄类型偏好、BP 倾向、数据与来源说明、当前结果 JSON 预览与复制。未实现的 Value、剧本、预测不提供虚构推荐。画像选手列表来自历史参赛记录，不标作当前阵容。

桌面采用纸白、墨色与朱红强调的编辑式布局，左侧窄导航、顶部筛选、主区选手与分析内容。移动端单列，无横向页面溢出；图表缺值不补零。无斜体、破折号、emoji，不依赖远程字体或图片。

## 文件归属

- 前端实施者：web/index.html、web/workbench.css、web/workbench.js。其余文件只读。
- 目录服务实施者：analysis/workbench_catalog.py、tests/analysis/test_workbench_catalog.py。不得改 server、schema、conftest 或开发库。
- 主控：analysis/server.py、tests/analysis/test_workbench_http.py、启动停止脚本、文档与验收；最终整合与 GitHub。
- 审查者只读。所有数据库测试显式使用独立 _test 库、端口 55439。

## 新的只读目录接口

GET /v1/catalog，无 query。不修改冻结 Profile body。只读取公共职业 CM 比赛及相关数据，排除 scrim、未来比赛。窗口锚定最新已发生公共 CM 比赛的 UTC 日期，回溯 90 个自然日。无比赛时使用 UTC 今天并返回空数组，不能制造默认队伍。

analysis/workbench_catalog.py 暴露 build_catalog_from_connection(conn) 和 load_catalog(dsn)。后者使用 repeatable read/read only、15 秒 SQL 超时，数据库失败转 ProfileServiceError('upstream_unavailable', 中文安全消息)。调用者可对前者使用测试事务，不自动提交。

返回 JSON 形状：

```json
{
  "generated_at": "ISO UTC",
  "window": {"as_of": "YYYY-MM-DD", "from": "YYYY-MM-DD"},
  "defaults": {"team_id": 123, "patch": "7.41e", "as_of": "YYYY-MM-DD"},
  "teams": [{"team_id":123,"name":"公开队名","tag":"TAG","n_matches":40,"n_detailed_matches":38,"latest_match_at":"ISO UTC","patches":["7.41e"]}],
  "patches": [{"patch":"7.41e","base_version":"7.41","n_matches":200,"n_detailed_matches":180,"latest_match_at":"ISO UTC"}],
  "heroes": {"1":{"name":"npc_dota_hero_antimage","localized_name":"Anti-Mage"}},
  "summary": {"public_cm_matches":200,"detailed_matches":180,"team_count":30,"latest_match_at":"ISO UTC"},
  "provenance": {"position_method":"team_lane_economy_v1","position_notice":"赛后规则推断，不是真值或同场赛前特征。","tier_notice":"逐赛事证据分类，未审赛事保留未知。","leagues":[{"league_id":1,"name":"赛事名称","tier":"tier1","source_url":"https://example.org","reviewed_at":"ISO UTC"}]}
}
```

空数据 defaults.team_id、defaults.patch、summary.latest_match_at 为 null。默认版本优先已有详情最多的精确版本，默认队伍为该版本详情最多的队伍；排序同数时按数值 ID 稳定。teams.patches 只含该队有比赛的精确版本。所有数量来自窗口内公开 CM 数据，详情以存在 match_players 为准。赛事证据只返回本窗口出现且名称仍匹配的核定旁表记录。返回安全字段，不含 DSN、任意原始 payload 或私密队伍。

## 服务与页面

make_server(host, port, *, provider, catalog_provider=None) 保持旧测试调用兼容。静态白名单仅 /、/index.html、/workbench.css、/workbench.js，不提供任意文件路径。响应安全 headers、正确 MIME。/v1/catalog 无配置时返回 503，不模拟数据。CLI 同时注入真实画像与目录 provider。

页面用当前 URL 参数恢复 team_id、patch、as_of。初次无参数时使用目录默认值自动查询。表单与已显示结果应清楚分开，筛选修改后提示重新查询；请求中止与序号检查防止旧结果覆盖新选择。HTTP 200 的 error 信封必须作为业务不足处理。失联显示可恢复错误，不能当成无数据成功。名称和消息使用 textContent，禁止直接注入数据库字符串到 HTML。

## 验收

目录与路由先业务 RED 再 GREEN。目录覆盖来源隔离、未来排除、窗口、默认值、空库、详情去重、名称变化；静态路由覆盖路径穿越拒绝与旧 Profile 契约。

主控实际浏览器验证：真实队伍切换、不同版本、选手详情、双人比较、BP 页、数据来源页、JSON 预览复制、空或错误态、返回重试，桌面和手机宽度无溢出。不把静态快照或接口测试称为 GUI 验收。最终保留可用 GUI 进程和独占本地数据库给用户操作，明确启动与停止命令；不启动后台采集。


## 最终实施记录

全量 423 项测试通过，耗时 185.77 秒，退出码 0。实际浏览器验证了真实目录、选手与队伍切换、两个精确版本、日期空结果、HTTP 200 样本不足、断线恢复、BP 手数筛选及 JSON 复制读回。桌面 1280×900 与手机 390×844 已检查，手机导航和表格滚动可用，页面无横溢出。

当前目录为 319 支战队、3 个精确版本、1,501 场公开 CM 比赛。GUI http://127.0.0.1:8016/ 与独占 PostgreSQL 55439 留给用户操作，无后台采集。Value、剧本和序列模型未接入。JSON 采用可预览、可复制的快照，浏览器文件下载不作为已验收能力。

启动器通过 X-Workbench-Pid 和子进程身份确认本次服务，失败只停止新启动的子进程；停止前核对工作区和进程身份。测试入口取消默认数据库连接，必须显式设置 TEST_DATABASE_URL，且名称以 _test 结尾。
