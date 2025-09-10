import os
import html
import time
import asyncio
import re
from PIL import Image, ImageSequence
from amiyabot import QQGuildBotInstance
from amiyabot.builtin.message import MessageStructure
from amiyabot.adapters.tencent.qqGroup import QQGroupBotInstance
from core.database.group import GroupSetting
from core.database.messages import *
from core.util import TimeRecorder, find_most_similar
from core import send_to_console_channel, Message, Chain, AmiyaBotPluginInstance, bot as main_bot

try:
    from core.util import attridict
except ImportError:
    from core.util import AttrDict as attridict

from .helper import WeiboUser, get_all_datasources, get_aggregated_content, adapt_content_to_unified, aggregator_manager, UnifiedContent

curr_dir = os.path.dirname(__file__)

class WeiboPluginInstance(AmiyaBotPluginInstance): ...

bot = WeiboPluginInstance(
    name='CeobeAPI聚合推送',
    version='4.0',
    plugin_id='amiyabot-aggregator',
    plugin_type='official',
    description='基于CeobeAPI的多平台聚合推送系统（兼容原微博推送）',
    document=f'{curr_dir}/README.md',
    instruction=f'{curr_dir}/README_USE.md',
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml',
)

@table
class WeiboRecord(MessageBaseModel):
    user_id: int = IntegerField()
    blog_id: str = CharField()
    record_time: int = IntegerField()

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

async def compress_gif_for_wechat(gif_path: str, cache_dir: str) -> str:
    """
    为适配微信的限制而压缩GIF。
    - 检查GIF的宽度是否超过1000px。
    - 检查GIF的文件大小是否超过10MB。
    - 如果任一条件满足，则进行压缩，直到满足所有条件。
    - 返回压缩后或原始的图片路径。
    """
    MAX_WIDTH = 1000  # 微信最大宽度限制
    MAX_SIZE_BYTES = 10 * 1024 * 1024  # 微信最大文件大小限制 (10MB)
    
    try:
        # 检查原始文件是否存在
        if not os.path.exists(gif_path):
            return gif_path
            
        file_size = os.path.getsize(gif_path)
        img = Image.open(gif_path)
        width, _ = img.size
        
        # 如果文件符合要求，直接返回原路径
        if width <= MAX_WIDTH and file_size <= MAX_SIZE_BYTES:
            img.close()
            return gif_path
            
        print(f"[微博插件] 检测到GIF需要压缩: {os.path.basename(gif_path)}, 原始尺寸: {width}px, 大小: {file_size / 1024 / 1024:.2f}MB")
        
        # 准备压缩后文件的保存路径
        base_name = os.path.basename(gif_path)
        compressed_path = os.path.join(cache_dir, f"compressed_{base_name}")
        
        scale = 1.0
        # 如果宽度超出限制，首先计算缩放比例
        if width > MAX_WIDTH:
            scale = MAX_WIDTH / width
            
        # 循环压缩，直到文件大小符合要求
        while True:
            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            
            # 提取并缩放每一帧
            frames = []
            # 兼容不同版本的Pillow库
            try:
                from PIL.Image import Resampling
                resample_method = Resampling.LANCZOS
            except ImportError:
                resample_method = Image.ANTIALIAS
                
            for frame in ImageSequence.Iterator(img):
                resized_frame = frame.convert("RGBA").resize((new_width, new_height), resample_method)
                frames.append(resized_frame)
                
            if not frames:
                img.close()
                return gif_path
                
            # 获取原始GIF的播放信息
            duration = img.info.get('duration', 100)
            loop = img.info.get('loop', 0)
            
            # 将压缩后的帧保存到一个临时文件，用于检查大小
            temp_path = compressed_path + ".tmp"
            frames[0].save(
                temp_path,
                save_all=True,
                append_images=frames[1:],
                duration=duration,
                loop=loop,
                optimize=True,  # 开启优化以减小文件大小
            )
            
            # 检查临时文件大小
            if os.path.getsize(temp_path) <= MAX_SIZE_BYTES:
                # 如果符合要求，重命名为最终文件
                if os.path.exists(compressed_path):
                    os.remove(compressed_path)
                os.rename(temp_path, compressed_path)
                img.close()
                print(f"[微博插件] GIF 压缩成功. 新路径: {compressed_path}, 新尺寸: {new_width}px, 大小: {os.path.getsize(compressed_path) / 1024 / 1024:.2f}MB")
                return compressed_path
            else:
                # 如果仍过大，删除临时文件并进一步缩小尺寸
                os.remove(temp_path)
                scale *= 0.95  # 每次将尺寸缩小5%
                
                # 安全检查，防止无限循环或图片缩得太小
                if new_width < 100:
                    img.close()
                    print(f"[微博插件] 无法将GIF压缩到目标大小, 将尝试发送原图。")
                    return gif_path
                    
    except Exception as e:
        print(f"[微博插件] 压缩GIF时发生意外错误: {e}")
        return gif_path  # 发生错误时返回原图路径

