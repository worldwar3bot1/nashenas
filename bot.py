"""ربات پیام‌رسان خصوصی با تأیید ادمین پیش از تحویل هر پیام."""
from __future__ import annotations

import logging
import re
from typing import Final

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
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


# ترتیب این فهرست فقط برای پیام‌هایی است که به‌ندرت چند فیلد محتوایی دارند.
# در حالت عادی هر پیام تلگرام دقیقاً با همان نوع واقعی خودش شناسایی می‌شود.
CONTENT_ATTRIBUTES: Final[tuple[tuple[str, str], ...]] = (
    ("paid_media", "رسانه پولی"),
    ("video", "ویدیو"),
    ("animation", "گیف / انیمیشن"),
    ("photo", "عکس"),
    ("voice", "پیام صوتی"),
    ("video_note", "ویدیوی گرد"),
    ("audio", "فایل صوتی"),
    ("document", "فایل"),
    ("sticker", "استیکر"),
    ("contact", "مخاطب"),
    ("location", "موقعیت مکانی"),
    ("venue", "مکان"),
    ("poll", "نظرسنجی"),
    ("dice", "تاس"),
    ("game", "بازی تلگرامی"),
    ("story", "استوری"),
    ("checklist", "چک‌لیست"),
    ("invoice", "فاکتور پرداخت"),
    ("successful_payment", "پرداخت موفق"),
    ("passport_data", "اطلاعات گذرنامه"),
    ("web_app_data", "داده وب‌اپ"),
    ("users_shared", "کاربران اشتراک‌گذاری‌شده"),
    ("chat_shared", "گفتگوی اشتراک‌گذاری‌شده"),
    ("giveaway", "قرعه‌کشی"),
    ("giveaway_winners", "نتیجه قرعه‌کشی"),
)


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def message_content_type(message) -> str:
    """نوع واقعی محتوای پیام را از فیلدهای خود پیام تلگرام تعیین می‌کند."""
    detected = [
        label
        for attribute, label in CONTENT_ATTRIBUTES
        if getattr(message, attribute, None) is not None
    ]

    if len(detected) == 1:
        return detected[0]
    if len(detected) > 1:
        # به‌جای انتخاب اشتباه یک نوع، همه انواع موجود را دقیق گزارش می‌کنیم.
        return "محتوای ترکیبی: " + "، ".join(detected)
    if getattr(message, "text", None) is not None:
        return "متن"
    if getattr(message, "caption", None) is not None:
        return "متن همراه با رسانه"
    return "محتوای تلگرامی"


def review_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject:{request_id}"),
        ]]
    )


