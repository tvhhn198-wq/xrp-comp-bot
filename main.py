import os
import time
import logging
import threading
from datetime import datetime
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from dotenv import load_dotenv
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from xrpl.models.transactions import Payment
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.utils import xrp_to_drops
from xrpl.transaction import autofill_and_sign, submit_and_wait

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
FEE_WALLET_ADDRESS = os.getenv("FEE_WALLET_ADDRESS")  # Chỉ cần address, không cần secret!

# 20 ví prize (mỗi ví ~3 XRP gas)
PRIZE_WALLETS = []
for i in range(1, 21):
    addr = os.getenv(f"PRIZE_WALLET_{i}_ADDRESS")
    sec = os.getenv(f"PRIZE_WALLET_{i}_SECRET")
    if addr and sec:
        PRIZE_WALLETS.append({
            "address": addr,
            "secret": sec,
            "wallet": Wallet.from_seed(sec),
            "available": True
        })

CLIENT = JsonRpcClient("https://s1.ripple.com:51234/")

# Lưu cuộc thi đang chạy
competitions = {}

# ================== HELPER ==================
def get_free_wallet():
    for w in PRIZE_WALLETS:
        if w["available"]:
            w["available"] = False
            return w
    return None

async def send_xrp(wallet: Wallet, to_address: str, amount_xrp: float, memo: str = ""):
    tx = Payment(
        account=wallet.classic_address,
        destination=to_address,
        amount=xrp_to_drops(amount_xrp),
    )
    if memo:
        tx.memos = [{"memo": {"memo_data": memo.encode().hex()}}]
    
    signed = autofill_and_sign(tx, wallet, CLIENT)
    response = submit_and_wait(signed, CLIENT)
    return response.result

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "XRP Spending Competition Bot\n\n"
        "Use /comp rTOKEN_ISSUER in a group to start a new competition!"
    )

async def comp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin can create competition!")
        return
    if not context.args or not context.args[0].startswith("r"):
        await update.message.reply_text("Usage: /comp rTOKEN_ISSUER")
        return

    issuer = context.args[0]
    comp_id = f"{update.effective_chat.id}_{int(time.time())}"

    competitions[comp_id] = {
        "issuer": issuer,
        "group_id": update.effective_chat.id,
        "status": "setup",
        "players": defaultdict(lambda: {"buy_xrp": 0.0, "tokens_bought": 0.0, "tokens_sold": 0.0})
    }

    keyboard = [[InlineKeyboardButton("Setup Competition (Private)", callback_data=f"setup_{comp_id}")]]
    await update.message.reply_text(
        f"New competition created!\nToken issuer: `{issuer}`\n\n"
        "Click button to setup in private chat →",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("setup_"):
        comp_id = query.data.split("_", 1)[1]
        if query.from_user.id != ADMIN_ID:
            await query.edit_message_text("Only admin!")
            return

        keyboard = [
            [InlineKeyboardButton("30 min", callback_data=f"time_30_{comp_id}")],
            [InlineKeyboardButton("60 min", callback_data=f"time_60_{comp_id}")],
            [InlineKeyboardButton("90 min", callback_data=f"time_90_{comp_id}")],
        ]
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Choose duration:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.edit_message_text("Setup sent to your private chat!")

    elif query.data.startswith("time_"):
        minutes = int(query.data.split("_")[1])
        comp_id = query.data.split("_", 2)[2]

        wallet = get_free_wallet()
        if not wallet:
            await query.edit_message_text("No free prize wallet! Max 20 competitions.")
            return

        tag = int(time.time() * 1000) % 100000000
        competitions[comp_id].update({
            "duration": minutes * 60,
            "prize_wallet": wallet,
            "dest_tag": tag,
            "pool": 0.0,
            "status": "waiting_payment"
        })

        await query.edit_message_text(
            f"Competition ready!\n\n"
            f"Duration: {minutes} minutes\n"
            f"Send ≥50 XRP to:\n"
            f"`{wallet['address']}`\n"
            f"Tag: `{tag}`\n\n"
            f"When ≥50 XRP received → START button appears!",
            parse_mode="Markdown"
        )
        threading.Thread(target=monitor_payment, args=(comp_id,), daemon=True).start()

# ================== MAIN ==================
def monitor_payment(comp_id: str):
    # Simple monitoring (sẽ nâng cấp sau)
    time.sleep(300)  # tạm 5 phút test
    if competitions[comp_id]["status"] == "waiting_payment":
        # giả lập nhận 200 XRP
        competitions[comp_id]["pool"] = 200.0
        competitions[comp_id]["status"] = "ready"

app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("comp", comp))
app.add_handler(CallbackQueryHandler(button))

if __name__ == "__main__":
    print("Bot is starting...")
    app.run_polling(drop_pending_updates=True)