async def send_by_index(index: int, weibo: WeiboUser, data: MessageStructure):
    result = await weibo.get_weibo_content(index - 1)
    if not result:
        return Chain(data).text('博士...暂时无法获取微博呢...请稍后再试吧~')
    else:
        chain = (
            Chain(data)
            .text(result.user_name + '\n')
            .text(html.unescape(result.html_text) + '\n')
        )

        # 发送普通图片
        if result.pics_list:
            chain.image(result.pics_list)

        # 检测是否为ComWeChat实例并发送GIF
        if is_comwechat_instance(data.instance):
            # ComWeChat：使用Face元素发送GIF，并在发送前检查是否需要压缩
            if result.gif_list:
                cache_dir = weibo.images_cache_dir  # 从weibo实例获取缓存目录
                for gif_path in result.gif_list:
                    # 调用压缩函数
                    compressed_path = await compress_gif_for_wechat(gif_path, cache_dir)
                    chain.face(compressed_path)  # 使用压缩后的路径发送
        else:
            # 其他平台：普通图片方式发送GIF
            if result.gif_list:
                chain.image(result.gif_list)

        if not isinstance(data.instance, QQGuildBotInstance):
            chain.text(f'\n\n{result.detail_url}')

        return chain

def get_index_from_text(text: str, array: list):
    r = re.search(r'(\d+)', text)
    if r:
        index = abs(int(r.group(1))) - 1
        if index >= len(array):
            index = len(array) - 1
        return index

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

@bot.on_message(group_id='weibo', keywords=['开启微博推送'])
async def _(data: Message):
    if isinstance(data.instance, QQGroupBotInstance):
        return Chain(data).text('抱歉博士，该功能在群聊暂不可用~')

    if not data.is_admin:
        return Chain(data).text('抱歉，微博推送只能由管理员设置')

    channel: GroupSetting = GroupSetting.get_or_none(group_id=data.channel_id, bot_id=data.instance.appid)
    if channel:
        GroupSetting.update(send_weibo=1).where(
            GroupSetting.group_id == data.channel_id,
            GroupSetting.bot_id == data.instance.appid,
        ).execute()
    else:
        if GroupSetting.get_or_none(group_id=data.channel_id):
            GroupSetting.update(bot_id=data.instance.appid, send_weibo=1).where(
                GroupSetting.group_id == data.channel_id
            ).execute()
        else:
            GroupSetting.create(group_id=data.channel_id, bot_id=data.instance.appid, send_weibo=1)

    return Chain(data).text('已在本群开启微博推送')

@bot.on_message(group_id='weibo', keywords=['关闭微博推送'])
async def _(data: Message):
    if not data.is_admin:
        return Chain(data).text('抱歉，微博推送只能由管理员设置')

    GroupSetting.update(send_weibo=0).where(
        GroupSetting.group_id == data.channel_id, GroupSetting.bot_id == data.instance.appid
    ).execute()

    return Chain(data).text('已在本群关闭微博推送')

