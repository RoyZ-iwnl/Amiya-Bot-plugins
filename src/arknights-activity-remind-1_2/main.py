import os
from datetime import datetime
import asyncio
import json

from core.database.group import GroupSetting
from core.database.messages import *
from amiyabot import event_bus
from core import send_to_console_channel, Message, Chain, AmiyaBotPluginInstance, bot as main_bot, log

from amiyabot.database import *
from core.database import config, is_mysql

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
    version='1.2',
    plugin_id='arknights-activity-remind',
    plugin_type='', 
    description='提醒活动的开启关闭时间',
    document=f'{curr_dir}/README.md',
    global_config_schema=f'{curr_dir}/config_schema.json',
    global_config_default=f'{curr_dir}/config_default.yaml'
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


poolIgnore = ["NORMAL", "CLASSIC", "FESCLASSIC", "CLASSIC_DOUBLE"]
#NORMAL：常驻标准寻访；CLASSIC：中坚寻访；FESCLASSIC：中坚甄选；CLASSIC_DOUBLE: 中坚选调
#限定卡池："LIMITED","LINKAGE"
async def init_actlist():
    log.info('building activity list...')
    JsonData.clear_cache()
    global actThemes, crisisSeasons, activeList, gachaPool, towerSeason
    actThemes = JsonData.get_json_data('activity_table')['actThemes']
    crisisSeasons = JsonData.get_json_data('crisis_v2_table')['seasonInfoDataMap']
    activeList = JsonData.get_json_data('activity_table')['basicInfo']
    gachaPool = JsonData.get_json_data('gacha_table')['gachaPoolClient']
    towerSeason = JsonData.get_json_data('climb_tower_table')['seasonInfos']
    return

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
    now = datetime.now().replace(second = 0, microsecond = 0)
    # now = now.replace(day = 5)
    description = ""
    des_dict_temp = []
    for active in activeList.values():
        if datetime.fromtimestamp(active['startTime']) >= now:
            des_dict_temp.append({active['startTime']: "活动 【" + active['name'] + "】 即将于\n" + datetime.fromtimestamp(active['startTime']).strftime('%Y-%m-%d %H:%M') + " 开始。\n"})
        if datetime.fromtimestamp(active['endTime']) >= now:
            des_dict_temp.append({active['endTime']: "活动 【" + active['name'] + "】 即将于\n" + datetime.fromtimestamp(active['endTime']).strftime('%Y-%m-%d %H:%M') + " 结束。\n"})
        if active['endTime'] != active['rewardEndTime'] and datetime.fromtimestamp(active['rewardEndTime']) >= now:
            des_dict_temp.append({active['rewardEndTime']: "活动 【" + active['name'] + "】 的奖励兑换 即将于\n" + datetime.fromtimestamp(active['rewardEndTime']).strftime('%Y-%m-%d %H:%M') + " 结束。\n"})

    for crisis in crisisSeasons.values():
        if datetime.fromtimestamp(crisis['startTs']) >= now:
            des_dict_temp.append({crisis['startTs']: "危机合约" + crisis['crisisV2SeasonCode'] + " 【" + crisis['name'] + "】 即将于\n" + datetime.fromtimestamp(crisis['startTs']).strftime('%Y-%m-%d %H:%M') + " 开始。\n"})
        if datetime.fromtimestamp(crisis['endTs']) >= now:
            des_dict_temp.append({crisis['endTs']: "危机合约" + crisis['crisisV2SeasonCode'] + " 【" + crisis['name'] + "】 即将于\n" + datetime.fromtimestamp(crisis['endTs']).strftime('%Y-%m-%d %H:%M') + " 结束。\n"})

    for item in actThemes:
        for i, node in enumerate(item['timeNodes']):
            if i > 0 and datetime.fromtimestamp(node['ts']) >= now:
                des_dict_temp.append({node['ts']: item['timeNodes'][0]['title'].replace('活动已开放','') + " 即将于\n" + datetime.fromtimestamp(node['ts']).strftime('%Y-%m-%d %H:%M') + " " + (('开放' + node['title'].replace('已开放','')) if '已开放' in node['title'] else node['title']) + "\n"})
            elif datetime.fromtimestamp(node['ts']) >= now and '新主题曲' in node['title']:
                des_dict_temp.append({node['ts']: node['title'].replace('已开放','') + " 即将于\n" + datetime.fromtimestamp(node['ts']).strftime('%Y-%m-%d %H:%M') + " 开放。\n"})
            elif datetime.fromtimestamp(node['ts']) >= now and '集成战略' in node['title']:
                des_dict_temp.append({node['ts']: node['title'].replace('已开启','') + " 即将于\n" + datetime.fromtimestamp(node['ts']).strftime('%Y-%m-%d %H:%M') + " 开启。\n"})
                
    if bot.get_config('sendGachaPoolRemind'):
        for pool in gachaPool:
            if pool['gachaRuleType'] in poolIgnore:
                continue
            if pool['gachaPoolName'] == "适合多种场合的强力干员":
                continue
            if datetime.fromtimestamp(pool['openTime']) >= now:
                des_dict_temp.append({pool['openTime']: "卡池 【" + pool['gachaPoolName'] + "】 即将于\n" + datetime.fromtimestamp(pool['openTime']).strftime('%Y-%m-%d %H:%M') + " 开始。\n"})
            if datetime.fromtimestamp(pool['endTime']) >= now:
                des_dict_temp.append({pool['endTime']: "卡池 【" + pool['gachaPoolName'] + "】 即将于\n" + datetime.fromtimestamp(pool['endTime']).strftime('%Y-%m-%d %H:%M') + " 结束。\n"})

    if bot.get_config('sendTowerSeasonRemind'):
        for season in towerSeason.values():
            if datetime.fromtimestamp(season['startTs']) >= now:
                des_dict_temp.append({season['startTs']: "保全派驻派驻周期 【" + season['name'] + "】 即将于\n" + datetime.fromtimestamp(season['startTs']).strftime('%Y-%m-%d %H:%M') + " 开始。\n"})
            if datetime.fromtimestamp(season['endTs']) >= now:
                des_dict_temp.append({season['endTs']: "保全派驻派驻周期 【" + season['name'] + "】 即将于\n" + datetime.fromtimestamp(season['endTs']).strftime('%Y-%m-%d %H:%M') + " 结束。\n"})

    des_dict = sorted(des_dict_temp, key=lambda x: next(iter(x)))
    for des in des_dict:
        for item in des.values():
            description = description + item + '\n'

    return Chain(data).text(description)


