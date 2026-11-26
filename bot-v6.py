import asyncio
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
)

# ==========================================
# НАСТРОЙКИ БОТА (@Maestro_vpn_official_bot)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://rkk77777.github.io/MaestroVPN/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "Maestro_vpn_official_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "5922226163"))
PORT = int(os.getenv("PORT", "10000"))

PLANS = {
    "month": {"title": "1 месяц", "price": 69, "devices": 3, "days": 30},
    "q3": {"title": "3 месяца", "price": 199, "devices": 5, "days": 90},
    "half": {"title": "6 месяцев", "price": 399, "devices": 7, "days": 180},
    "year": {"title": "12 месяцев (1 год)", "price": 799, "devices": 10, "days": 365},
}

# Временное хранилище в памяти (при перезапуске сбрасывается; для продакшена подключи БД)
# user_id -> {"expires_at": datetime | None, "devices": int, "referrals": int}
USER_DATA: dict[int, dict] = {}


class SupportStates(StatesGroup):
    waiting_message = State()


logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def get_user_record(user_id: int) -> dict:
    if user_id not in USER_DATA:
        USER_DATA[user_id] = {
            "expires_at": None,
            "devices": 3,
            "referrals": 0,
        }
    return USER_DATA[user_id]


def format_subscription_status(user_id: int) -> str:
    rec = get_user_record(user_id)
    expires_at: datetime | None = rec.get("expires_at")
    now = datetime.now(timezone.utc)
    if not expires_at or expires_at <= now:
        return "❌ Подписка не активна (0 дн. осталось)"
    delta = expires_at - now
    days_left = max(1, (delta.days + (1 if delta.seconds > 0 else 0)))
    date_str = expires_at.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")
    return f"✅ Активна до <b>{date_str}</b> (осталось ~<b>{days_left} дн.</b>)"


def build_referral_link(user_id: int) -> str:
    clean_bot = BOT_USERNAME.lstrip("@")
    return f"https://t.me/{clean_bot}?start=ref_{user_id}"


def back_button(target: str = "nav:start") -> InlineKeyboardButton:
    return InlineKeyboardButton(text="⬅️ Вернуться назад", callback_data=target)


def reply_hints_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🏠 Меню (/start)"), KeyboardButton(text="👤 Кабинет")],
            [KeyboardButton(text="💎 Тарифы"), KeyboardButton(text="👥 Добавить друзей")],
            [KeyboardButton(text="🆘 Поддержка"), KeyboardButton(text="❓ Помощь (/help)")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите подсказку или напишите сообщение...",
    )


def start_keyboard() -> InlineKeyboardMarkup:
    app_btn = InlineKeyboardButton(
        text="📱 Открыть приложение",
        web_app=WebAppInfo(url=MINI_APP_URL),
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [app_btn],
            [
                InlineKeyboardButton(text="👤 Кабинет", callback_data="nav:cabinet"),
                InlineKeyboardButton(text="💎 Тарифы", callback_data="nav:tariffs"),
            ],
            [
                InlineKeyboardButton(text="👥 Добавить друзей", callback_data="nav:referral"),
                InlineKeyboardButton(text="🆘 Поддержка", callback_data="nav:support"),
            ],
            [
                InlineKeyboardButton(text="❓ Справка (/help)", callback_data="nav:help"),
            ],
        ]
    )


def cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Продлить подписку", callback_data="nav:tariffs")],
            [back_button("nav:start")],
        ]
    )


def tariffs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 мес • 69 ₽", callback_data="buy:month"),
                InlineKeyboardButton(text="3 мес • 199 ₽", callback_data="buy:q3"),
            ],
            [
                InlineKeyboardButton(text="6 мес • 399 ₽", callback_data="buy:half"),
                InlineKeyboardButton(text="12 мес • 799 ₽", callback_data="buy:year"),
            ],
            [back_button("nav:cabinet")],
        ]
    )


def build_plan_invoice_text(plan_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    plan = PLANS[plan_key]
    text = (
        f"💎 <b>Maestro VPN — Оформление тарифа</b>\n\n"
        f"• Тариф: <b>{plan['title']}</b> ({plan['days']} дн.)\n"
        f"• Лимит устройств: <b>до {plan['devices']}</b>\n"
        f"• К оплате: <b>{plan['price']} руб.</b>\n"
        f"• Ваш Telegram ID: <code>{user_id}</code>\n\n"
        f"Нажмите кнопку ниже для оформления или вернитесь назад к тарифам."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить {plan['price']} руб.",
                    callback_data=f"confirm_pay:{plan_key}",
                )
            ],
            [back_button("nav:tariffs")],
        ]
    )
    return text, kb


