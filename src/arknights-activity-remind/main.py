import os
from datetime import datetime
import asyncio
import json
import re

from core import send_to_console_channel, Message, Chain, AmiyaBotPluginInstance, Requirement, bot as main_bot, log
from core.database.group import GroupSetting
from core.database.messages import *
from core.database import config, is_mysql

from amiyabot import event_bus
from amiyabot.database import *
from amiyabot.factory import BotHandlerFactory




db = connect_database('activity_remind' if is_mysql else 'database/activity_remind.db', is_mysql, config)


class GroupBaseModel(ModelClass):
    class Meta:
        database = db

@table
class GroupSetting(GroupBaseModel):
    group_id: str = CharField(primary_key=True)
    bot_id: str = CharField(null=True)
    activity_remind: int = IntegerField(default=0, null=True)

curr_dir = os.path.dirname(__file__)

gamedata_path = 'resource/gamedata'

class RemindPluginInstance(AmiyaBotPluginInstance):
    def install(self):
        asyncio.create_task(init_actlist())

    def uninstall(self):
        event_bus.unsubscribe('gameDataInitialized', update)

    @staticmethod
    async def get_remind_list(filter_type=None):
        if filter_type is not None:
            # 当提供了filter_type参数时，只返回type在filter_type列表中的项
            # type包括：'活动','危机合约', '新主题曲','集成战略','生息演算','保全派驻周期','卡池'
            if isinstance(filter_type, list):
                # 如果filter_type是列表
                res = [d for d in remind_list if d.get('type') in filter_type]
            else:
                # 如果filter_type是单个值
                res = [d for d in remind_list if d.get('type') == filter_type]
        else:
            # 当没有提供filter_type参数时，按照插件设置返回
            res = [
                d for d in remind_list 
                if not (
                    (d.get('type') == '卡池' and not bot.get_config('sendGachaPoolRemind')) or 
                    (d.get('type') == '保全派驻周期' and not bot.get_config('sendTowerSeasonRemind'))
                )
            ]
        return res


@event_bus.subscribe('gameDataInitialized')
def update(_):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        bot.install()

bot = RemindPluginInstance(
    name='明日方舟活动提醒',
    version='2.0',
    plugin_id='arknights-activity-remind',
    plugin_type='', 
    description='提醒活动的开启关闭时间',
    document=f'{curr_dir}/README.md',
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml',
    requirements=[Requirement('amiyabot-arknights-gamedata', official=True)],
)

class JsonData:
    cache = {}

    @classmethod
    def get_json_data(cls, name: str, folder: str = 'excel'):
        if name not in cls.cache:
            path = f'resource/gamedata/gamedata/{folder}/{name}.json'
            if os.path.exists(path):
                with open(path, mode='r', encoding='utf-8') as src:
                    cls.cache[name] = json.load(src)
            else:
                return {}

        return cls.cache[name]

    @classmethod
    def clear_cache(cls, name: str = None):
        if name:
            del cls.cache[name]
        else:
            cls.cache = {}


