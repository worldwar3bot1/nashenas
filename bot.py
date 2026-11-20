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


# فیلدهای محتوایی Message در python-telegram-bot 22.8.
# بعضی فیلدها در نسخه‌های جدید به‌صورت alias نیز پر می‌شوند:
# live_photo همراه photo، animation همراه document و venue همراه location.
# فیلد photo در PTB ممکن است برای پیام غیرعکسی یک tuple خالی باشد؛ بنابراین
# صرفاً None نبودن فیلد برای تشخیص نوع محتوا کافی نیست.
CONTENT_ATTRIBUTES: Final[tuple[tuple[str, str], ...]] = (
    ("live_photo", "عکس زنده"),
    ("paid_media", "رسانه پولی"),
    ("animation", "گیف / انیمیشن"),
    ("video", "ویدیو"),
    ("photo", "عکس"),
    ("voice", "پیام صوتی"),
    ("video_note", "ویدیوی گرد"),
    ("audio", "فایل صوتی"),
    ("document", "فایل"),
    ("sticker", "استیکر"),
    ("contact", "مخاطب"),
    ("venue", "مکان"),
    ("location", "موقعیت مکانی"),
    ("poll", "نظرسنجی"),
    ("dice", "تاس"),
    ("game", "بازی تلگرامی"),
    ("story", "استوری"),
    ("checklist", "چک‌لیست"),
    ("invoice", "فاکتور پرداخت"),
    ("successful_payment", "پرداخت موفق"),
    ("passport_data", "اطلاعات گذرنامه"),
    ("web_app_data", "داده وب‌اپ"),
    ("users_shared", "اشتراک‌گذاری کاربران"),
    ("chat_shared", "اشتراک‌گذاری گفتگو"),
    ("giveaway_winners", "نتیجه قرعه‌کشی"),
    ("giveaway", "قرعه‌کشی"),
    ("gift_upgrade_sent", "ارتقای هدیه"),
    ("unique_gift", "هدیه ویژه"),
    ("gift", "هدیه"),
    ("refunded_payment", "بازپرداخت"),
    ("checklist_tasks_done", "تغییر وضعیت چک‌لیست"),
    ("checklist_tasks_added", "افزودن موارد به چک‌لیست"),
)

ATTACHMENT_CLASS_LABELS: Final[dict[str, str]] = {
    "animation": "گیف / انیمیشن",
    "audio": "فایل صوتی",
    "contact": "مخاطب",
    "dice": "تاس",
    "document": "فایل",
    "game": "بازی تلگرامی",
    "invoice": "فاکتور پرداخت",
    "livephoto": "عکس زنده",
    "location": "موقعیت مکانی",
    "passportdata": "اطلاعات گذرنامه",
    "paidmediainfo": "رسانه پولی",
    "poll": "نظرسنجی",
    "sticker": "استیکر",
    "story": "استوری",
    "successfulpayment": "پرداخت موفق",
    "venue": "مکان",
    "video": "ویدیو",
    "videonote": "ویدیوی گرد",
    "voice": "پیام صوتی",
}


def get_settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def get_db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _has_content(value) -> bool:
    """مقادیر تهی مثل photo=() را محتوای واقعی حساب نمی‌کند."""
    if value is None:
        return False
    if isinstance(value, (str, bytes, tuple, list, set, frozenset, dict)):
        return bool(value)
    return True


def message_content_type(message) -> str:
    """نوع واقعی پیام را بدون اشتباه گرفتن فیلدهای خالی یا aliasها تشخیص می‌دهد."""
    for attribute, label in CONTENT_ATTRIBUTES:
        if _has_content(getattr(message, attribute, None)):
            # در Bot API بعضی فیلدها همراه با فیلد اصلی پر می‌شوند؛ ترتیب بالا
            # نوع اصلی را جلوتر از aliasهایی مثل photo/document/location قرار می‌دهد.
            return label

    # پشتیبان برای انواع جدیدی که در آینده به effective_attachment افزوده شوند.
    attachment = getattr(message, "effective_attachment", None)
    if _has_content(attachment):
        if isinstance(attachment, (tuple, list)):
            if attachment:
                class_name = type(attachment[-1]).__name__.replace("_", "").lower()
                if class_name == "photosize":
                    return "عکس"
        else:
            class_name = type(attachment).__name__.replace("_", "").lower()
            if class_name in ATTACHMENT_CLASS_LABELS:
                return ATTACHMENT_CLASS_LABELS[class_name]
            return f"پیوست تلگرامی ({type(attachment).__name__})"

    if getattr(message, "text", None) is not None:
        return "متن"
    if getattr(message, "caption", None) is not None:
        return "متن توضیحی"
    return "پیام تلگرامی دیگر"


