import os
import html
import time
import asyncio
import re
from amiyabot import QQGuildBotInstance
from amiyabot.builtin.message import MessageStructure
from amiyabot.adapters.tencent.qqGroup import QQGroupBotInstance
from core.database.group import GroupSetting
from core.database.messages import *
from core.util import TimeRecorder
from core import send_to_console_channel, Message, Chain, AmiyaBotPluginInstance, bot as main_bot

try:
    from core.util import attridict
except ImportError:
    from core.util import AttrDict as attridict

from .helper import get_all_datasources, get_aggregated_content, adapt_content_to_unified, initialize_aggregator_manager, UnifiedContent

curr_dir = os.path.dirname(__file__)

class WeiboPluginInstance(AmiyaBotPluginInstance): ...

bot = WeiboPluginInstance(
    name='CeobeAPI聚合推送',
    version='4.0',
    plugin_id='royz-arknights-aggregator',
    plugin_type='',
    description='基于CeobeAPI的多平台聚合推送系统',
    document=f'{curr_dir}/README.md',
    instruction=f'{curr_dir}/README_USE.md',
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml',
)

# 初始化订阅管理器
aggregator_manager = initialize_aggregator_manager(bot)

@table
class AggregatorRecord(MessageBaseModel):
    """聚合推送记录 - 防止重复推送"""
    content_id: str = CharField()       # 内容唯一ID
    platform: str = CharField()        # 平台类型
    datasource_id: str = CharField()    # 数据源ID
    record_time: int = IntegerField()   # 记录时间

def is_comwechat_instance(instance):
    """检测是否为ComWeChat实例"""
    return str(instance) == 'ComWeChat'

def parse_user_selection(text: str, max_count: int) -> List[int]:
    """解析用户选择的序号列表（支持逗号分隔的多选）"""
    indices = []
    # 提取所有数字
    numbers = re.findall(r'\d+', text)
    
    for num_str in numbers:
        try:
            num = int(num_str)
            if 1 <= num <= max_count:
                indices.append(num)
        except ValueError:
            continue
    
    return list(set(indices))  # 去重

# ========== 聚合推送系统命令 ==========

@bot.on_message(group_id='aggregator', keywords=['开启聚合推送'])
async def enable_aggregator_push(data: Message):
    """开启聚合推送"""
    if isinstance(data.instance, QQGroupBotInstance):
        return Chain(data).text('抱歉博士，该功能在群聊暂不可用~')

    if not data.is_admin:
        return Chain(data).text('抱歉，聚合推送只能由管理员设置')

    try:
        # 获取所有数据源
        datasources = await get_all_datasources()
        if not datasources:
            return Chain(data).text('获取数据源列表失败，请稍后重试')

        # 更新订阅管理器的数据源信息
        aggregator_manager.update_datasources(datasources)
        
        # 生成数据源选择菜单
        supported_platforms = ['weibo', 'bilibili', 'netease-cloud-music', 'arknights-game', 'arknights-website', 'endfield-game']
        menu_text, index_map = aggregator_manager.generate_datasource_menu(supported_platforms)
        
        if not index_map:
            return Chain(data).text('暂无可用数据源')
        
        # 发送选择菜单
        reply = Chain(data).text(f"CeobeAPI聚合推送设置\n\n{menu_text}")
        
        # 等待用户选择
        wait = await data.wait(reply)
        if not wait:
            return Chain(data).text('操作取消或超时，已取消设置')

        # 解析用户选择
        selected_indices = parse_user_selection(wait.text_digits, len(index_map))
        if not selected_indices:
            return Chain(data).text('未选择有效的数据源，已取消设置')

        # 转换为数据源ID列表
        selected_datasource_ids = [index_map[i] for i in selected_indices]
        
        # 添加订阅
        success = aggregator_manager.add_subscription(
            data.channel_id, 
            data.instance.appid, 
            selected_datasource_ids
        )
        
        if success:
            # 生成确认信息
            selected_names = []
            for ds_id in selected_datasource_ids:
                ds_info = aggregator_manager.datasources.get(ds_id)
                if ds_info:
                    selected_names.append(f"{ds_info.get('nickname', '未知')}({ds_info.get('platform', '未知')})")
            
            confirm_text = f"已成功订阅 {len(selected_datasource_ids)} 个数据源：\n\n"
            confirm_text += "\n".join(selected_names)
            confirm_text += "\n\n聚合推送已在本群开启"
            
            return Chain(data).text(confirm_text)
        else:
            return Chain(data).text('订阅设置失败，请重试')

    except Exception as e:
        from .helper import debug_log
        debug_log(f"开启聚合推送失败: {e}", force=True, bot_instance=bot)
        print(f"开启聚合推送失败: {e}")
        return Chain(data).text('设置失败，请检查日志或重试')