@bot.timed_task(each=60)
async def _(_):
    now = datetime.now().replace(second = 0, microsecond = 0)
    if bot.get_config('sendRealtimeRemind'):
        notice = ""

        for item in actThemes:
            for i, node in enumerate(item['timeNodes']):
                nodeTime = datetime.fromtimestamp(node['ts'])
                #4点的通知改到10点
                if nodeTime.timetuple().tm_hour == 4:
                    nodeTime = nodeTime.replace(hour = 10)

                if nodeTime == now:
                    if i == 0:
                        notice = notice + node['title'] + "\n"
                    else:
                        notice = notice + item['timeNodes'][0]['title'].replace('已开放','') + node['title'] + "\n"
        
        if notice != "":
                target: List[GroupSetting] = GroupSetting.select().where(GroupSetting.activity_remind == 1)

                if not target:
                    return

                async_send_tasks = []

                for target_item in target:
                    data = Chain()

                    instance = main_bot[target_item.bot_id]
                    if not instance:
                        continue

                    data.text(notice)

                    if bot.get_config('sendAsync'):
                        async_send_tasks.append(instance.send_message(data, channel_id=target_item.group_id))
                    else:
                        await instance.send_message(data, channel_id=target_item.group_id)
                        await asyncio.sleep(bot.get_config('sendInterval'))

                if async_send_tasks:
                    await asyncio.wait(async_send_tasks)

    for item in bot.get_config('sendTime'):
        description = ""
        itemFoward = item.get('foward')
        itemTime = datetime.strptime(item.get('time'), '%H:%M:%S').time().replace(second = 0)
        itemRemindType = item.get('remindType')

        if itemTime == now.time():
            for active in activeList.values():
                if datetime.fromtimestamp(active['startTime']) >= now and (datetime.fromtimestamp(active['startTime']) - now).days == itemFoward:
                    description = description + "活动 【" + active['name'] + "】 即将于"
                    if itemFoward == 0:
                        if (datetime.fromtimestamp(active['startTime']) - now).seconds // 3600 > 0:
                            description = description + str((datetime.fromtimestamp(active['startTime']) - now).seconds // 3600) + "小时后开始。\n"
                        else:
                            description = description + str((datetime.fromtimestamp(active['startTime']) - now).seconds // 60) + "分钟后开始。\n"
                    else: 
                        description = description + str(itemFoward) + "天后开始。\n"
                if datetime.fromtimestamp(active['endTime']) >= now and (datetime.fromtimestamp(active['endTime']) - now).days == itemFoward:
                    description = description + "活动 【" + active['name'] + "】 即将于"
                    if itemFoward == 0:
                        if (datetime.fromtimestamp(active['endTime']) - now).seconds // 3600 > 0:
                            description = description + str((datetime.fromtimestamp(active['endTime']) - now).seconds // 3600) + "小时后结束。\n"
                        else:
                            description = description + str((datetime.fromtimestamp(active['endTime']) - now).seconds // 60) + "分钟后结束。\n"
                    else: 
                        description = description + str(itemFoward) + "天后结束。\n"
                if active['endTime'] != active['rewardEndTime'] and datetime.fromtimestamp(active['rewardEndTime']) >= now and (datetime.fromtimestamp(active['rewardEndTime']) - now).days == itemFoward:
                    description = description + "活动 【" + active['name'] + "】 的奖励兑换即将于"
                    if itemFoward == 0:
                        if (datetime.fromtimestamp(active['rewardEndTime']) - now).seconds // 3600 > 0:
                            description = description + str((datetime.fromtimestamp(active['rewardEndTime']) - now).seconds // 3600) + "小时后结束。\n"
                        else:
                            description = description + str((datetime.fromtimestamp(active['rewardEndTime']) - now).seconds // 60) + "分钟后结束。\n"
                    else: 
                        description = description + str(itemFoward) + "天后结束。\n"
            
            for crisis in crisisSeasons.values():
                if datetime.fromtimestamp(crisis['startTs']) >= now and (datetime.fromtimestamp(crisis['startTs']) - now).days == itemFoward:
                    description = description + "危机合约" + crisis['crisisV2SeasonCode'] + " 【" + crisis['name'] + "】 即将于"
                    if itemFoward == 0:
                        if (datetime.fromtimestamp(crisis['startTs']) - now).seconds // 3600 > 0:
                            description = description + str((datetime.fromtimestamp(crisis['startTs']) - now).seconds // 3600) + "小时后开始。\n"
                        else:
                            description = description + str((datetime.fromtimestamp(crisis['startTs']) - now).seconds // 60) + "分钟后开始。\n"
                    else: 
                        description = description + str(itemFoward) + "天后开始。\n"
                if datetime.fromtimestamp(crisis['endTs']) >= now and (datetime.fromtimestamp(crisis['endTs']) - now).days == itemFoward:
                    description = description + "危机合约" + crisis['crisisV2SeasonCode'] + " 【" + crisis['name'] + "】 即将于"
                    if itemFoward == 0:
                        if (datetime.fromtimestamp(crisis['endTs']) - now).seconds // 3600 > 0:
                            description = description + str((datetime.fromtimestamp(crisis['endTs']) - now).seconds // 3600) + "小时后结束，特设兑换所将同时关闭。\n"
                        else:
                            description = description + str((datetime.fromtimestamp(crisis['endTs']) - now).seconds // 60) + "分钟后结束，特设兑换所将同时关闭。\n"
                    else: 
                        description = description + str(itemFoward) + "天后结束，特设兑换所将同时关闭。\n"

            if bot.get_config('sendGachaPoolRemind'):
                for pool in gachaPool:
                    if pool['gachaRuleType'] in poolIgnore:
                        continue
                    if pool['gachaPoolName'] == "适合多种场合的强力干员":
                        continue

                    if datetime.fromtimestamp(pool['endTime']) >= now and (datetime.fromtimestamp(pool['endTime']) - now).days == itemFoward:
                        description = description + "卡池 【" + pool['gachaPoolName'] + "】 即将于"
                        if itemFoward == 0:
                            if (datetime.fromtimestamp(pool['endTime']) - now).seconds // 3600 > 0:
                                description = description + str((datetime.fromtimestamp(pool['endTime']) - now).seconds // 3600) + "小时后结束。\n"
                            else:
                                description = description + str((datetime.fromtimestamp(pool['endTime']) - now).seconds // 60) + "分钟后结束。\n"
                        else: 
                            description = description + str(itemFoward) + "天后结束。\n"

            if bot.get_config('sendTowerSeasonRemind'):
                for season in towerSeason.values():
                    if datetime.fromtimestamp(season['endTs']) >= now and (datetime.fromtimestamp(season['endTs']) - now).days == itemFoward:
                        description = description + "保全派驻派驻周期 【" + season['name'] + "】 即将于"
                        if itemFoward == 0:
                            if (datetime.fromtimestamp(season['endTs']) - now).seconds // 3600 > 0:
                                description = description + str((datetime.fromtimestamp(season['endTs']) - now).seconds // 3600) + "小时后结束。\n"
                            else:
                                description = description + str((datetime.fromtimestamp(season['endTs']) - now).seconds // 60) + "分钟后结束。\n"
                        else: 
                            description = description + str(itemFoward) + "天后结束。\n"

        if description != "":
            target: List[GroupSetting] = GroupSetting.select().where(GroupSetting.activity_remind == 1)

            if not target:
                return

            async_send_tasks = []

            await send_to_console_channel(
                Chain().text(f'开始发送提醒\n目标数: {len(target)}')
            )

            for target_item in target:
                data = Chain()

                instance = main_bot[target_item.bot_id]
                if not instance:
                    continue

                data.text(description)

                if itemRemindType == '@所有人':
                    data.at_all()

                if bot.get_config('sendAsync'):
                    for i in range(3 if itemRemindType == '连发三遍' else 1):
                        async_send_tasks.append(
                            instance.send_message(data, channel_id=target_item.group_id)
                        )
                else:
                    for i in range(3 if itemRemindType == '连发三遍' else 1):
                        await instance.send_message(data, channel_id=target_item.group_id)
                    await asyncio.sleep(bot.get_config('sendInterval'))

            if async_send_tasks:
                await asyncio.wait(async_send_tasks)

            await send_to_console_channel(Chain().text(f'提醒发送结束。'))
