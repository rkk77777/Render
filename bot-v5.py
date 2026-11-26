import asyncio
import logging
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)

# ==========================================
# НАСТРОЙКИ БОТА (@Maestro_vpn_official_bot)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_СЮДА_ТОКЕН_ОТ_BOTFATHER")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://rkk77777.github.io/MaestroVPN/")
PORT = int(os.getenv("PORT", "10000"))

PLANS = {
    "month": {"title": "1 месяц", "price": 69, "devices": 3, "days": 30},
    "q3":    {"title": "3 месяца", "price": 199, "devices": 5, "days": 90},
    "half":  {"title": "6 месяцев", "price": 399, "devices": 7, "days": 180},
    "year":  {"title": "12 месяцев (1 год)", "price": 799, "devices": 10, "days": 365},
}

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def mini_app_button(text: str = "✦ Открыть Maestro VPN") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=MINI_APP_URL))


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [mini_app_button("✦ Открыть Maestro VPN")],
            [
                InlineKeyboardButton(text="1 мес • 69 ₽", callback_data="buy:month"),
                InlineKeyboardButton(text="3 мес • 199 ₽", callback_data="buy:q3"),
            ],
            [
                InlineKeyboardButton(text="6 мес • 399 ₽", callback_data="buy:half"),
                InlineKeyboardButton(text="12 мес • 799 ₽", callback_data="buy:year"),
            ],
        ]
    )


def build_plan_invoice_text(plan_key: str, user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    plan = PLANS[plan_key]
    text = (
        f"✦ <b>Maestro VPN — Оформление тарифа</b>\n\n"
        f"• Тариф: <b>{plan['title']}</b> ({plan['days']} дн.)\n"
        f"• Лимит устройств: <b>до {plan['devices']}</b>\n"
        f"• К оплате: <b>{plan['price']} руб.</b>\n"
        f"• Ваш Telegram ID: <code>{user_id}</code>\n\n"
        f"Нажмите кнопку ниже для перехода к оплате или вернитесь в кабинет."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💳 Оплатить {plan['price']} руб.",
                    callback_data=f"confirm_pay:{plan_key}",
                )
            ],
            [mini_app_button("↩ Открыть кабинет Mini App")],
        ]
    )
    return text, kb


@dp.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject):
    args = (command.args or "").strip()
    user_id = message.from_user.id

    if args.startswith("pay_"):
        raw = args.removeprefix("pay_")
        plan_key = raw.split("_uid_")[0]
        if plan_key in PLANS:
            text, kb = build_plan_invoice_text(plan_key, user_id)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            return

    welcome_text = (
        f"✦ <b>Добро пожаловать в Maestro VPN</b>\n\n"
        f"Ваш Telegram ID: <code>{user_id}</code>\n"
        f"Откройте личный кабинет Mini App, чтобы забрать пробные <b>1,5 дня</b>, "
        f"выбрать конфигуратор (<b>Happ</b> или <b>v2RayTun</b>) или пригласить друзей."
    )
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=main_menu_keyboard())


@dp.callback_query(F.data.startswith("buy:"))
async def cb_buy_plan(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    if plan_key not in PLANS:
        await callback.answer("Тариф не найден", show_alert=True)
        return
    text, kb = build_plan_invoice_text(plan_key, callback.from_user.id)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("confirm_pay:"))
async def cb_confirm_pay(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]
    plan = PLANS.get(plan_key)
    if not plan:
        await callback.answer("Ошибка тарифа", show_alert=True)
        return

    await callback.message.answer(
        f"✅ Заявка на оплату <b>{plan['title']} ({plan['price']} руб.)</b> создана для ID <code>{callback.from_user.id}</code>.\n"
        f"Подключите сюда свой платёжный шлюз (ЮKassa / Stars / CryptoBot) и отправьте пользователю ключ для Happ / v2RayTun.",
        parse_mode="HTML",
    )
    await callback.answer()


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


async def main():
    await start_http_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