@bot.on_message(group_id='aggregator', keywords=['关闭聚合推送'])
async def disable_aggregator_push(data: Message):
    """关闭聚合推送"""
    if not data.is_admin:
        return Chain(data).text('抱歉，聚合推送只能由管理员设置')

    success = aggregator_manager.remove_subscription(data.channel_id, data.instance.appid)
    
    if success:
        return Chain(data).text('已在本群关闭聚合推送')
    else:
        return Chain(data).text('本群未开启聚合推送或关闭失败')


@bot.on_message(group_id='aggregator', keywords=['最新内容', '最新聚合'])
async def get_latest_aggregated_content(data: Message):
    """获取最新聚合内容"""
    group_key = aggregator_manager._get_group_key(data.channel_id, data.instance.appid)
    subscription = aggregator_manager.subscriptions.get(group_key)
    
    if not subscription or not subscription.get('enabled', False):
        return Chain(data).text('本群未开启聚合推送，请先发送"兔兔开启聚合推送"进行设置')
    
    datasource_ids = subscription.get('datasource_ids', [])
    if not datasource_ids:
        return Chain(data).text('未订阅任何数据源')
    
    try:
        # 获取最新内容
        raw_contents = await get_aggregated_content(datasource_ids)
        if not raw_contents:
            return Chain(data).text('暂无最新内容')
        
        # 取第一条内容进行展示
        raw_data = raw_contents[0]
        content = adapt_content_to_unified(raw_data)
        
        if content:
            return await build_aggregated_message(content, data)
        else:
            return Chain(data).text('内容处理失败')
            
    except Exception as e:
        from .helper import debug_log
        debug_log(f"获取最新聚合内容失败: {e}", force=True, bot_instance=bot)
        print(f"获取最新聚合内容失败: {e}")
        return Chain(data).text('获取失败，请检查日志或重试')