def rejection_notice_texts(sender_name: str, receiver_name: str) -> tuple[str, str]:
    """متن اعلان رد برای گیرنده و پاسخ ریپلای‌شده برای فرستنده."""
    receiver_notice = (
        f"پیامی از طرف {sender_name} برای شما ارسال شده بود، "
        "اما به دلیل سیاست‌های امنیتی حذف گردید و به شما تحویل داده نشد."
    )
    sender_reply = (
        f"این پیام به دلیل سیاست‌های امنیتی برای شخص {receiver_name} ارسال نشد."
    )
    return receiver_notice, sender_reply


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
    reply_context: str | None = None,
) -> str:
    """متن کارت را برای ساخت اولیه و ویرایش نهایی یکسان تولید می‌کند."""
    heading = result_line or f"📩 پیام شماره {request_id} برای بررسی"
    text = (
        f"{heading}\n\n"
        f"👤 فرستنده: {sender_name}\n"
        f"👤 گیرنده: {receiver_name}\n"
        f"📎 نوع محتوا: {content_type}"
    )
    if reply_context:
        text += f"\n\n{reply_context}"
    return text


def _message_preview(message, limit: int = 220) -> str:
    """پیش‌نمایش امن و کوتاهی از پیامِ موردِ ریپلای می‌سازد."""
    value = getattr(message, "text", None) or getattr(message, "caption", None)
    if value:
        value = " ".join(str(value).split())
        return value if len(value) <= limit else value[: limit - 1] + "…"
    return f"[{message_content_type(message)}]"


