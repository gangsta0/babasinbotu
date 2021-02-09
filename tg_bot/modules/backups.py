import json, time, os
from io import BytesIO
from typing import Optional

from telegram import MAX_MESSAGE_LENGTH, ParseMode, InlineKeyboardMarkup
from telegram import Message, Chat, Update, Bot
from telegram.error import BadRequest
from telegram.ext import CommandHandler, run_async, Filters

import tg_bot.modules.sql.notes_sql as sql
from tg_bot import dispatcher, LOGGER, OWNER_ID, SUDO_USERS, MESSAGE_DUMP
from tg_bot.__main__ import DATA_IMPORT
from tg_bot.modules.helper_funcs.chat_status import user_admin
from tg_bot.modules.helper_funcs.misc import build_keyboard, revert_buttons
from tg_bot.modules.helper_funcs.msg_types import get_note_type
from tg_bot.modules.rules import get_rules
from tg_bot.modules.sql import notes_sql as notesql
import tg_bot.modules.sql.rules_sql as rulessql
from tg_bot.modules.sql import warns_sql as warnssql
import tg_bot.modules.sql.blacklist_sql as blacklistsql
from tg_bot.modules.sql import disable_sql as disabledsql
from tg_bot.modules.sql import cust_filters_sql as filtersql
import tg_bot.modules.sql.welcome_sql as welcsql
import tg_bot.modules.sql.locks_sql as locksql
from tg_bot.modules.connection import connected

@run_async
@user_admin
def import_data(bot: Bot, update):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    # TODO: allow uploading doc with command, not just as reply
    # only work with a doc

    conn = connected(bot, update, chat, user.id, need_admin=True)
    if conn:
        chat = dispatcher.bot.getChat(conn)
        chat_name = dispatcher.bot.getChat(conn).title
    else:
        if update.effective_message.chat.type == "private":
            update.effective_message.reply_text("Bu əmr qrup üçündür!")
            return ""

        chat = update.effective_chat
        chat_name = update.effective_message.chat.title

    if msg.reply_to_message and msg.reply_to_message.document:
        try:
            file_info = bot.get_file(
                msg.reply_to_message.document.file_id)
        except BadRequest:
            msg.reply_text(
                "Faylı yenidən yüklə! Bu mənə xarab kimi gəldi!"
            )
            return

        with BytesIO() as file:
            file_info.download(out=file)
            file.seek(0)
            data = json.load(file)

        # only import one group
        if len(data) > 1 and str(chat.id) not in data:
            msg.reply_text(
                "There are more than one group in this file and the chat.id is not same! How am i supposed to import it?"
            )
            return

        # Check if backup is this chat
        try:
            if data.get(str(chat.id)) is None:
                if conn:
                    text = "Yedək başqa bir söhbətdən gəlir, başqa bir sohbeti sohbet üçün geri qaytara bilmirəm *{}*".format(
                        chat_name)
                else:
                    text = "Yedək başqa bir söhbətdən gəlir, başqa bir sohbəti bu söhbətə qaytara bilmirəm"
                return msg.reply_text(text, parse_mode="markdown")
        except Exception:
            return msg.reply_text(
                "məlumatları idxal edərkən bir problem oldu!")
        # Check if backup is from self
        try:
            if str(bot.id) != str(data[str(chat.id)]["bot"]):
                return msg.reply_text(
                    "Təklif olunmayan başqa bir botdan yedəkləmə problemə səbəb ola bilər, sənədlər, şəkillər, videolar, audiolar, qeydlər lazım olduğu kimi işləməyəcəkdir."
                )
        except Exception:
            pass
        # Select data source
        if str(chat.id) in data:
            data = data[str(chat.id)]["hashes"]
        else:
            data = data[list(data.keys())[0]]["hashes"]

        try:
            for mod in DATA_IMPORT:
                mod.__import_data__(str(chat.id), data)
        except Exception:
            msg.reply_text(
                "Məlumatlarınızı bərpa edərkən bir səhv baş verdi. Proses alınmadı."
            )

            LOGGER.exception(
                "Imprt for the chat %s with the name %s failed.",
                str(chat.id),
                str(chat.title),
            )
            return

        # TODO: some of that link logic
        # NOTE: consider default permissions stuff?
        if conn:

            text = "Yedəkləmə *{}* tarixində tamamilə bərpa edildi.".format(chat_name)
        else:
            text = "Yedəkləmə tamamilə bərpə edildi"
        msg.reply_text(text, parse_mode="markdown")