async def build_aggregated_message(content: UnifiedContent, data: MessageStructure) -> Chain:
    """构建聚合内容消息"""
    try:
        chain = Chain(data)
        
        # 添加来源信息和文本内容
        header = f"来自 {content.source_name} 的最新内容"
        if content.publish_time:
            header += f"\n时间: {content.publish_time.strftime('%Y-%m-%d %H:%M')}"
        
        # 获取文本长度限制（0表示不限制）
        content_length = bot.get_config('setting', {}).get('contentPreviewLength', 0)
        display_text = content.get_display_text(content_length)
        full_text = f"{header}\n\n{html.unescape(display_text)}"
        chain.text(full_text)
        
        # 处理媒体内容（下载图片/GIF）
        if content.media_urls:
            setting = attridict(bot.get_config('setting'))
            images_cache_dir = setting.get('imagesCache', 'log/aggregator')
            
            # 分别处理图片和GIF
            image_paths = []
            gif_paths = []
            max_images = bot.get_config('setting', {}).get('maxImagesPerPost', 9)
            media_urls = content.media_urls if max_images <= 0 else content.media_urls[:max_images]
            
            for url in media_urls:
                path = await download_media_file(url, images_cache_dir)
                if path:
                    from .helper import is_gif_file
                    if is_gif_file(path):
                        # 检查是否允许发送GIF
                        if bot.get_config('setting', {}).get('sendGIF', True):
                            gif_paths.append(path)
                    else:
                        image_paths.append(path)
            
            # 处理图片拼接
            if image_paths and bot.get_config('setting', {}).get('mergeImages', False):
                from .helper import merge_images, merge_remaining_long_strips, merge_square_like_images
                merged_path, remaining_images = merge_images(image_paths, images_cache_dir, {
                    'setting': bot.get_config('setting', {})
                })
                if merged_path:
                    # 先发送拼接图片
                    chain.image([merged_path])
                    # 对剩余图片中的长条图进行左右拼接
                    if remaining_images:
                        long_strips_merged, final_remaining = merge_remaining_long_strips(
                            remaining_images, images_cache_dir,
                            aspect_ratio_threshold=bot.get_config('setting', {}).get('longStripThreshold', 1.5),
                            max_width=bot.get_config('setting', {}).get('maxMergedWidth', 2000)
                        )
                        if long_strips_merged:
                            # 发送长条图拼接结果
                            chain.image([long_strips_merged])
                            # 对剩余图片中的1:1图片进行拼接
                            if final_remaining:
                                square_merged, truly_final_remaining = merge_square_like_images(
                                    final_remaining, images_cache_dir,
                                    tolerance_percent=bot.get_config('setting', {}).get('mergeTolerance', 5.0)
                                )
                                if square_merged:
                                    # 发送1:1图片拼接结果
                                    chain.image([square_merged])
                                    # 如果还有剩余图片，也发送
                                    if truly_final_remaining:
                                        chain.image(truly_final_remaining)
                                else:
                                    # 没有1:1图片拼接，直接发送剩余图片
                                    chain.image(final_remaining)
                        else:
                            # 没有长条图拼接，尝试1:1图片拼接
                            square_merged, final_remaining = merge_square_like_images(
                                remaining_images, images_cache_dir,
                                tolerance_percent=bot.get_config('setting', {}).get('mergeTolerance', 5.0)
                            )
                            if square_merged:
                                # 发送1:1图片拼接结果
                                chain.image([square_merged])
                                # 如果还有剩余图片，也发送
                                if final_remaining:
                                    chain.image(final_remaining)
                            else:
                                # 没有任何拼接，直接发送剩余图片
                                chain.image(remaining_images)
                else:
                    chain.image(image_paths)  # 拼接失败，发送原图
            elif image_paths:
                chain.image(image_paths)
            
            # 处理GIF文件
            if gif_paths:
                # 检查是否为ComWeChat实例
                if is_comwechat_instance(data.instance):
                    from .helper import compress_gif_for_wechat
                    for gif_path in gif_paths:
                        compressed_path = await compress_gif_for_wechat(gif_path, images_cache_dir)
                        if compressed_path:
                            chain.face(compressed_path)
                else:
                    # 非ComWeChat实例，直接发送GIF作为图片
                    chain.image(gif_paths)
        
        # 添加原文链接
        if content.source_url and not isinstance(data.instance, QQGuildBotInstance):
            chain.text(f'\n\n原文链接: {content.source_url}')
        
        return chain
        
    except Exception as e:
        from .helper import debug_log
        debug_log(f"构建聚合消息失败: {e}", force=True, bot_instance=bot)
        print(f"构建聚合消息失败: {e}")
        return Chain(data).text('消息构建失败')


async def download_media_file(url: str, cache_dir: str) -> Optional[str]:
    """下载媒体文件到本地缓存"""
    try:
        from amiyabot.network.download import download_async
        from core.util import create_dir
        
        # 从URL获取文件名
        name = url.split('/')[-1]
        if '?' in name:
            name = name.split('?')[0]
        if not name or '.' not in name:
            name = f"{int(time.time())}.jpg"
        
        path = os.path.join(cache_dir, name)
        create_dir(path, is_file=True)
        
        if not os.path.exists(path):
            stream = await download_async(url)
            if stream:
                with open(path, 'wb') as f:
                    f.write(stream)
                return path
        else:
            return path
        
        return None
        
    except Exception as e:
        from .helper import debug_log
        debug_log(f"下载媒体文件失败: {e}", force=True)
        print(f"下载媒体文件失败: {e}")
        return None


