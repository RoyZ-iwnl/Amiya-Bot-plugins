import random

from amiyabot.adapters.mirai import MiraiBotInstance
from amiyabot.adapters.cqhttp import CQHttpBotInstance
from amiyabot.adapters.comwechat import ComWeChatBotInstance

from core import Event
from core.util import any_match
from core.lib.baiduCloud import BaiduCloud
from core.database.user import *

from .mainBot import *

curr_dir = os.path.dirname(__file__)
config_path = 'resource/plugins/baiduCloud.yaml'

if not os.path.exists(config_path):
    create_dir(config_path, is_file=True)
    shutil.copy(f'{curr_dir}/baiduCloud.yaml', config_path)

baidu = BaiduCloud(read_yaml(config_path))


def is_comwechat_instance(instance):
    """检测是否为ComWeChat实例"""
    return str(instance) == 'ComWeChat'

async def get_user_nickname(data: Message):
    """获取用户昵称，兼容comwechat实例"""
    # 检查是否为comwechat实例且没有nickname
    if is_comwechat_instance(data.instance):
        try:
            # 使用comwechat接口获取群成员昵称
            res = await data.instance.api.post(
                '/',
                {
                    'action': 'wx.get_groupmember_nickname',
                    'params': {
                        'group_id': data.group_id,
                        'user_id': data.user_id
                    },
                },
            )
            
            # 检查响应状态并返回昵称
            if res.get('status') == 'ok' and res.get('retcode') == 0:
                return res.get('data', '用户')
            else:
                # 如果获取失败，返回默认值
                return '用户'
        except Exception as e:
            # 异常处理，返回默认值
            print(f"获取comwechat昵称失败: {e}")
            return '用户'
    else:
        # 非comwechat实例或已有nickname，使用原来的nickname
        return getattr(data, 'nickname', '用户')


@table
class PokeLock(UserBaseModel):
    group_id: str = CharField()


@table
class UserCustom(UserBaseModel):
    user_id: Union[CharField, str] = CharField()
    custom_nickname: Union[CharField, str] = CharField(null=True)

    @classmethod
    def get_nickname(cls, user_id: str):
        custom: UserCustom = cls.get_or_none(user_id=user_id)
        if custom and custom.custom_nickname:
            idx = str(custom.id)
            id_suffix = ('0' * (4 - len(idx)) + idx) if len(idx) < 4 else idx

            return f'{custom.custom_nickname}#{id_suffix}'


@bot.on_message(group_id='user', keywords=['昵称'], level=10)
async def _(data: Message):
    if '删除昵称' in data.text_original:
        user: UserCustom = UserCustom.get_or_none(user_id=data.user_id)
        if user:
            user.custom_nickname = ''
            user.save()

        return Chain(data).text('自定义昵称已删除')

    r = re.search(r'(/)?昵称(.*)', data.text_original)
    if r:
        nickname = r.group(2).strip()

        if not nickname:
            return Chain(data).text('博士，请使用“昵称 + 文字”的格式修改昵称哦！')

        if len(nickname) > 10:
            return Chain(data).text('博士，昵称长度不能超过 10 个字 >.<')

        if baidu.enable:
            await data.send(Chain(data).text('昵称审核中，请稍等...'))

            check = await baidu.text_censor(nickname)

            if not check:
                return Chain(data).text('审核失败...')

            if check['conclusionType'] == 2:
                text = '审核不通过！检测到以下违规内容：\n'
                for item in check['data']:
                    text += item['msg'] + '\n'

                return Chain(data).text(text)

        user: UserCustom = UserCustom.get_or_none(user_id=data.user_id)
        if user:
            user.custom_nickname = nickname
            user.save()
        else:
            UserCustom.create(user_id=data.user_id, custom_nickname=nickname)

        return Chain(data).text(f'审核通过！你好，{UserCustom.get_nickname(data.user_id)}')
    else:
        return Chain(data).text('博士，请正确使用指令设置昵称哦~')


@bot.on_message(group_id='user', verify=only_name)
async def echo(data: Message = None):
    actions = [
        lambda dt: Chain(dt).text(random.choice(talking.touch)),
        lambda dt: Chain(dt, at=False).image(random.choice(get_face())),
    ]

    return random.choice(actions)(data)