def referral_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ref_link = build_referral_link(user_id)
    share_text = urllib.parse.quote("Попробуй Maestro VPN — быстрый доступ и удобный кабинет:")
    share_url = urllib.parse.quote(ref_link)
    telegram_share_url = f"https://t.me/share/url?url={share_url}&text={share_text}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться в Telegram", url=telegram_share_url)],
            [InlineKeyboardButton(text="📋 Скопировать вашу ссылку", callback_data="ref:copy")],
            [back_button("nav:start")],
        ]
    )


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👤 Посмотреть статус подписки", callback_data="nav:cabinet")],
            [InlineKeyboardButton(text="💎 Перейти к тарифам", callback_data="nav:tariffs")],
            [InlineKeyboardButton(text="👥 Пригласить друзей", callback_data="nav:referral")],
            [InlineKeyboardButton(text="🆘 Написать в поддержку", callback_data="nav:support")],
            [back_button("nav:start")],
        ]
    )


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [back_button("nav:start")],
        ]
    )


def build_start_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"✦ <b>Maestro VPN — Главное меню</b>\n\n"
        f"🆔 Ваш Telegram ID: <code>{user_id}</code>\n"
        f"📊 Статус: {format_subscription_status(user_id)}\n\n"
        f"Выберите нужный раздел на кнопках ниже или воспользуйтесь подсказками клавиатуры:"
    )
    return text, start_keyboard()


def build_cabinet_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    rec = get_user_record(user_id)
    text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"📅 Когда кончится подписка: {format_subscription_status(user_id)}\n"
        f"📱 Лимит устройств: <b>до {rec['devices']}</b>\n"
        f"👥 Приглашено друзей: <b>{rec['referrals']}</b>"
    )
    return text, cabinet_keyboard()


def build_tariffs_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"💎 <b>Тарифы Maestro VPN</b>\n\n"
        f"• <b>1 месяц</b> — <b>69 ₽</b> (до 3 устройств, 30 дн.)\n"
        f"• <b>3 месяца</b> — <b>199 ₽</b> (до 5 устройств, 90 дн.)\n"
        f"• <b>6 месяцев</b> — <b>399 ₽</b> (до 7 устройств, 180 дн.)\n"
        f"• <b>12 месяцев</b> — <b>799 ₽</b> (до 10 устройств, 365 дн.)\n\n"
        f"Выберите подходящий вариант:"
    )
    return text, tariffs_keyboard()


def build_referral_message(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    ref_link = build_referral_link(user_id)
    rec = get_user_record(user_id)
    text = (
        f"👥 <b>Добавить друзей</b>\n\n"
        f"Приглашено друзей: <b>{rec['referrals']}</b>\n"
        f"Ваша реферальная ссылка:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Нажмите кнопку ниже, чтобы выбрать кому отправить ссылку в Telegram, или скопируйте её."
    )
    return text, referral_keyboard(user_id)


def build_help_message() -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"❓ <b>Справка — что делать в разных ситуациях</b>\n\n"
        f"1. <b>Посмотреть статус подписки</b> — откройте «Кабинет», чтобы узнать дату окончания и остаток дней.\n"
        f"2. <b>Продлить или купить подписку</b> — нажмите «Продлить подписку» в кабинете или «Тарифы» в меню.\n"
        f"3. <b>Закончилась подписка</b> — перейдите в «Тарифы» и выберите подходящий срок действия.\n"
        f"4. <b>Нужен личный кабинет Mini App</b> — нажмите кнопку «Открыть приложение».\n"
        f"5. <b>Хотите пригласить друзей</b> — откройте «Добавить друзей» и отправьте свою ссылку.\n"
        f"6. <b>Не знаете, кому отправить ссылку</b> — нажмите «Поделиться в Telegram» и выберите чат или контакт.\n"
        f"7. <b>Нужно скопировать ссылку</b> — нажмите «Скопировать вашу ссылку» в разделе друзей.\n"
        f"8. <b>Возникла проблема с подключением</b> — напишите в «Поддержка», обращение уйдёт администратору.\n"
        f"9. <b>Не приходит ответ от поддержки</b> — проверьте, что в профиле Telegram открыты входящие сообщения.\n"
        f"10. <b>Нужно подключить несколько устройств</b> — выберите тариф с нужным лимитом (3, 5, 7 или 10 устройств).\n"
        f"11. <b>Хотите сэкономить на сроке</b> — сравните тарифы на 3, 6 и 12 месяцев в разделе «Тарифы».\n"
        f"12. <b>Потерялись в меню</b> — нажмите «Вернуться назад» в любом разделе или введите /start.\n"
        f"13. <b>Нужны быстрые подсказки</b> — откройте клавиатуру внизу поля ввода сообщения.\n"
        f"14. <b>Нужен ваш Telegram ID</b> — он отображается на старте и в разделе «Кабинет».\n"
        f"15. <b>Остался вопрос по работе сервиса</b> — опишите ситуацию в разделе «Поддержка»."
    )
    return text, help_keyboard()