# ========== 聚合推送定时任务 ==========

@bot.timed_task(each=60)  # 聚合推送使用60秒间隔
async def aggregator_push_task(_):
    """聚合推送定时任务"""
    try:
        # 添加定时任务执行日志
        from .helper import debug_log
        debug_log("执行聚合推送定时任务", bot_instance=bot)
        
        # 获取所有启用的订阅
        enabled_subscriptions = aggregator_manager.get_enabled_subscriptions()
        if not enabled_subscriptions:
            debug_log(f"没有启用的订阅，跳过推送", bot_instance=bot)
            return
        
        # 收集所有订阅的数据源ID
        all_datasource_ids = set()
        for sub in enabled_subscriptions:
            all_datasource_ids.update(sub.get('datasource_ids', []))
        
        if not all_datasource_ids:
            return
        
        # 更新数据源信息（确保映射关系是最新的）
        datasources = await get_all_datasources()
        if datasources:
            aggregator_manager.update_datasources(datasources)
            debug_log(f"更新了 {len(datasources)} 个数据源信息用于映射", bot_instance=bot)
            
            # 更新现有订阅的数据源名称（向后兼容）
            updated_count = 0
            for group_key, subscription in aggregator_manager.subscriptions.items():
                if 'datasource_names' not in subscription:
                    datasource_names = []
                    for uuid_id in subscription.get('datasource_ids', []):
                        ds_info = aggregator_manager.datasources.get(uuid_id)
                        if ds_info:
                            nickname = ds_info.get('nickname', '未知数据源')
                            datasource_names.append(nickname)
                    
                    if datasource_names:
                        subscription['datasource_names'] = datasource_names
                        updated_count += 1
            
            if updated_count > 0:
                aggregator_manager._save_subscriptions()
                debug_log(f"更新了 {updated_count} 个订阅的数据源名称", bot_instance=bot)
        else:
            debug_log("获取数据源信息失败，可能影响数据源匹配", bot_instance=bot)
        
        # 获取聚合内容
        from .helper import debug_log
        debug_log(f"开始获取聚合内容，数据源数量: {len(all_datasource_ids)}", bot_instance=bot)
        raw_contents = await get_aggregated_content(list(all_datasource_ids))
        if not raw_contents:
            debug_log("没有获取到内容，跳过推送", bot_instance=bot)
            return
        
        debug_log(f"获取到 {len(raw_contents)} 条内容，开始处理", bot_instance=bot)
        # 处理每条内容
        for raw_data in raw_contents:
            await process_single_aggregated_content(raw_data, enabled_subscriptions)
            
    except Exception as e:
        from .helper import debug_log
        debug_log(f"聚合推送任务失败: {e}", force=True, bot_instance=bot)
        print(f"聚合推送任务失败: {e}")


