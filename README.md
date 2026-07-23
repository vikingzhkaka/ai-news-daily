# AI 资讯每日简报 · AI 趋势雷达

每日自动抓取并汇总的 AI 资讯仪表盘，覆盖六个维度：
- A. AI 原生（AI-native）
- B. AI 转型（AI transformation）
- C. 企业员工个人如何更有效地利用 AI
- D. 反直觉思考（Counterintuitive）
- E. 新趋势 · 新应用 · 迅速走红的 AI skills 与实践
- F. 对普通行业与普通人的启发与警示

## 数据来源
通过 WebSearch 检索公开网络资讯，优先采用近 3 个月、权威来源，包括：
GitHub Trending / OSSInsight、BCG / McKinsey / Gallup / Persol 等调研机构、行业媒体、国务院及部委政策文件（如"人工智能+"行动）。所有条目均附真实可点击来源链接，不编造数据。

## 更新频率
- 每日 **09:00 / 20:00**（设备本地时间）由 WorkBuddy 自动化生成并推送至本仓库（经 GitHub Contents API，未使用 git push）。
- 若设备在上述时点处于休眠、错过生成，下次成功运行时将检测距上次生成是否超过 14 小时；若超过则在页面顶部显示「⚠️ 补生成提醒」横幅并补生成最新资讯。
- 线上地址：https://vikingzhkaka.github.io/ai-news-daily/

## 推送失败提醒
若某次推送至 GitHub 失败（如 gh 未登录、网络不可达），自动化会保留本地最新 index.html 并在下次运行重试；若已在本机配置 SMTP 凭据，还会额外向 仓库所有者邮箱（本 README 不公开具体地址） 发送一封失败提醒邮件（凭据仅存于本机，不入库）。

> 注意：Outlook/Hotmail 已禁用 SMTP 基础认证，不能作为发信端。请用仍支持 SMTP 授权的邮箱（如 QQ/163）作为发信账户，收件人填对应邮箱地址即可正常收到。凭据格式见本机 `.workbuddy/mail_creds.json`。
