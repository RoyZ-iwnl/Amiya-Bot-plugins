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
from core.util import TimeRecorder, AttrDict, find_most_similar
from core import send_to_console_channel, Message, Chain, AmiyaBotPluginInstance, bot as main_bot

from .helper import WeiboUser

curr_dir = os.path.dirname(__file__)


class WeiboPluginInstance(AmiyaBotPluginInstance): ...


bot = WeiboPluginInstance(
    name='明日方舟微博推送',
    version='3.2',
    plugin_id='amiyabot-weibo',
    plugin_type='official',
    description='在明日方舟相关官微更新时自动推送到群',
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
                cache_dir = weibo.images_cache_dir # 从weibo实例获取缓存目录
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
    setting = AttrDict(bot.get_config('setting'))

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


@bot.timed_task(each=30)
async def _(_):
    listens: list = bot.get_config('listen')
    for listen in listens:
        user = listen['uid']
        weibo = WeiboUser(user, AttrDict(bot.get_config('setting')))
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

            data.text(f'来自 {result.user_name} 的最新微博\n\n{html.unescape(result.html_text)}')

            if isinstance(instance.instance, QQGuildBotInstance):
                if not instance.instance.private:
                    # QQ频道公域，发送图片URL
                    for url in result.pics_urls:
                        data.image(url=url)
                    # GIF以图片URL形式发送
                    for url in result.gif_urls:
                        data.image(url=url)
                else:
                    # QQ频道私域，发送本地图片文件
                    if result.pics_list:
                        data.image(result.pics_list)
                    # GIF以图片文件形式发送
                    if result.gif_list:
                        data.image(result.gif_list)
            elif is_comwechat_instance(instance.instance):
                # ComWeChat平台
                if result.pics_list:
                    data.image(result.pics_list)
                # GIF使用Face元素发送，并在发送前检查是否需要压缩
                if result.gif_list:
                    cache_dir = weibo.images_cache_dir # 从weibo实例获取缓存目录
                    for gif_path in result.gif_list:
                        # 调用压缩函数
                        compressed_path = await compress_gif_for_wechat(gif_path, cache_dir)
                        data.face(compressed_path) # 使用压缩后的路径发送
                data.text(f'\n\n{result.detail_url}')
            else:
                # 普通群聊，发送本地图片文件
                if result.pics_list:
                    data.image(result.pics_list)
                # GIF以图片文件形式发送
                if result.gif_list:
                    data.image(result.gif_list)
                data.text(f'\n\n{result.detail_url}')

            if bot.get_config('sendAsync'):
                async_send_tasks.append(instance.send_message(data, channel_id=item.group_id))
            else:
                await instance.send_message(data, channel_id=item.group_id)
                await asyncio.sleep(bot.get_config('sendInterval'))

        if async_send_tasks:
            await asyncio.wait(async_send_tasks)

        await send_to_console_channel(Chain().text(f'微博推送结束:\n{new_id}\n耗时{time_rec.total()}'))
