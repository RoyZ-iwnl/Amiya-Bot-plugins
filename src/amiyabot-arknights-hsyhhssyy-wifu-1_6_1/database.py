from datetime import datetime

from peewee import AutoField,CharField,TextField,DateTimeField

from amiyabot.database import ModelClass

from core.database.plugin import db

class AmiyaBotWifuStatusDataBase(ModelClass):
    id: int = AutoField()
    channel_id: str = CharField()
    user_id: str = CharField()
    wifu_name: str = CharField()
    create_at: datetime = DateTimeField(null=True)

    class Meta:
        database = db
        table_name = "amiyabot-wifu-status"