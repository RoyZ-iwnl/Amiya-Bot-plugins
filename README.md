# Amiya-bot 插件开发仓库
# RoyZ的ComWechat适配改版

文档：https://www.amiyabot.com/develop/plugin/

## 修改内容
- 活动提醒插件修复：替换 < >为【 】防止被误识别ANSI Tag

- 兔兔互动插件修复：微信戳一戳

- ~~真实昵称获取修复~~ 已经单独作为插件上架商店

- 随机助理插件魔改：新增专属助理功能

- 微博推送插件修复：GIF正常动起来，需要修改sitepackge中的适配器源码见[Commit](https://github.com/RoyZ-iwnl/Amiya-Bot-core/commit/b8bb0070e26fd2e41806d46ac3849ff82aab7474)~~（视频正在尝试修复中）~~视频没法推送，见[Comwecaht文档](https://justundertaker.github.io/ComWeChatBotClient/message/#%E8%A7%86%E9%A2%91)