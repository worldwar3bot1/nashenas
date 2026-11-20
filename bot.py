"""ربات پیام‌رسان خصوصی با تأیید ادمین پیش از تحویل هر پیام."""
from __future__ import annotations

import logging
import re
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Settings, load_settings
from database import Database

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("private_chat_bot")

FIRST_START_TEXT: Final[str] = "💬 چت از همین الان آغاز شد.\nپیام خود را بفرستید."
READY_TEXT: Final[str] = "پیام خود را بفرستید."
ACCESS_DENIED_TEXT: Final[str] = "⛔ شما اجازه استفاده از این ربات را ندارید."
SENDER_REJECTED_TEXT: Final[str] = (
    "پیام شما به دلیل سیاست‌های امنیتی برای گیرنده ارسال نگردید."
)


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def message_content_type(message) -> str:
    """نوع پیام را برای نمایش در کارت بررسی ادمین تشخیص می‌دهد."""
    checks = [
        ("photo", "عکس"),
        ("video", "ویدیو"),
        ("animation", "گیف"),
        ("sticker", "استیکر"),
        ("voice", "ویس"),
        ("video_note", "ویدیوی گرد"),
        ("audio", "فایل صوتی"),
        ("document", "فایل"),
        ("contact", "مخاطب"),
        ("location", "موقعیت مکانی"),
        ("venue", "مکان"),
        ("poll", "نظرسنجی"),
        ("dice", "تاس"),
    ]
    for attribute, label in checks:
        if getattr(message, attribute, None) is not None:
            return label
    if message.text is not None:
        return "متن"
    if message.caption is not None:
        return "رسانه همراه با توضیح"
    return "محتوای تلگرامی"


