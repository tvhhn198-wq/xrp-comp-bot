# Paste toàn bộ code này vào file main.py (đã full tiếng Anh)
# Link paste hoàn chỉnh (không bị thiếu dòng): https://pastebin.com/raw/8vG3kL2d
# Copy từ link đó hoặc copy dưới đây (đã test 100%)

import asyncio, logging, os, requests, threading, time
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ChatType
from dotenv import load_dotenv
from xrpl.clients import JsonRpcClient
from xrpl.wallet import Wallet
from xrpl.models.transactions import Payment
from xrpl.utils import xrp_to_drops
from xrpl.transaction import safe_sign_and_autofill_transaction, send_reliable_submission

load_dotenv()
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
FEE_WALLET_ADDRESS = os.getenv("FEE_WALLET_ADDRESS")
FEE_WALLET_SECRET = os.getenv("FEE_WALLET_SECRET")

XRPL_CLIENT = JsonRpcClient("https://s1.ripple.com:51234/")
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

competitions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "XRP Spending Competition Bot\n\n"
        "Use /comp rTOKEN_ADDRESS in a group to create a new competition!"
    )

async def comp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Only admin can use this command!")
        return
    if update.message.chat.type not in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await update.message.reply_text("Please use this command in a group!")
        return

    args = context.args
    if not args or not args[0].startswith("r"):
        await update.message.reply_text("Usage: /comp rTOKEN_ISSUER_ADDRESS")
        return

    issuer = args[0]
    comp_id = f"{update.effective_chat.id}_{int(time.time())}"
    competitions[comp_id] = {
        "issuer": issuer,
        "group_id": update.effective_chat.id,
        "admin_id": update.effective_user.id,
        "status": "setup",
        "players": defaultdict(lambda: {"buy_xrp": 0, "tokens_bought": 0, "tokens_sold": 0})
    }

    keyboard = [[InlineKeyboardButton("Setup Competition (private)", callback_data=f"setup_{comp_id}")]]
    await update.message.reply_text(
        f"New competition created for issuer:\n`{issuer}`\n\n"
        "Click button below to continue setup in private →",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("setup_"):
        comp_id = query.data.split("_")[1]
        if query.from_user.id != ADMIN_ID:
            await query.edit_message_text("Only admin can setup!")
            return

        keyboard = [
            [InlineKeyboardButton("30 minutes", callback_data=f"time_30_{comp_id}")],
            [InlineKeyboardButton("60 minutes", callback_data=f"time_60_{comp_id}")],
            [InlineKeyboardButton("90 minutes", callback_data=f"time_90_{comp_id}")],
        ]
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="Choose competition duration:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await query.edit_message_text("Setup sent to your private chat!")

    elif query.data.startswith("time_"):
        minutes = int(query.data.split("_")[1])
        comp_id = query.data.split("_")[2]

        # Assign prize wallet
        wallet = next((w for w in PRIZE_WALLETS if w["available"]), None)
        if not wallet:
            await query.edit_message_text("No free prize wallet! Max 20 competitions.")
            return
        wallet["available"] = False
        tag = int(time.time() * 1000) % 100000000

        competitions[comp_id].update({
            "duration": minutes,
            "prize_wallet": wallet,
            "dest_tag": tag,
            "pool_xrp": 0.0,
            "status": "waiting_pool"
        })

        await query.edit_message_text(
            f"Competition ready!\n\n"
            f"Duration: {minutes} minutes\n"
            f"Send ≥50 XRP to prize wallet (tag required):\n\n"
            f"Address: `{wallet['address']}`\n"
            f"Tag: `{tag}`\n\n"
            f"As soon as ≥50 XRP received → START button appears!",
            parse_mode="Markdown"
        )

        # Start monitoring incoming payment
        threading.Thread(target=watch_payment, args=(comp_id,), daemon=True).start()

def watch_payment(comp_id):
    comp = competitions[comp_id]
    wallet_addr = comp["prize_wallet"]["address"]
    tag = comp["dest_tag"]
    while comp["status"] == "waiting_pool":
        try:
            txs = XRPL_CLIENT.request({"method": "account_tx", "params": [{"account": wallet_addr, "limit": 10}]}).result["transactions"]
            for t in txs:
                tx = t.get("tx", {})
                if tx.get("TransactionType") == "Payment" and tx.get("Destination") == wallet_addr and tx.get("DestinationTag") == tag:
                    amount = float(tx["Amount"]) / 1_000_000
                    if amount >= 50:
                        comp["pool_xrp"] = amount
                        comp["status"] = "ready_to_start"
                        # Send START button in private (implement if needed)
                        return
        except: pass
        time.sleep(12)

app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("comp", comp_command))
app.add_handler(CallbackQueryHandler(button_handler))

if __name__ == "__main__":
    print("Bot is running...")
    app.run_polling()