async def init_actlist():
    log.info('building activity list...')
    JsonData.clear_cache()

    global remind_list
    remind_list = []
    now = int(datetime.now().timestamp())
    activeList = JsonData.get_json_data('activity_table')['basicInfo']
    actThemes = JsonData.get_json_data('activity_table')['actThemes']
    crisisSeasons = JsonData.get_json_data('crisis_v2_table')['seasonInfoDataMap']
    gachaPool = JsonData.get_json_data('gacha_table')['gachaPoolClient']
    towerSeason = JsonData.get_json_data('climb_tower_table')['seasonInfos']

    # 活动提醒
    for active in activeList.values():
        if active['startTime'] >= now:
            remind_list.append({'timestamp': active['startTime'], 'type': '新主题曲' if active['type'] == 'TYPE_MAINSS' else '活动', 'name': active['name'], 'time_str': datetime.fromtimestamp(active['startTime']).strftime('%Y-%m-%d %H:%M'), 'remind_type': '开始'})
        if active['endTime'] >= now:
            remind_list.append({'timestamp': active['endTime'], 'type': '活动', 'name': active['name'], 'time_str': datetime.fromtimestamp(active['endTime']).strftime('%Y-%m-%d %H:%M'),'remind_type': '结束'})
        if active['rewardEndTime'] >= now and active['rewardEndTime'] != active['endTime']:
            remind_list.append({'timestamp': active['rewardEndTime'], 'type': '活动', 'name': active['name'], 'node':'奖励兑换', 'time_str': datetime.fromtimestamp(active['rewardEndTime']).strftime('%Y-%m-%d %H:%M'),'remind_type': '结束'})

    # 活动节点提醒
    for item in actThemes:
        act_name = ''
        act_type = '活动'
        pattern = r"<([^>]*)>"
        match = re.search(pattern, item['timeNodes'][0]['title'])
        if match:
            act_name = match.group(1)
        ACT_TYPE_MAP = {
                'ACTIVITY': '活动',
                '1': '活动',
                'CRISIS': '危机合约',
                '2': '危机合约',
                '5': '危机合约',
                'MAINLINE': '新主题曲',
                '3': '新主题曲',
                '7': '新主题曲',
                'ROGUELIKE': '集成战略',
                '4': '集成战略',
                'SANDBOX': '生息演算',
                '6': '生息演算',
            }
        
        for type in ACT_TYPE_MAP.keys():
            if type in str(item['type']):
                act_type = ACT_TYPE_MAP[type]
                break
        
        for i, node in enumerate(item['timeNodes']):
            if node['ts'] >= now:
                result = {'timestamp': node['ts'], 'type': act_type, 'name': act_name, 'time_str': datetime.fromtimestamp(node['ts']).strftime('%Y-%m-%d %H:%M'),'remind_type': '开始'}
                if i > 0 :
                    result['node'] = node['title'].replace('已开放','') 
                    result['remind_type'] = '开放'
                remind_list.append(result)
    
    # 危机合约提醒
    for crisis in crisisSeasons.values():
        if crisis['startTs'] >= now:
            remind_list.append({'timestamp': crisis['startTs'], 'type': '危机合约', 'name': crisis['name'], 'time_str': datetime.fromtimestamp(crisis['startTs']).strftime('%Y-%m-%d %H:%M'),'remind_type': '开始'})
        if crisis['endTs'] >= now:
            remind_list.append({'timestamp': crisis['endTs'], 'type': '危机合约', 'name': crisis['name'], 'time_str': datetime.fromtimestamp(crisis['endTs']).strftime('%Y-%m-%d %H:%M'),'remind_type': '结束'})

    # 卡池提醒
    IGNOREPOOL_LIST = {'NORMAL', '0','CLASSIC', '4', 'FESCLASSIC', '6', 'CLASSIC_DOUBLE', '10'}
    # NORMAL(0)：常驻标准寻访；CLASSIC(4)：中坚寻访；
    # SINGLE(5)：单UP卡池； DOUBLE(9)：双UP卡池；
    # LIMITED(1): 限定卡池; LINKAGE(2): 联动卡池;
    # ATTAIN(3): 跨年欢庆; FESCLASSIC(6)：中坚甄选；SPECIAL(8): 定向甄选; CLASSIC_DOUBLE(10): 中坚选调;

    for pool in gachaPool:
        if pool['gachaRuleType'] in IGNOREPOOL_LIST:
            continue
        if pool['gachaPoolName'] == "适合多种场合的强力干员":
            continue
        if pool['openTime'] >= now:
            remind_list.append({'timestamp': pool['openTime'], 'type': '卡池', 'name': pool['gachaPoolName'], 'time_str': datetime.fromtimestamp(pool['openTime']).strftime('%Y-%m-%d %H:%M'),'remind_type': '开始'})
        if pool['endTime'] >= now:
            remind_list.append({'timestamp': pool['endTime'], 'type': '卡池', 'name': pool['gachaPoolName'], 'time_str': datetime.fromtimestamp(pool['endTime']).strftime('%Y-%m-%d %H:%M'),'remind_type': '结束'})

    # 保全派驻周期提醒
    for season in towerSeason.values():
        if season['startTs'] >= now:
            remind_list.append({'timestamp': season['startTs'], 'type': '保全派驻周期', 'name': season['name'], 'time_str': datetime.fromtimestamp(season['startTs']).strftime('%Y-%m-%d %H:%M'),'remind_type': '开始'})
        if season['endTs'] >= now:
            remind_list.append({'timestamp': season['endTs'], 'type': '保全派驻周期', 'name': season['name'], 'time_str': datetime.fromtimestamp(season['endTs']).strftime('%Y-%m-%d %H:%M'),'remind_type': '结束'})

    # 去除重复字典并按时间戳排序
    seen = set()
    unique_list = []
    for d in remind_list:
        dict_repr = frozenset(d.items())
        if dict_repr not in seen:
            seen.add(dict_repr)
            unique_list.append(d)
    unique_list.sort(key=lambda x: x['timestamp'])    
    remind_list = unique_list


@bot.on_message(group_id='remind', keywords=['开启活动提醒'], level=5)
async def _(data: Message):
    if not data.is_admin:
        return Chain(data).text('抱歉，活动提醒只能由管理员设置')

    channel: GroupSetting = GroupSetting.get_or_none(
        group_id=data.channel_id, bot_id=data.instance.appid
    )
    if channel:
        GroupSetting.update(activity_remind=1).where(
            GroupSetting.group_id == data.channel_id,
            GroupSetting.bot_id == data.instance.appid,
        ).execute()
    else:
        if GroupSetting.get_or_none(group_id=data.channel_id):
            GroupSetting.update(bot_id=data.instance.appid, activity_remind=1).where(
                GroupSetting.group_id == data.channel_id
            ).execute()
        else:
            GroupSetting.create(
                group_id=data.channel_id, bot_id=data.instance.appid, activity_remind=1
            )

    return Chain(data).text('已在本群开启活动提醒')


@bot.on_message(group_id='remind', keywords=['关闭活动提醒'], level=5)
async def _(data: Message):
    if not data.is_admin:
        return Chain(data).text('抱歉，活动提醒只能由管理员设置')

    GroupSetting.update(activity_remind=0).where(GroupSetting.group_id == data.channel_id,
                                            GroupSetting.bot_id == data.instance.appid).execute()

    return Chain(data).text('已在本群关闭活动提醒')