@bot.on_message(
    group_id='user',
    verify=compose_talk_verify(talking.talk.positive, talking.call.positive, 'enable_positive'),
)
async def _(data: Message):
    user: UserInfo = UserInfo.get_user(data.user_id)
    reply = Chain(data)

    if user.user_mood == 0:
        text = '阿米娅这次就原谅博士吧，博士要好好对阿米娅哦[face:21]'
    else:
        text = random.choice(talking.touch)

    setattr(reply, 'feeling', 5)
    return reply.text(text, auto_convert=False)


@bot.on_message(
    group_id='user',
    verify=compose_talk_verify(talking.talk.inactive, talking.call.positive, 'enable_inactive'),
)
async def _(data: Message):
    user: UserInfo = UserInfo.get_user(data.user_id)
    reply = Chain(data)
    setattr(reply, 'feeling', -5)

    if user.user_mood - 5 <= 0:
        return reply.text('(阿米娅没有应答...似乎已经生气了...)')

    anger = int((1 - (user.user_mood - 5 if user.user_mood - 5 >= 0 else 0) / 15) * 100)

    return reply.text(f'博士为什么要说这种话，阿米娅要生气了！[face:67]（怒气值：{anger}%）')


@bot.on_message(
    group_id='user',
    verify=check_keywords(list(talking.call.inactive), 'enable_inactive'),
    check_prefix=False,
)
async def _(data: Message):
    bad_word = any_match(data.text, list(talking.call.inactive))
    text = f'哼！Dr. {data.nickname}不许叫人家{bad_word}，不然人家要生气了！'

    reply = Chain(data).text(text)
    setattr(reply, 'feeling', -5)

    return reply


@bot.on_message(
    group_id='user',
    verify=check_keywords(['我错了', '对不起', '抱歉'], 'enable_inactive'),
)
async def _(data: Message):
    info: UserInfo = UserInfo.get_user(data.user_id)

    reply = Chain(data)
    setattr(reply, 'feeling', 5)
    setattr(reply, 'unlock', True)

    if not info or info.user_mood >= 15:
        return reply.text('博士为什么要这么说呢，嗯……博士是不是偷偷做了对不起阿米娅的事[face:181]')
    else:
        return reply.text('好吧，阿米娅就当博士刚刚是在开玩笑吧，博士要好好对阿米娅哦[face:21]')


@bot.on_message(
    group_id='user',
    verify=check_keywords(['早上好', '早安', '中午好', '午安', '下午好', '晚上好'], 'enable_greeting'),
)
async def _(data: Message):
    hour = talk_time()
    text = ''
    if hour:
        text += f'Dr. {data.nickname}，{hour}好～'
    else:
        text += f'Dr. {data.nickname}，这么晚还不睡吗？要注意休息哦～'

    status = sign_in(data)
    if status['status']:
        text += status['text']

    return Chain(data).text(text)


@bot.on_message(
    group_id='user',
    verify=check_keywords(['晚安'], 'enable_greeting'),
)
async def _(data: Message):
    return Chain(data).text(f'Dr. {data.nickname}，晚安～')


@bot.on_message(group_id='user', keywords=['签到'])
async def _(data: Message):
    status = sign_in(data, 1)

    reply = await user_info(data)

    return reply.text(status['text'])


@bot.on_message(group_id='user', keywords=['我的信息', '个人信息'])
async def _(data: Message):
    return await user_info(data)


@bot.on_message(group_id='user', keywords=['开启戳一戳', '关闭戳一戳'])
async def _(data: Message):
    if '开启' in data.text:
        PokeLock.delete().where(PokeLock.group_id == data.channel_id).execute()
        return '已开启戳一戳'
    else:
        PokeLock.create(group_id=data.channel_id)
        return '已关闭戳一戳'

# mirai 适配器戳一戳处理
@bot.on_event('NudgeEvent')
async def _(event: Event, instance: MiraiBotInstance):
    if (str(event.data['target']) == instance.appid and 
        not PokeLock.get_or_none(group_id=event.data['subject']['id'])):
        
        if random.randint(0, 10) >= 6:
            await instance.api.send_nudge(
                event.data['fromId'], 
                event.data['subject']['id']
            )
        else:
            await instance.send_message(
                await echo(), 
                event.data['fromId'], 
                event.data['subject']['id']
            )