@bot.on_message(group_id='weibo', keywords=['微博'])
async def _(data: Message):
    listens: list = bot.get_config('listen')
    setting = attridict(bot.get_config('setting'))
    weibo: Optional[WeiboUser] = None

    text = data.text.replace('微博', '').replace('最新', '')
    if text:
        name_map = {item['name']: item for item in listens}
        name = find_most_similar(text, list(name_map.keys()))
        if name:
            weibo = WeiboUser(name_map[name]['uid'], setting)

    if not weibo:
        if len(listens) == 1:
            weibo = WeiboUser(listens[0]['uid'], setting)
        else:
            md = '回复序号选择已关注的微博：\n\n|序号|微博ID|备注|\n|----|----|----|\n'
            for index, item in enumerate(listens):
                md += '|{index}|{uid}|{name}|\n'.format(index=index + 1, **item)

            wait = await data.wait(Chain(data).markdown(md))
            if not wait:
                return None

            index = get_index_from_text(wait.text_digits, listens)
            if index is None:
                return None

            weibo = WeiboUser(listens[index]['uid'], setting)
    
    # 手动触发时重置错误状态（重新启用禁用的数据源）
    weibo.reset_error_state()

    message = data.text_digits
    index = 0
    r = re.search(r'(\d+)', message)
    if r:
        index = abs(int(r.group(1)))

    if '最新' in message:
        index = 1

    if index:
        return await send_by_index(index, weibo, data)
    else:
        blog_list = await weibo.get_blog_list()
        user_name = await weibo.get_user_name()

        if not blog_list:
            return Chain(data).text('博士...暂时无法获取微博列表呢...请稍后再试吧~')

        md = f'博士，这是【{user_name}】的微博列表，回复【序号】来获取详情吧\n\n|序号|日期|内容|\n|----|----|----|\n'
        for item in blog_list:
            md += '|{index}|{date}|{content}|\n'.format(**item)

        reply = Chain(data).markdown(md)
        wait = await data.wait(reply)
        if wait:
            r = re.search(r'(\d+)', wait.text_digits)
            if r:
                index = abs(int(r.group(1)))
                return await send_by_index(index, weibo, wait)

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
        supported_platforms = ['weibo', 'bilibili', 'netease-cloud-music', 'arknights-game', 'arknights-website']
        menu_text, index_map = aggregator_manager.generate_datasource_menu(supported_platforms)
        
        if not index_map:
            return Chain(data).text('暂无可用数据源')
        
        # 发送选择菜单
        reply = Chain(data).text(f"CeobeAPI聚合推送设置\n\n{menu_text}")
        
        # 等待用户选择（移除timeout参数）
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
        
        # 限制文本长度
        display_text = content.get_display_text(200)
        full_text = f"{header}\n\n{html.unescape(display_text)}"
        chain.text(full_text)
        
        # 处理媒体内容（下载图片）
        if content.media_urls:
            setting = attridict(bot.get_config('setting'))
            images_cache_dir = setting.get('imagesCache', 'log/weibo')
            
            # 下载并添加图片
            image_paths = []
            for url in content.media_urls[:9]:  # 最多9张图片
                path = await download_media_file(url, images_cache_dir)
                if path:
                    image_paths.append(path)
            
            if image_paths:
                if is_comwechat_instance(data.instance):
                    chain.image(image_paths)
                else:
                    chain.image(image_paths)
        
        # 添加原文链接
        if content.source_url and not isinstance(data.instance, QQGuildBotInstance):
            chain.text(f'\n\n原文链接: {content.source_url}')
        
        return chain
        
    except Exception as e:
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
        print(f"下载媒体文件失败: {e}")
        return None