@bot.on_message(group_id='remind', keywords=['活动列表'], allow_direct=True, level=5)
async def _(data: Message):
    description = ""
    for remind in remind_list:
        if not bot.get_config('sendGachaPoolRemind'):
            if remind['type'] == '卡池':
                continue
        if not bot.get_config('sendTowerSeasonRemind'):
            if remind['type'] == '保全派驻周期':
                continue
        node_info = f" {remind['node']}" if remind.get('node') else ''
        description += f"{remind['type']} 【{remind['name']}】 {node_info} 将于\n{remind['time_str']} {remind['remind_type']}\n\n"
    return Chain(data).text(description)


@bot.timed_task(each=60)
async def _(instance: BotHandlerFactory):
    target_groups = list(GroupSetting.select().where(GroupSetting.activity_remind == 1))
    if not target_groups:
        return
    
    now = datetime.now().replace(second=0, microsecond=0)
    send_async = bot.get_config('sendAsync')
    send_interval = bot.get_config('sendInterval')
    send_gacha_pool_remind = bot.get_config('sendGachaPoolRemind')
    send_tower_season_remind = bot.get_config('sendTowerSeasonRemind')
    send_realtime_remind = bot.get_config('sendRealtimeRemind')

    # 预计算发送时间配置
    send_time_configs = []
    for item in bot.get_config('sendTime'):
        item_time = datetime.strptime(item.get('time'), '%H:%M:%S').time().replace(second=0)
        send_time_configs.append({
            'time': item_time,
            'forward': item.get('forward'),
            'remind_type': item.get('remindType')
        })

    realtime_content = ""
    scheduled_groups = {}
    
    for remind in remind_list:
        if not send_gacha_pool_remind and remind['type'] == '卡池':
            continue
        if not send_tower_season_remind and remind['type'] == '保全派驻周期':
            continue
            
        remind_time = datetime.fromtimestamp(remind['timestamp'])
        
        # 处理实时提醒
        if send_realtime_remind:
            # 4点的通知改到10点
            adjusted_remind_time = remind_time.replace(hour=10) if remind_time.timetuple().tm_hour == 4 else remind_time
            
            if adjusted_remind_time == now:
                node_info = f" {remind['node']}" if remind.get('node') else ''
                realtime_content += f"{remind['type']} 【{remind['name']}】{node_info} {remind['remind_type']}\n"
        
        # 处理定时提醒 - 按配置分组
        current_time = now.time()
        for item_config in send_time_configs:
            if current_time == item_config['time']:
                time_diff = remind_time - now
                if remind_time >= now and time_diff.days == item_config['forward']:
                    how_long = ''                    
                    if item_config['forward'] == 0:
                        hours_ahead = time_diff.seconds // 3600
                        if hours_ahead > 0:
                            how_long = f"{hours_ahead}小时后"
                        else:
                            how_long = f"{time_diff.seconds // 60}分钟后"
                    else: 
                        how_long = f"{item_config['forward']}天后"
                    
                    node_info = f" {remind['node']}" if remind.get('node') else ''
                    remind_text = f"{remind['type']} 【{remind['name']}】{node_info} 将于{how_long}{remind['remind_type']}\n"
                    
                    remind_type = item_config['remind_type']
                    
                    if remind_type not in scheduled_groups:
                        scheduled_groups[remind_type] = ""                
                    scheduled_groups[remind_type] += remind_text

    async_send_tasks = []
    
    # 发送实时提醒
    if realtime_content:
        await send_to_console_channel(Chain().text(f'开始发送实时提醒，目标群数: {len(target_groups)}'))        
        for target_item in target_groups:
            data = Chain().text(realtime_content)
            instance = main_bot[target_item.bot_id]
            if not instance:
                continue
                
            if send_async:
                async_send_tasks.append(instance.send_message(data, channel_id=target_item.group_id))
            else:
                await instance.send_message(data, channel_id=target_item.group_id)
                if scheduled_groups:
                    await asyncio.sleep(send_interval)

    # 按配置分组发送定时提醒
    if scheduled_groups:
        await send_to_console_channel(Chain().text(f'开始发送定时提醒，目标群数: {len(target_groups)}'))
        
        for remind_type, content in scheduled_groups.items():            
            if not content:
                continue
                
            for target_item in target_groups:
                data = Chain().text(content)
                instance = main_bot[target_item.bot_id]
                if not instance:
                    continue

                # 应用当前配置组的提醒类型
                if remind_type == '@所有人':
                    data.at_all()

                send_count = 3 if remind_type == '连发三遍' else 1
                
                for i in range(send_count):
                    if send_async:
                        async_send_tasks.append(instance.send_message(data, channel_id=target_item.group_id))
                    else:
                        await instance.send_message(data, channel_id=target_item.group_id)
                        if not (i == send_count - 1 and target_item == target_groups[-1]):
                            await asyncio.sleep(send_interval)

    if async_send_tasks:
        await asyncio.wait(async_send_tasks)