async def send_or_edit_callback(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception:
        await callback.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    await callback.answer()


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    args = (command.args or "").strip()
    user_id = message.from_user.id
    rec = get_user_record(user_id)

    if args.startswith("ref_"):
        inviter_raw = args.removeprefix("ref_")
        if inviter_raw.isdigit():
            inviter_id = int(inviter_raw)
            if inviter_id != user_id:
                inviter_rec = get_user_record(inviter_id)
                inviter_rec["referrals"] += 1

    if args.startswith("pay_"):
        raw = args.removeprefix("pay_")
        plan_key = raw.split("_uid_")[0]
        if plan_key in PLANS:
            text, kb = build_plan_invoice_text(plan_key, user_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return

    _ = rec
    start_text, start_kb = build_start_message(user_id)
    await message.answer("⌨️ Подсказки клавиатуры активированы.", reply_markup=reply_hints_keyboard())
    await message.answer(start_text, parse_mode="HTML", reply_markup=start_kb)


@dp.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# Обработка быстрых подсказок из нижней клавиатуры чата
@dp.message(F.text == "🏠 Меню (/start)")
async def hint_start(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_start_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👤 Кабинет")
async def hint_cabinet(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_cabinet_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "💎 Тарифы")
async def hint_tariffs(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_tariffs_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "👥 Добавить друзей")
async def hint_referral(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_referral_message(message.from_user.id)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.message(F.text == "🆘 Поддержка")
async def hint_support(message: Message, state: FSMContext):
    await state.set_state(SupportStates.waiting_message)
    await message.answer(
        "🆘 <b>Поддержка</b>\n\nНапишите ваше сообщение следующим сообщением — я передам его администратору.",
        parse_mode="HTML",
        reply_markup=support_keyboard(),
    )


@dp.message(F.text == "❓ Помощь (/help)")
async def hint_help(message: Message, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# Навигация по inline-кнопкам
@dp.callback_query(F.data == "nav:start")
async def cb_nav_start(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_start_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:cabinet")
async def cb_nav_cabinet(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_cabinet_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:tariffs")
async def cb_nav_tariffs(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_tariffs_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:referral")
async def cb_nav_referral(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_referral_message(callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "ref:copy")
async def cb_ref_copy(callback: CallbackQuery):
    ref_link = build_referral_link(callback.from_user.id)
    await callback.answer("Ссылка отправлена ниже для удобного копирования", show_alert=False)
    await callback.message.answer(
        f"📋 <b>Ваша ссылка для приглашения друзей:</b>\n<code>{ref_link}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:referral")]]),
    )


@dp.callback_query(F.data == "nav:help")
async def cb_nav_help(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    text, kb = build_help_message()
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data == "nav:support")
async def cb_nav_support(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SupportStates.waiting_message)
    text = (
        "🆘 <b>Поддержка</b>\n\n"
        "Напишите ваш вопрос или описание проблемы следующим сообщением — оно придёт напрямую администратору."
    )
    await send_or_edit_callback(callback, text, support_keyboard())


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    if plan_key not in PLANS:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    text, kb = build_plan_invoice_text(plan_key, callback.from_user.id)
    await send_or_edit_callback(callback, text, kb)


@dp.callback_query(F.data.startswith("confirm_pay:"))
async def cb_confirm_pay(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Ошибка тарифа", show_alert=True)
        return

    user_id = callback.from_user.id
    rec = get_user_record(user_id)
    now = datetime.now(timezone.utc)
    base = rec["expires_at"] if (rec["expires_at"] and rec["expires_at"] > now) else now
    rec["expires_at"] = base + timedelta(days=plan["days"])
    rec["devices"] = plan["devices"]

    await callback.message.answer(
        f"✅ Заявка на тариф <b>{plan['title']} ({plan['price']} руб.)</b> оформлена для ID <code>{user_id}</code>.\n"
        f"Статус подписки: {format_subscription_status(user_id)}\n"
        f"Подключите сюда свой платёжный шлюз (ЮKassa / Stars / CryptoBot) для реального списания средств.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👤 Перейти в кабинет", callback_data="nav:cabinet")],
                [back_button("nav:tariffs")],
            ]
        ),
    )
    await callback.answer()


# Приём сообщения пользователя в поддержку и пересылка администратору (ADMIN_ID = 5922226163)
@dp.message(SupportStates.waiting_message)
async def handle_support_message(message: Message, state: FSMContext):
    user = message.from_user
    username_str = f"@{user.username}" if user.username else "без username"
    admin_text = (
        f"📩 <b>Новое обращение в поддержку</b>\n\n"
        f"👤 От: <b>{user.full_name}</b> ({username_str})\n"
        f"🆔 Telegram ID: <code>{user.id}</code>\n\n"
        f"💬 Сообщение:\n{message.html_text or message.text or '[медиа/вложение]'}"
    )

    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, parse_mode="HTML")
        if message.text is None:
            await message.copy_to(chat_id=ADMIN_ID)
    except Exception as exc:
        logging.error(f"Failed to forward support message to ADMIN_ID={ADMIN_ID}: {exc}")

    await state.clear()
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку! Ожидайте ответ администратора.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_button("nav:start")]]),
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK — Maestro VPN bot is running")


async def start_http_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"HTTP health server listening on port {PORT}")


async def on_startup():
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню и кабинет"),
            BotCommand(command="help", description="Справка по всем ситуациям"),
        ]
    )


async def main():
    dp.startup.register(on_startup)
    await start_http_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