@bot.timed_task(each=30)
async def _(_):
    listens: list = bot.get_config('listen')
    for listen in listens:
        user = listen.get('uid', '').strip()
        
        # 跳过空的或无效的UID
        if not user or user == '' or user == 'None':
            continue
            
        weibo = WeiboUser(user, attridict(bot.get_config('setting')))
        
        # 检查是否应该跳过这个数据源
        if weibo.disable_until_manual:
            continue  # 已禁用，等待手动重启
            
        if not weibo.should_retry():
            continue  # 尚未到重试时间

        new_id = await weibo.get_weibo_id(0)
        if not new_id:
            continue

        record = WeiboRecord.get_or_none(blog_id=new_id)
        if record:
            continue

        WeiboRecord.create(user_id=user, blog_id=new_id, record_time=int(time.time()))

        target: List[GroupSetting] = GroupSetting.select().where(GroupSetting.send_weibo == 1)
        if not target:
            continue

        time_rec = TimeRecorder()
        async_send_tasks = []

        result = await weibo.get_weibo_content(0)
        if not result:
            await send_to_console_channel(Chain().text(f'微博获取失败\nUSER: {user}\nID: {new_id}'))
            return

        # 添加调试日志（受debugCeobeAPI开关控制）
        if bot.get_config('setting').get('debugCeobeAPI', False):
            print(f"[微博推送调试] 获取到微博内容:")
            print(f"  用户: {result.user_name}")
            print(f"  文本长度: {len(result.html_text)}")
            print(f"  图片数量: pics_list={len(result.pics_list)}, pics_urls={len(result.pics_urls)}")
            print(f"  GIF数量: gif_list={len(result.gif_list)}, gif_urls={len(result.gif_urls)}")
            if result.pics_list:
                print(f"  图片文件:")
                for i, path in enumerate(result.pics_list):
                    exists = os.path.exists(path)
                    print(f"    [{i+1}] {path} (存在: {exists})")
            if result.gif_list:
                print(f"  GIF文件:")
                for i, path in enumerate(result.gif_list):
                    exists = os.path.exists(path)
                    print(f"    [{i+1}] {path} (存在: {exists})")

        send = True
        for regex in bot.get_config("block"):
            if re.match(regex, html.unescape(result.html_text)):
                await send_to_console_channel(
                    Chain().text(f'微博正文触发正则屏蔽，跳过推送\nUSER: {user}\nID: {new_id}')
                )
                send = False
                break
            if re.search(regex, html.unescape(result.html_text)):
                await send_to_console_channel(
                    Chain().text(f'微博正文触发搜索屏蔽，跳过推送\nUSER: {user}\nID: {new_id}')
                )
                send = False
                break

        if not send:
            continue

        await send_to_console_channel(
            Chain().text(f'开始推送微博\nUSER: {result.user_name}\nID: {new_id}\n目标数: {len(target)}')
        )

        for item in target:
            data = Chain()
            instance = main_bot[item.bot_id]
            if not instance:
                continue

            debug_enabled = bot.get_config('setting').get('debugCeobeAPI', False)
            if debug_enabled:
                print(f"[微博推送调试] 开始为 {item.group_id} 构建消息")
            
            data.text(f'来自 {result.user_name} 的最新微博\n\n{html.unescape(result.html_text)}')

            if isinstance(instance.instance, QQGuildBotInstance):
                if debug_enabled:
                    print(f"[微博推送调试] QQ频道模式")
                if not instance.instance.private:
                    # QQ频道公域，发送图片URL
                    if debug_enabled:
                        print(f"[微博推送调试] QQ频道公域，发送图片URL: {len(result.pics_urls)}张")
                    for url in result.pics_urls:
                        data.image(url=url)
                    # GIF以图片URL形式发送
                    if debug_enabled:
                        print(f"[微博推送调试] QQ频道公域，发送GIF URL: {len(result.gif_urls)}张")
                    for url in result.gif_urls:
                        data.image(url=url)
                else:
                    # QQ频道私域，发送本地图片文件
                    if debug_enabled:
                        print(f"[微博推送调试] QQ频道私域，发送本地图片文件: {len(result.pics_list)}张")
                    if result.pics_list:
                        data.image(result.pics_list)
                    # GIF以图片文件形式发送
                    if debug_enabled:
                        print(f"[微博推送调试] QQ频道私域，发送GIF文件: {len(result.gif_list)}张")
                    if result.gif_list:
                        data.image(result.gif_list)
            elif is_comwechat_instance(instance.instance):
                # ComWeChat平台
                if debug_enabled:
                    print(f"[微博推送调试] ComWeChat模式，发送图片文件: {len(result.pics_list)}张")
                if result.pics_list:
                    data.image(result.pics_list)
                # GIF使用Face元素发送，并在发送前检查是否需要压缩
                if debug_enabled:
                    print(f"[微博推送调试] ComWeChat模式，发送GIF文件: {len(result.gif_list)}张")
                if result.gif_list:
                    cache_dir = weibo.images_cache_dir  # 从weibo实例获取缓存目录
                    for gif_path in result.gif_list:
                        # 调用压缩函数
                        compressed_path = await compress_gif_for_wechat(gif_path, cache_dir)
                        data.face(compressed_path)  # 使用压缩后的路径发送
                data.text(f'\n\n{result.detail_url}')
            else:
                # 普通群聊，发送本地图片文件
                if debug_enabled:
                    print(f"[微博推送调试] 普通群聊模式，发送图片文件: {len(result.pics_list)}张")
                if result.pics_list:
                    data.image(result.pics_list)
                    if debug_enabled:
                        print(f"[微博推送调试] 已添加图片到消息链: {result.pics_list}")
                else:
                    if debug_enabled:
                        print(f"[微博推送调试] 没有图片需要发送")
                # GIF以图片文件形式发送
                if debug_enabled:
                    print(f"[微博推送调试] 普通群聊模式，发送GIF文件: {len(result.gif_list)}张")
                if result.gif_list:
                    data.image(result.gif_list)
                    if debug_enabled:
                        print(f"[微博推送调试] 已添加GIF到消息链: {result.gif_list}")
                else:
                    if debug_enabled:
                        print(f"[微博推送调试] 没有GIF需要发送")
                data.text(f'\n\n{result.detail_url}')

            if bot.get_config('sendAsync'):
                async_send_tasks.append(instance.send_message(data, channel_id=item.group_id))
            else:
                await instance.send_message(data, channel_id=item.group_id)
                await asyncio.sleep(bot.get_config('sendInterval'))

        if async_send_tasks:
            await asyncio.wait(async_send_tasks)

        await send_to_console_channel(Chain().text(f'微博推送结束:\n{new_id}\n耗时{time_rec.total()}'))