async def process_single_aggregated_content(raw_data: dict, subscriptions: List[dict]):
    """处理单条聚合内容"""
    try:
        from .helper import debug_log
        
        # 转换为统一格式
        content = adapt_content_to_unified(raw_data)
        if not content:
            debug_log("内容转换失败，跳过", bot_instance=bot)
            return
        
        debug_log(f"处理内容: {content.content_id} 来自 {content.source_name}", bot_instance=bot)
        
        # 检查是否已推送过
        existing_record = AggregatorRecord.get_or_none(
            AggregatorRecord.content_id == content.content_id,
            AggregatorRecord.platform == content.platform
        )
        if existing_record:
            debug_log(f"内容已推送过，跳过: {content.content_id}", bot_instance=bot)
            return
        
        # 查找订阅了该数据源的群组
        target_subscriptions = []
        debug_log(f"检查订阅匹配 - 内容source_id: {content.source_id}", bot_instance=bot)
        debug_log(f"当前所有订阅: {[(sub.get('group_id'), sub.get('datasource_names', sub.get('datasource_ids'))) for sub in subscriptions]}", bot_instance=bot)
        
        for sub in subscriptions:
            # 优先使用datasource_names，如果没有则回退到datasource_ids（向后兼容）
            subscription_names = sub.get('datasource_names', [])
            subscription_ids = sub.get('datasource_ids', [])
            
            debug_log(f"检查订阅 {sub.get('group_id')}: names={subscription_names}, ids={subscription_ids}", bot_instance=bot)
            
            # 按名称匹配
            if content.source_id in subscription_names:
                target_subscriptions.append(sub)
                debug_log(f"找到名称匹配订阅: {sub.get('group_id')}", bot_instance=bot)
            # 向后兼容：如果没有names字段，尝试用ID匹配
            elif not subscription_names and content.source_id in subscription_ids:
                target_subscriptions.append(sub)
                debug_log(f"找到ID匹配订阅: {sub.get('group_id')}", bot_instance=bot)
        
        if not target_subscriptions:
            debug_log(f"没有群组订阅该数据源，跳过: {content.source_id} ({content.source_name})", bot_instance=bot)
            return
        
        # 内容屏蔽检查
        for regex in bot.get_config("block", []):
            if re.match(regex, html.unescape(content.text)) or re.search(regex, html.unescape(content.text)):
                debug_log(f"内容触发屏蔽规则，跳过推送: {content.content_id}", bot_instance=bot)
                
                # 标记为已处理，避免重复通知
                AggregatorRecord.create(
                    content_id=content.content_id,
                    platform=content.platform,
                    datasource_id=content.source_id,
                    record_time=int(time.time())
                )
                
                await send_to_console_channel(
                    Chain().text(f'聚合内容触发屏蔽规则，跳过推送\n来源: {content.source_name}\nID: {content.content_id}')
                )
                return
        
        # 标记为已推送
        AggregatorRecord.create(
            content_id=content.content_id,
            platform=content.platform,
            datasource_id=content.source_id,
            record_time=int(time.time())
        )
        
        # 发送到各个订阅群组
        time_rec = TimeRecorder()
        send_tasks = []
        
        await send_to_console_channel(
            Chain().text(f'开始推送聚合内容\n来源: {content.source_name}\nID: {content.content_id}\n目标数: {len(target_subscriptions)}')
        )
        
        for subscription in target_subscriptions:
            try:
                instance = main_bot[subscription['bot_id']]
                if not instance:
                    continue
                
                # 构建消息链
                chain = Chain()
                header = f"【{content.source_name}】最新内容"
                if content.publish_time:
                    header += f"\n{content.publish_time.strftime('%Y-%m-%d %H:%M')}"
                
                # 获取文本长度限制（0表示不限制）
                content_length = bot.get_config('setting', {}).get('contentPreviewLength', 0)
                display_text = content.get_display_text(content_length)
                full_text = f"{header}\n\n{html.unescape(display_text)}"
                chain.text(full_text)
                
                # 处理媒体内容（下载图片/GIF）
                if content.media_urls:
                    setting = attridict(bot.get_config('setting'))
                    cache_dir = setting.get('imagesCache', 'log/aggregator')
                    
                    # 分别处理图片和GIF
                    image_paths = []
                    gif_paths = []
                    max_images = bot.get_config('setting', {}).get('maxImagesPerPost', 9)
                    media_urls = content.media_urls if max_images <= 0 else content.media_urls[:max_images]
                    
                    for url in media_urls:
                        path = await download_media_file(url, cache_dir)
                        if path:
                            from .helper import is_gif_file
                            if is_gif_file(path):
                                # 检查是否允许发送GIF
                                if bot.get_config('setting', {}).get('sendGIF', True):
                                    gif_paths.append(path)
                            else:
                                image_paths.append(path)
                    
                    # 处理图片拼接
                    if image_paths and bot.get_config('setting', {}).get('mergeImages', False):
                        from .helper import merge_images, merge_remaining_long_strips, merge_square_like_images
                        merged_path, remaining_images = merge_images(image_paths, cache_dir, {
                            'setting': bot.get_config('setting', {})
                        })
                        if merged_path:
                            # 先发送拼接图片
                            chain.image([merged_path])
                            # 对剩余图片中的长条图进行左右拼接
                            if remaining_images:
                                long_strips_merged, final_remaining = merge_remaining_long_strips(
                                    remaining_images, cache_dir,
                                    aspect_ratio_threshold=bot.get_config('setting', {}).get('longStripThreshold', 1.5),
                                    max_width=bot.get_config('setting', {}).get('maxMergedWidth', 2000)
                                )
                                if long_strips_merged:
                                    # 发送长条图拼接结果
                                    chain.image([long_strips_merged])
                                    # 对剩余图片中的1:1图片进行拼接
                                    if final_remaining:
                                        square_merged, truly_final_remaining = merge_square_like_images(
                                            final_remaining, cache_dir,
                                            tolerance_percent=bot.get_config('setting', {}).get('mergeTolerance', 5.0)
                                        )
                                        if square_merged:
                                            # 发送1:1图片拼接结果
                                            chain.image([square_merged])
                                            # 如果还有剩余图片，也发送
                                            if truly_final_remaining:
                                                chain.image(truly_final_remaining)
                                        else:
                                            # 没有1:1图片拼接，直接发送剩余图片
                                            chain.image(final_remaining)
                                else:
                                    # 没有长条图拼接，尝试1:1图片拼接
                                    square_merged, final_remaining = merge_square_like_images(
                                        remaining_images, cache_dir,
                                        tolerance_percent=bot.get_config('setting', {}).get('mergeTolerance', 5.0)
                                    )
                                    if square_merged:
                                        # 发送1:1图片拼接结果
                                        chain.image([square_merged])
                                        # 如果还有剩余图片，也发送
                                        if final_remaining:
                                            chain.image(final_remaining)
                                    else:
                                        # 没有任何拼接，直接发送剩余图片
                                        chain.image(remaining_images)
                        else:
                            chain.image(image_paths)  # 拼接失败，发送原图
                    elif image_paths:
                        chain.image(image_paths)
                    
                    # 处理GIF文件
                    if gif_paths:
                        # 检查是否为ComWeChat实例
                        if is_comwechat_instance(instance.instance):
                            from .helper import compress_gif_for_wechat
                            for gif_path in gif_paths:
                                compressed_path = await compress_gif_for_wechat(gif_path, cache_dir)
                                if compressed_path:
                                    chain.face(compressed_path)
                        else:
                            # 非ComWeChat实例，直接发送GIF作为图片
                            chain.image(gif_paths)
                
                # 添加原文链接
                if content.source_url and not isinstance(instance.instance, QQGuildBotInstance):
                    chain.text(f'\n\n{content.source_url}')
                
                # 发送消息
                if bot.get_config('sendAsync'):
                    send_tasks.append(instance.send_message(chain, channel_id=subscription['group_id']))
                else:
                    await instance.send_message(chain, channel_id=subscription['group_id'])
                    await asyncio.sleep(bot.get_config('sendInterval'))
                    
            except Exception as e:
                from .helper import debug_log
                debug_log(f"发送到群组 {subscription['group_id']} 失败: {e}", force=True, bot_instance=bot)
                print(f"发送到群组 {subscription['group_id']} 失败: {e}")
        
        if send_tasks:
            await asyncio.wait(send_tasks)
        
        await send_to_console_channel(
            Chain().text(f'聚合推送完成\nID: {content.content_id}\n耗时: {time_rec.total()}')
        )
        
    except Exception as e:
        from .helper import debug_log
        debug_log(f"处理聚合内容失败: {e}", force=True, bot_instance=bot)
        print(f"处理聚合内容失败: {e}")