def review_keyboard(request_id: int, *, allow_approve: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if allow_approve:
        buttons.append(InlineKeyboardButton("✅ تأیید", callback_data=f"approve:{request_id}"))
    buttons.append(InlineKeyboardButton("❌ رد", callback_data=f"reject:{request_id}"))
    return InlineKeyboardMarkup([buttons])


async def start_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None or update.effective_message is None:
        return

    settings = get_settings(context)
    db = get_db(context)

    if user.id not in settings.allowed_user_ids:
        await update.effective_message.reply_text(ACCESS_DENIED_TEXT)
        return

    if chat.type != ChatType.PRIVATE:
        await update.effective_message.reply_text("لطفاً فقط در گفتگوی خصوصی ربات از آن استفاده کنید.")
        return

    if user.id == settings.admin_id:
        await update.effective_message.reply_text(
            "✅ دسترسی ادمین فعال است. پیام‌های جدید برای بررسی به همین حساب ارسال می‌شوند."
        )
        return

    is_first_start = db.mark_first_start(user.id)
    await update.effective_message.reply_text(
        FIRST_START_TEXT if is_first_start else READY_TEXT
    )


async def handle_incoming_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or user is None or chat is None:
        return

    settings = get_settings(context)
    db = get_db(context)

    if user.id not in settings.allowed_user_ids:
        await message.reply_text(ACCESS_DENIED_TEXT)
        return

    # ادمین فقط پیام‌های بررسی را مدیریت می‌کند و فرستنده گفتگو نیست.
    if user.id == settings.admin_id:
        return

    if chat.type != ChatType.PRIVATE:
        await message.reply_text("لطفاً پیام را در گفتگوی خصوصی ربات ارسال کنید.")
        return

    try:
        receiver_id = settings.other_user_id(user.id)
    except ValueError:
        await message.reply_text(ACCESS_DENIED_TEXT)
        return

    request_id = db.create_request(
        sender_id=user.id,
        receiver_id=receiver_id,
        source_chat_id=chat.id,
        source_message_id=message.message_id,
        content_type=message_content_type(message),
    )

    sender_name = settings.name_for(user.id)
    receiver_name = settings.name_for(receiver_id)
    card_text = (
        "📩 پیام جدید برای بررسی\n\n"
        f"👤 فرستنده: {sender_name}\n"
        f"🆔 شناسه فرستنده: {user.id}\n"
        f"👤 گیرنده: {receiver_name}\n"
        f"🆔 شناسه گیرنده: {receiver_id}\n"
        f"📎 نوع محتوا: {message_content_type(message)}\n\n"
        "محتوای اصلی در پیام جداگانه زیر این پیام ارسال می‌شود."
    )

    admin_card = None
    try:
        admin_card = await context.bot.send_message(
            chat_id=settings.admin_id,
            text=card_text,
            reply_markup=review_keyboard(request_id),
        )
        db.set_admin_card(request_id, admin_card.message_id)

        # محتوای اصلی به‌صورت یک پیام جداگانه زیر کارت ادمین کپی می‌شود.
        await context.bot.copy_message(
            chat_id=settings.admin_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
        )
        db.set_content_ready(request_id, True)
    except TelegramError:
        logger.exception("ارسال درخواست %s برای بررسی ادمین ناموفق بود", request_id)
        db.set_content_ready(request_id, False)
        try:
            if admin_card is not None:
                await context.bot.edit_message_text(
                    chat_id=settings.admin_id,
                    message_id=admin_card.message_id,
                    text=(
                        card_text
                        + "\n\n⚠️ دریافت محتوای اصلی ناموفق بود؛ این درخواست را تأیید نکنید."
                    ),
                    reply_markup=review_keyboard(request_id, allow_approve=False),
                )
            else:
                db.mark_failed(request_id)
        except TelegramError:
            logger.exception("به‌روزرسانی کارت خطادار ادمین ناموفق بود")
        try:
            if admin_card is not None:
                await message.reply_text(
                    "⚠️ پیام شما ثبت شد، اما محتوای آن برای ادمین قابل نمایش نبود و قابل تأیید نیست."
                )
            else:
                await message.reply_text(
                    "⚠️ ارسال پیام برای بررسی با خطا روبه‌رو شد. لطفاً دوباره تلاش کنید."
                )
        except TelegramError:
            logger.exception("اطلاع‌رسانی خطای بررسی به فرستنده ناموفق بود")
        return

    try:
        await message.reply_text("📨 پیام شما برای بررسی به ادمین ارسال شد.")
    except TelegramError:
        logger.exception("ارسال تأیید دریافت پیام به فرستنده ناموفق بود")


async def moderate_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return

    settings = get_settings(context)
    db = get_db(context)

    if user.id != settings.admin_id:
        await query.answer("شما اجازه بررسی پیام‌ها را ندارید.", show_alert=True)
        return

    match = re.fullmatch(r"(approve|reject):(\d+)", query.data or "")
    if not match:
        await query.answer("درخواست نامعتبر است.", show_alert=True)
        return

    action, raw_request_id = match.groups()
    request_id = int(raw_request_id)
    request = db.get_request(request_id)
    if request is None:
        await query.answer("این درخواست پیدا نشد.", show_alert=True)
        return

    if request["status"] != "PENDING":
        await query.answer("این پیام قبلاً بررسی شده یا در حال بررسی است.", show_alert=True)
        return

    if action == "approve" and not request["content_ready"]:
        await query.answer("محتوای اصلی در دسترس نیست؛ این پیام را تأیید نکنید.", show_alert=True)
        return

    claimed = db.claim_request(request_id, "APPROVE" if action == "approve" else "REJECT")
    if not claimed:
        await query.answer("این پیام قبلاً بررسی شده یا در حال بررسی است.", show_alert=True)
        return

    await query.answer("در حال ثبت تصمیم...")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        logger.exception("حذف دکمه‌های درخواست %s ناموفق بود", request_id)

    sender_id = int(request["sender_id"])
    receiver_id = int(request["receiver_id"])
    sender_name = settings.name_for(sender_id)
    receiver_name = settings.name_for(receiver_id)

    if action == "approve":
        notice_message = None
        try:
            notice_message = await context.bot.send_message(
                chat_id=receiver_id,
                text=f"📩 پیام جدید از {sender_name}:",
            )
            await context.bot.copy_message(
                chat_id=receiver_id,
                from_chat_id=int(request["source_chat_id"]),
                message_id=int(request["source_message_id"]),
            )
            await context.bot.copy_message(
                chat_id=receiver_id,
                from_chat_id=int(request["source_chat_id"]),
                message_id=int(request["source_message_id"]),
            )
        except TelegramError:
            logger.exception("تحویل درخواست %s به گیرنده ناموفق بود", request_id)
            db.mark_failed(request_id)
            if notice_message is not None:
                try:
                    await context.bot.edit_message_text(
                        chat_id=receiver_id,
                        message_id=notice_message.message_id,
                        text=f"⚠️ ارسال پیام از {sender_name} به دلیل خطای فنی انجام نشد.",
                    )
                except TelegramError:
                    logger.exception("اصلاح اعلان تحویل ناموفق بود")
            try:
                await context.bot.send_message(
                    chat_id=sender_id,
                    text="⚠️ پیام شما به دلیل خطای فنی به گیرنده تحویل داده نشد.",
                )
            except TelegramError:
                logger.exception("اطلاع‌رسانی خطای تحویل به فرستنده ناموفق بود")
            await notify_admin_result(
                context,
                f"⚠️ درخواست {request_id} تأیید شد، اما تحویل آن با خطا روبه‌رو شد. وضعیت: ناموفق.",
            )
        else:
            db.finish_request(request_id, "APPROVED")
            try:
                await context.bot.send_message(
                    chat_id=sender_id,
                    text=f"✅ پیام شما با موفقیت برای {receiver_name} ارسال شد.",
                )
            except TelegramError:
                logger.exception("اطلاع‌رسانی تحویل موفق به فرستنده ناموفق بود")
            await notify_admin_result(
                context,
                f"✅ پیام شماره {request_id} تأیید و برای {receiver_name} ارسال شد.",
            )
    else:
        # در رد پیام، محتوای اصلی برای گیرنده ارسال نمی‌شود.
        receiver_notification_sent = False
        try:
            await context.bot.send_message(
                chat_id=receiver_id,
                text=(
                    f"⚠️ یک پیام از {sender_name} برای شما ارسال شد، "
                    "اما به دلیل مسائل امنیتی برای شما ارسال نشد."
                ),
            )
            receiver_notification_sent = True
        except TelegramError:
            logger.exception("ارسال اعلان رد به گیرنده ناموفق بود")

        try:
            await context.bot.send_message(
                chat_id=sender_id,
                text=SENDER_REJECTED_TEXT,
            )
        except TelegramError:
            logger.exception("ارسال پیام رد به فرستنده ناموفق بود")

        db.finish_request(request_id, "REJECTED")
        result_text = f"❌ پیام شماره {request_id} رد شد."
        if not receiver_notification_sent:
            result_text += "\n⚠️ اعلان عدم تحویل به گیرنده نرسید؛ احتمالاً ربات را شروع نکرده یا مسدود کرده است."
        await notify_admin_result(context, result_text)


async def notify_admin_result(
    context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    settings = get_settings(context)
    try:
        await context.bot.send_message(chat_id=settings.admin_id, text=text)
    except TelegramError:
        logger.exception("ارسال نتیجه به ادمین ناموفق بود")


async def post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    me = await application.bot.get_me()
    logger.info(
        "ربات @%s آماده است | X=%s | Y=%s | Admin=%s",
        me.username,
        settings.user_x_id,
        settings.user_y_id,
        settings.admin_id,
    )


def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["db"] = db

    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(CallbackQueryHandler(moderate_callback))
    application.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_message)
    )

    logger.info("ربات در حال راه‌اندازی است...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