# cqhttp 适配器戳一戳处理
@bot.on_event('notice.notify.poke')
async def _(event: Event, instance: CQHttpBotInstance):
    if (str(event.data['target_id']) == instance.appid and 
        not PokeLock.get_or_none(group_id=event.data['group_id'])):
        
        if random.randint(0, 10) >= 6:
            await instance.api.send_nudge(
                event.data['user_id'], 
                event.data['group_id']
            )
        else:
            await instance.send_message(
                await echo(), 
                event.data['user_id'], 
                event.data['group_id']
            )

# comwechat 适配器戳一戳处理
@bot.on_event('notice.wx.get_group_poke')
async def _(event: Event, instance: ComWeChatBotInstance):
    #print("\n[戳一戳调试] ===== 收到 comwechat 戳一戳事件 =====")
    #print(f"[戳一戳调试] 原始事件数据: {event.data}")

    target_user_id = event.data.get('user_id')
    # 使用事件数据中的真实机器人微信ID，而不是 instance.appid
    bot_id = event.data.get('self', {}).get('user_id')
    is_bot_poked = str(target_user_id) == str(bot_id)

    group_id = event.data.get('group_id')
    lock = PokeLock.get_or_none(group_id=group_id)
    is_locked = lock is not None

    #print(f"[戳一戳调试] 群组ID(group_id): {group_id}")
    #print(f"[戳一戳调试] 检查是否戳机器人: {is_bot_poked} (目标ID: {target_user_id}, 机器人真实ID: {bot_id})")
    #print(f"[戳一戳调试] 检查该群功能是否已关闭: {is_locked}")

    # 正确逻辑：当功能未被锁定时（not is_locked）
    if is_bot_poked and not is_locked:
        print("[戳一戳调试] ✅ 条件满足，准备执行回复操作。")
        if random.randint(0, 10) >= 6:
            print("[戳一戳调试] 准备发送随机消息...")
            await instance.send_message(
                await echo(),
                event.data['from_user_id'],
                event.data['group_id']
            )
        else:
            print("[戳一戳调试] 准备发送固定文本消息...")
            await instance.send_message(
                await echo(),
                event.data['from_user_id'],
                event.data['group_id']
            )
    else:
        print("[戳一戳调试] ❌ 条件不满足，忽略本次事件。")
        if not is_bot_poked:
            print(f"[戳一戳调试] 原因：不是戳机器人 (目标: {target_user_id}, 机器人: {bot_id})")
        if is_locked:
            print(f"[戳一戳调试] 原因：该群戳一戳功能已关闭")
    print("[戳一戳调试] ================= 事件处理结束 =================\n")



@bot.message_created
async def _(data: Message, _):
    # 先处理comwechat实例的nickname获取
    if is_comwechat_instance(data.instance) and not hasattr(data, 'nickname'):
        data.nickname = await get_user_nickname(data)
    
    # 然后处理自定义昵称（保持原有逻辑）
    custom_nickname = UserCustom.get_nickname(data.user_id)
    if custom_nickname:
        data.nickname = custom_nickname

    return data


@bot.message_before_handle
async def _(data: Message, factory_name: str, _):
    user: UserInfo = UserInfo.get_user(data.user_id)

    if user.user_id.black == 1:
        return False

    if user.user_mood <= 0 and not any_match(data.text, ['我错了', '对不起', '抱歉']):
        if data.is_at or data.is_at_all or data.text.startswith('兔兔') or data.text.startswith('阿米娅'):
            await data.send(Chain(data).text('哼~阿米娅生气了！不理博士！[face:38]'))
        return False

    return True


@bot.message_after_send
async def _(data: Chain, factory_name: str, _):
    if not data.data:
        return

    user_id = data.data.user_id

    if not User.get_or_none(user_id=user_id):
        return None

    user: UserInfo = UserInfo.get_user(user_id)

    feeling = 2
    if hasattr(data, 'feeling'):
        feeling = getattr(data, 'feeling')

    if user.user_mood <= 0 and not hasattr(data, 'unlock'):
        feeling = 0

    user_mood = user.user_mood + feeling
    if user_mood <= 0:
        user_mood = 0
    if user_mood >= 15:
        user_mood = 15

    User.update(nickname=data.data.nickname, message_num=User.message_num + 1).where(User.user_id == user_id).execute()

    UserInfo.update(
        user_mood=user_mood,
        user_feeling=UserInfo.user_feeling + feeling,
    ).where(UserInfo.user_id == user_id).execute()