@run_async
@user_admin
def export_data(bot: Bot, update: Update, chat_data):
    chat_data = chat_data
    msg = update.effective_message  # type: Optional[Message]
    user = update.effective_user  # type: Optional[User]
    chat_id = update.effective_chat.id
    chat = update.effective_chat
    current_chat_id = update.effective_chat.id
    conn = connected(bot, update, chat, user.id, need_admin=True)
    if conn:
        chat = dispatcher.bot.getChat(conn)
        chat_id = conn
        # chat_name = dispatcher.bot.getChat(conn).title
    else:
        if update.effective_message.chat.type == "private":
            update.effective_message.reply_text(
                "Bu əmr PM'də işləməz! Qrupda yaz!"
            )
            return ""
        chat = update.effective_chat
        chat_id = update.effective_chat.id
        # chat_name = update.effective_message.chat.title

    jam = time.time()
    new_jam = jam + 10800
    checkchat = get_chat(chat_id, chat_data)
    if checkchat.get("status"):
        if jam <= int(checkchat.get("value")):
            timeformatt = time.strftime(
                "%H:%M:%S %d/%m/%Y", time.localtime(checkchat.get("value"))
            )
            update.effective_message.reply_text(
                "Gündə 1 dəfə yedəkləmə edə bilərsiz.\nTəxmini `{}` zaman sonra təkrar cəhd edin".format(
                    timeformatt
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        else:
            if user.id != OWNER_ID:
                put_chat(chat_id, new_jam, chat_data)
    else:
        if user.id != OWNER_ID:
            put_chat(chat_id, new_jam, chat_data)

    note_list = sql.get_all_chat_notes(chat_id)
    backup = {}
    # button = ""
    buttonlist = []
    namacat = ""
    isicat = ""
    rules = ""
    count = 0
    countbtn = 0
    # Notes
    for note in note_list:
        count += 1
        # getnote = sql.get_note(chat_id, note.name)
        namacat += "{}<###splitter###>".format(note.name)
        if note.msgtype == 1:
            tombol = sql.get_buttons(chat_id, note.name)
            # keyb = []
            for btn in tombol:
                countbtn += 1
                if btn.same_line:
                    buttonlist.append(
                        ("{}".format(btn.name), "{}".format(btn.url), True)
                    )
                else:
                    buttonlist.append(
                        ("{}".format(btn.name), "{}".format(btn.url), False)
                    )
            isicat += "###button###: {}<###button###>{}<###splitter###>".format(
                note.value, str(buttonlist)
            )
            buttonlist.clear()
        elif note.msgtype == 2:
            isicat += "###sticker###:{}<###splitter###>".format(note.file)
        elif note.msgtype == 3:
            isicat += "###file###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        elif note.msgtype == 4:
            isicat += "###photo###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        elif note.msgtype == 5:
            isicat += "###audio###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        elif note.msgtype == 6:
            isicat += "###voice###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        elif note.msgtype == 7:
            isicat += "###video###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        elif note.msgtype == 8:
            isicat += "###video_note###:{}<###TYPESPLIT###>{}<###splitter###>".format(
                note.file, note.value
            )
        else:
            isicat += "{}<###splitter###>".format(note.value)
    notes = {"#{}".format(namacat.split("<###splitter###>")[x]): "{}".format(
            isicat.split("<###splitter###>")[x]
        ) for x in range(count)}
    # Rules
    rules = rulessql.get_rules(chat_id)
    # Blacklist
    bl = list(blacklistsql.get_chat_blacklist(chat_id))
    # Disabled command
    disabledcmd = list(disabledsql.get_all_disabled(chat_id))
    # Filters (TODO)
    """
	all_filters = list(filtersql.get_chat_triggers(chat_id))
	export_filters = {}
	for filters in all_filters:
		filt = filtersql.get_filter(chat_id, filters)
		# print(vars(filt))
		if filt.is_sticker:
			tipefilt = "sticker"
		elif filt.is_document:
			tipefilt = "doc"
		elif filt.is_image:
			tipefilt = "img"
		elif filt.is_audio:
			tipefilt = "audio"
		elif filt.is_voice:
			tipefilt = "voice"
		elif filt.is_video:
			tipefilt = "video"
		elif filt.has_buttons:
			tipefilt = "button"
			buttons = filtersql.get_buttons(chat.id, filt.keyword)
			print(vars(buttons))
		elif filt.has_markdown:
			tipefilt = "text"
		if tipefilt == "button":
			content = "{}#=#{}|btn|{}".format(tipefilt, filt.reply, buttons)
		else:
			content = "{}#=#{}".format(tipefilt, filt.reply)
		print(content)
		export_filters[filters] = content
	print(export_filters)
	"""
    # Welcome (TODO)
    # welc = welcsql.get_welc_pref(chat_id)
    # Locked
    curr_locks = locksql.get_locks(chat_id)
    curr_restr = locksql.get_restr(chat_id)

    if curr_locks:
        locked_lock = {
            "sticker": curr_locks.sticker,
            "audio": curr_locks.audio,
            "voice": curr_locks.voice,
            "document": curr_locks.document,
            "video": curr_locks.video,
            "contact": curr_locks.contact,
            "photo": curr_locks.photo,
            "gif": curr_locks.gif,
            "url": curr_locks.url,
            "bots": curr_locks.bots,
            "forward": curr_locks.forward,
            "game": curr_locks.game,
            "location": curr_locks.location,
        }
    else:
        locked_lock = {}

    if curr_restr:
        locked_restr = {
            "messages": curr_restr.messages,
            "media": curr_restr.media,
            "other": curr_restr.other,
            "previews": curr_restr.preview,
            "all": all(
                [
                    curr_restr.messages,
                    curr_restr.media,
                    curr_restr.other,
                    curr_restr.preview,
                ]
            ),
        }
    else:
        locked_restr = {}

    locks = {"locks": locked_lock, "restrict": locked_restr}
    # Warns (TODO)
    # warns = warnssql.get_warns(chat_id)
    # Backing up
    backup[chat_id] = {
        "bot": bot.id,
        "hashes": {
            "info": {"rules": rules},
            "extra": notes,
            "blacklist": bl,
            "disabled": disabledcmd,
            "locks": locks,
        },
    }
    baccinfo = json.dumps(backup, indent=4)
    with open("CTRL{}.backup".format(chat_id), "w") as f:
        f.write(str(baccinfo))
    bot.sendChatAction(current_chat_id, "upload_document")
    tgl = time.strftime("%H:%M:%S - %d/%m/%Y", time.localtime(time.time()))
    try:
        bot.sendMessage(
            MESSAGE_DUMP,
            "*Yədəkləmə uğurla içə əlavə edildi:*\nQrup: `{}`\nQrup ID: `{}`\nON: `{}`".format(
                chat.title, chat_id, tgl
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest:
        pass
    bot.sendDocument(
        current_chat_id,
        document=open("CTRL{}.backup".format(chat_id), "rb"),
        caption="*Yedəkləmə uğurla içə əlavə edildi:*\nQrup: `{}`\nQrup ID: `{}`\nON: `{}`".format(
            chat.title, chat_id, tgl
        ),
        timeout=360,
        reply_to_message_id=msg.message_id,
        parse_mode=ParseMode.MARKDOWN,
    )
    os.remove("@C9ALA{}.backup".format(chat_id))  # Cleaning file


# Temporary data
def put_chat(chat_id, value, chat_data):
    	# print(chat_data)
    status = value != False
    chat_data[chat_id] = {'backups': {"status": status, "value": value}}

def get_chat(chat_id, chat_data):
    	# print(chat_data)
    try:
        return chat_data[chat_id]['backups']
    except KeyError:
    	return {"status": False, "value": False}


__mod_name__ = "Backups"

__help__ = """
*Qrup adminləri üçün:*
 - /import: köçürmələri çox asanlaşdıraraq / emilia qrupunun mümkün qədər idxal etməsi üçün ehtiyat sənədinə cavab verin
 - /export: İxrac ediləcək ixracat qrupu məlumatları bunlardır: qaydalar, qeydlər (sənədlər, şəkillər, musiqi, video, səs, səs, mətn, mətn düymələri) \
Bu modul hələ beta versiyasındadır!
"""

IMPORT_HANDLER = CommandHandler("import", import_data)
EXPORT_HANDLER = CommandHandler("export", export_data, pass_chat_data=True)

dispatcher.add_handler(IMPORT_HANDLER)
dispatcher.add_handler(EXPORT_HANDLER)