def build_reply_context(message, db: Database, settings: Settings):
    """اطلاعات نمایشی و مقصد واقعی ریپلای را از پیام مقصد استخراج می‌کند."""
    replied = getattr(message, "reply_to_message", None)
    if replied is None:
        return None, None

    target = db.get_delivery_reply_target(message.chat_id, replied.message_id)
    if target is not None:
        original_sender = settings.name_for(int(target["sender_id"]))
        context = (
            f"↩️ ریپلای روی پیامِ {original_sender}:\n"
            f"{_message_preview(replied)}"
        )
        return target, context

    # اگر پیام مورد اشاره خارج از نگاشت پیام‌های تحویل‌شده باشد، جزئیاتش را
    # برای ادمین نمایش می‌دهیم ولی آن را به پیام نامرتبطی در چت مقصد وصل نمی‌کنیم.
    context = (
        "↩️ این پیام، پاسخ به پیام قبلیِ خارج از نگاشت تحویل ربات است:\n"
        f"{_message_preview(replied)}"
    )
    return None, context


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
    reply_target, reply_context = build_reply_context(message, db, settings)
    is_forward = getattr(message, "forward_origin", None) is not None
    request_id = db.create_request(
        sender_id=user.id,
        receiver_id=receiver_id,
        source_chat_id=chat.id,
        source_message_id=message.message_id,
        content_type=content_type,
        reply_to_original_chat_id=(
            int(reply_target["original_chat_id"]) if reply_target is not None else None
        ),
        reply_to_original_message_id=(
            int(reply_target["original_message_id"]) if reply_target is not None else None
        ),
        reply_context=reply_context,
        is_forward=is_forward,
    )

    sender_name = settings.name_for(user.id)
    receiver_name = settings.name_for(receiver_id)
    card_text = build_admin_card_text(
        request_id, sender_name, receiver_name, content_type,
        reply_context=reply_context,
    )

    admin_card = None
    try:
        admin_card = await context.bot.send_message(
            chat_id=settings.admin_id,
            text=card_text,
            reply_markup=review_keyboard(request_id),
        )
        db.set_admin_card(request_id, admin_card.message_id)

        if is_forward:
            # محتوا زیر کارت و به‌صورت فوروارد واقعی ارسال می‌شود تا
            # مبدأ فوروارد در پنل ادمین از بین نرود.
            await context.bot.forward_message(
                chat_id=settings.admin_id,
                from_chat_id=chat.id,
                message_id=message.message_id,
            )
        else:
            # محتوای اصلی به‌صورت Reply به کارت متصل می‌شود.
            await context.bot.copy_message(
                chat_id=settings.admin_id,
                from_chat_id=chat.id,
                message_id=message.message_id,
                reply_parameters=ReplyParameters(message_id=admin_card.message_id),
            )

        db.set_content_ready(request_id, True)

    except Exception:
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
            except Exception:
                logger.exception("ویرایش کارت ناموفق درخواست %s ناموفق بود", request_id)
        else:
            # در خطای ارسال فوروارد یا ساخت کارت، یک کارت خطا به ادمین نشان می‌دهیم.
            try:
                error_card = await context.bot.send_message(
                    chat_id=settings.admin_id,
                    text=(
                        card_text
                        + "\n\n⚠️ ارسال محتوای اصلی برای بررسی ادمین ناموفق بود."
                    ),
                )
                db.set_admin_card(request_id, error_card.message_id)
            except Exception:
                logger.exception("ارسال کارت خطا برای درخواست %s ناموفق بود", request_id)
        # برای فرستنده هیچ پیام تأیید/خطای اضافه‌ای ارسال نمی‌شود.
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

    # callback حتماً پاسخ می‌گیرد تا پنجرهٔ «در حال ثبت تصمیم» معلق نماند.
    try:
        await query.answer("در حال ثبت تصمیم...")
    except TelegramError:
        logger.warning("پاسخ اولیه callback برای درخواست %s ارسال نشد", request_id)

    sender_id = int(request["sender_id"])
    receiver_id = int(request["receiver_id"])
    sender_name = settings.name_for(sender_id)
    receiver_name = settings.name_for(receiver_id)
    content_type = str(request["content_type"])
    reply_context = request.get("reply_context")

    if action == "approve":
        try:
            reply_chat_id = request.get("reply_to_original_chat_id")
            reply_message_id = request.get("reply_to_original_message_id")
            has_reply_target = (
                reply_chat_id is not None
                and reply_message_id is not None
                and int(reply_chat_id) == receiver_id
            )
            is_forward = bool(request.get("is_forward"))

            if has_reply_target:
                # forwardMessage در Bot API پارامتر reply_parameters ندارد.
                # برای حفظ رابطهٔ واقعی reply از copy_message استفاده می‌کنیم.
                delivery = await context.bot.copy_message(
                    chat_id=receiver_id,
                    from_chat_id=int(request["source_chat_id"]),
                    message_id=int(request["source_message_id"]),
                    reply_parameters=ReplyParameters(
                        message_id=int(reply_message_id),
                        allow_sending_without_reply=True,
                    ),
                )
                if is_forward:
                    logger.warning(
                        "درخواست %s هم‌زمان فوروارد و ریپلای است؛ برای حفظ ریپلای، "
                        "پیام با copy_message تحویل شد و مبدأ فوروارد در مقصد حفظ نمی‌شود.",
                        request_id,
                    )
            elif is_forward:
                # فقط پیام‌های واقعاً فورواردشده را forward می‌کنیم.
                delivery = await context.bot.forward_message(
                    chat_id=receiver_id,
                    from_chat_id=int(request["source_chat_id"]),
                    message_id=int(request["source_message_id"]),
                )
            else:
                # پیام معمولی کپی می‌شود تا به‌اشتباه به‌شکل «فوروارد از فرستنده» دیده نشود.
                delivery = await context.bot.copy_message(
                    chat_id=receiver_id,
                    from_chat_id=int(request["source_chat_id"]),
                    message_id=int(request["source_message_id"]),
                )

            delivered_message_id = int(delivery.message_id)
            db.record_delivery_and_finish(
                recipient_chat_id=receiver_id,
                recipient_message_id=delivered_message_id,
                original_chat_id=int(request["source_chat_id"]),
                original_message_id=int(request["source_message_id"]),
                sender_id=sender_id,
                receiver_id=receiver_id,
                request_id=request_id,
            )
            result_line = (
                f"✅ پیام شماره {request_id} تأیید و برای {receiver_name} ارسال شد."
            )
            if bool(request.get("is_forward")) and request.get("reply_to_original_message_id") is not None:
                result_line += "\n⚠️ به‌دلیل محدودیت تلگرام، در پیامِ هم‌زمان فوروارد و ریپلای، حفظ ریپلای در اولویت قرار گرفت."

        except Exception as exc:
            # TypeError و خطاهای برنامه‌نویسی نیز باید مدیریت شوند؛ قبلاً فقط
            # TelegramError گرفته می‌شد و callback نیمه‌کاره می‌ماند.
            logger.exception("تحویل/ثبت درخواست %s به گیرنده ناموفق بود", request_id)
            try:
                db.mark_failed(request_id)
            except Exception:
                logger.exception("ثبت وضعیت FAILED برای درخواست %s ناموفق بود", request_id)
            result_line = (
                f"⚠️ پیام شماره {request_id} تأیید نشد؛ تحویل آن به {receiver_name} ناموفق بود.\n"
                f"جزئیات خطا: {type(exc).__name__}"
            )
    else:
        try:
            db.finish_request(request_id, "REJECTED")
        except Exception:
            logger.exception("ثبت رد درخواست %s ناموفق بود", request_id)
            result_line = f"⚠️ ثبت رد پیام شماره {request_id} ناموفق بود."
        else:
            receiver_notice, sender_reply = rejection_notice_texts(
                sender_name, receiver_name
            )
            receiver_notified = False
            sender_notified = False

            try:
                await context.bot.send_message(chat_id=receiver_id, text=receiver_notice)
                receiver_notified = True
            except Exception:
                logger.exception(
                    "ارسال اعلان رد به گیرنده درخواست %s ناموفق بود", request_id
                )

            try:
                await context.bot.send_message(
                    chat_id=int(request["source_chat_id"]),
                    text=sender_reply,
                    reply_parameters=ReplyParameters(
                        message_id=int(request["source_message_id"])
                    ),
                )
                sender_notified = True
            except Exception:
                logger.exception(
                    "ارسال پاسخ ریپلای رد به فرستنده درخواست %s ناموفق بود", request_id
                )

            if receiver_notified and sender_notified:
                notification_result = "اعلان رد به هر دو طرف ارسال شد."
            elif receiver_notified:
                notification_result = "اعلان رد به گیرنده ارسال شد؛ ارسال پاسخ به فرستنده ناموفق بود."
            elif sender_notified:
                notification_result = "پاسخ رد برای فرستنده ارسال شد؛ اعلان گیرنده ناموفق بود."
            else:
                notification_result = "ارسال اعلان رد به طرفین ناموفق بود."
            result_line = f"❌ پیام شماره {request_id} رد شد؛ {notification_result}"

    final_card_text = build_admin_card_text(
        request_id,
        sender_name,
        receiver_name,
        content_type,
        result_line=result_line,
        reply_context=reply_context,
    )
    try:
        admin_card_id = request.get("admin_card_message_id")
        if admin_card_id is not None:
            await context.bot.edit_message_text(
                chat_id=settings.admin_id,
                message_id=int(admin_card_id),
                text=final_card_text,
                reply_markup=None,
            )
        else:
            await query.edit_message_text(text=final_card_text, reply_markup=None)
    except Exception:
        logger.exception("ویرایش کارت نهایی درخواست %s ناموفق بود", request_id)
        # تلاش دوم از مسیر خود callback، در صورت در دسترس بودن پیام callback.
        try:
            await query.edit_message_text(text=final_card_text, reply_markup=None)
        except Exception:
            logger.exception("تلاش دوم برای ویرایش کارت درخواست %s نیز ناموفق بود", request_id)


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