# ========== 聚合推送定时任务 ==========

@bot.timed_task(each=60)  # 聚合推送使用更长的间隔
async def aggregator_push_task(_):
    """聚合推送定时任务"""
    try:
        # 获取所有启用的订阅
        enabled_subscriptions = aggregator_manager.get_enabled_subscriptions()
        if not enabled_subscriptions:
            return
        
        # 收集所有订阅的数据源ID
        all_datasource_ids = set()
        for sub in enabled_subscriptions:
            all_datasource_ids.update(sub.get('datasource_ids', []))
        
        if not all_datasource_ids:
            return
        
        # 获取聚合内容
        raw_contents = await get_aggregated_content(list(all_datasource_ids))
        if not raw_contents:
            return
        
        # 处理每条内容
        for raw_data in raw_contents:
            await process_single_aggregated_content(raw_data, enabled_subscriptions)
            
    except Exception as e:
        print(f"聚合推送任务失败: {e}")


async def process_single_aggregated_content(raw_data: dict, subscriptions: List[dict]):
    """处理单条聚合内容"""
    try:
        # 转换为统一格式
        content = adapt_content_to_unified(raw_data)
        if not content:
            return
        
        # 检查是否已推送过
        existing_record = AggregatorRecord.get_or_none(
            AggregatorRecord.content_id == content.content_id,
            AggregatorRecord.platform == content.platform
        )
        if existing_record:
            return
        
        # 查找订阅了该数据源的群组
        target_subscriptions = []
        for sub in subscriptions:
            if content.source_id in sub.get('datasource_ids', []):
                target_subscriptions.append(sub)
        
        if not target_subscriptions:
            return
        
        # 内容屏蔽检查
        for regex in bot.get_config("block", []):
            if re.match(regex, html.unescape(content.text)) or re.search(regex, html.unescape(content.text)):
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
                
                # 使用与手动获取一致的文本处理方式
                display_text = content.get_display_text(200)
                full_text = f"{header}\n\n{html.unescape(display_text)}"
                chain.text(full_text)
                
                # 处理图片
                if content.media_urls:
                    setting = attridict(bot.get_config('setting'))
                    cache_dir = setting.get('imagesCache', 'log/weibo')
                    
                    image_paths = []
                    for url in content.media_urls[:9]:  # 最多9张图片
                        path = await download_media_file(url, cache_dir)
                        if path:
                            image_paths.append(path)
                    
                    if image_paths:
                        if is_comwechat_instance(instance.instance):
                            chain.image(image_paths)
                        else:
                            chain.image(image_paths)
                
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
                print(f"发送到群组 {subscription['group_id']} 失败: {e}")
        
        if send_tasks:
            await asyncio.wait(send_tasks)
        
        await send_to_console_channel(
            Chain().text(f'聚合推送完成\nID: {content.content_id}\n耗时: {time_rec.total()}')
        )
        
    except Exception as e:
        print(f"处理聚合内容失败: {e}")