def build_admin_card_text(
    request_id: int,
    sender_name: str,
    receiver_name: str,
    content_type: str,
    result_line: str | None = None,
) -> str:
    """متن کارت را برای ساخت اولیه و ویرایش نهایی یکسان تولید می‌کند."""
    heading = result_line or f"📩 پیام شماره {request_id} برای بررسی"
    return (
        f"{heading}\n\n"
        f"👤 فرستنده: {sender_name}\n"
        f"👤 گیرنده: {receiver_name}\n"
        f"📎 نوع محتوا: {content_type}"
    )


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
        await update.effective_message.reply_text(
            "لطفاً فقط در گفتگوی خصوصی ربات از آن استفاده کنید."
        )
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

    # ادمین فقط کارت‌های بررسی را مدیریت می‌کند و فرستنده گفتگو نیست.
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

    content_type = message_content_type(message)
    request_id = db.create_request(
        sender_id=user.id,
        receiver_id=receiver_id,
        source_chat_id=chat.id,
        source_message_id=message.message_id,
        content_type=content_type,
    )

    sender_name = settings.name_for(user.id)
    receiver_name = settings.name_for(receiver_id)
    card_text = build_admin_card_text(
        request_id, sender_name, receiver_name, content_type
    )

    admin_card = None
    try:
        admin_card = await context.bot.send_message(
            chat_id=settings.admin_id,
            text=card_text,
            reply_markup=review_keyboard(request_id),
        )
        db.set_admin_card(request_id, admin_card.message_id)

        # محتوای اصلی به‌صورت Reply به کارت متصل می‌شود تا دقیقاً زیر همان کارت باشد.
        # copy_message نوع، متن/کپشن، فایل و سایر اجزای پیام اصلی را حفظ می‌کند.
        await context.bot.copy_message(
            chat_id=settings.admin_id,
            from_chat_id=chat.id,
            message_id=message.message_id,
            reply_parameters=ReplyParameters(message_id=admin_card.message_id),
        )
        db.set_content_ready(request_id, True)

    except TelegramError:
        logger.exception("ثبت محتوای درخواست %s برای بررسی ادمین ناموفق بود", request_id)
        db.set_content_ready(request_id, False)
        db.mark_failed(request_id)

        if admin_card is not None:
            try:
                await context.bot.edit_message_text(
                    chat_id=settings.admin_id,
                    message_id=admin_card.message_id,
                    text=(
                        card_text
                        + "\n\n⚠️ دریافت محتوای اصلی ناموفق بود؛ این درخواست قابل تأیید نیست."
                    ),
                    reply_markup=None,
                )
            except TelegramError:
                logger.exception("ویرایش کارت ناموفق درخواست %s ناموفق بود", request_id)
        # برای شبیه‌ماندن تجربه به چت، پس از ارسال کاربر پیام تأیید/خطای اضافی نمی‌فرستیم.
        return

    # عمداً هیچ پیام تأیید یا «در انتظار بررسی» برای فرستنده فرستاده نمی‌شود.


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

    claimed = db.claim_request(
        request_id, "APPROVE" if action == "approve" else "REJECT"
    )
    if not claimed:
        await query.answer("این پیام قبلاً بررسی شده یا در حال بررسی است.", show_alert=True)
        return

    # فقط پاسخ کوچک Callback نمایش داده می‌شود؛ هیچ پیام تازه‌ای در چت ساخته نمی‌شود.
    await query.answer("در حال ثبت تصمیم...")

    sender_id = int(request["sender_id"])
    receiver_id = int(request["receiver_id"])
    sender_name = settings.name_for(sender_id)
    receiver_name = settings.name_for(receiver_id)
    content_type = str(request["content_type"])

    if action == "approve":
        try:
            # فقط محتوای اصلی فرستنده به گیرنده کپی می‌شود؛ اعلان جداگانه‌ای وجود ندارد.
            await context.bot.copy_message(
                chat_id=receiver_id,
                from_chat_id=int(request["source_chat_id"]),
                message_id=int(request["source_message_id"]),
            )
        except TelegramError:
            logger.exception("تحویل درخواست %s به گیرنده ناموفق بود", request_id)
            db.mark_failed(request_id)
            result_line = (
                f"⚠️ پیام شماره {request_id} بررسی و تأیید شد، "
                f"اما تحویل آن به {receiver_name} ناموفق بود."
            )
        else:
            db.finish_request(request_id, "APPROVED")
            result_line = (
                f"✅ پیام شماره {request_id} تأیید و برای {receiver_name} ارسال شد."
            )
    else:
        # رد پیام فقط در همان کارت ادمین ثبت می‌شود؛ به هیچ‌یک از طرفین پیام اضافه نمی‌رود.
        db.finish_request(request_id, "REJECTED")
        result_line = f"❌ پیام شماره {request_id} رد شد."

    final_card_text = build_admin_card_text(
        request_id,
        sender_name,
        receiver_name,
        content_type,
        result_line=result_line,
    )
    try:
        # همان پیام بررسی ویرایش می‌شود؛ پیام جدیدی برای ادمین ارسال نمی‌شود.
        await query.edit_message_text(text=final_card_text, reply_markup=None)
    except TelegramError:
        logger.exception("ویرایش کارت نهایی درخواست %s ناموفق بود", request_id)


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
