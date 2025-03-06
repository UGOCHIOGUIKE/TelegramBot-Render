
import firebase_admin
from firebase_admin import credentials, db
import requests
import telebot
from telebot import TeleBot, types
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
import datetime
import schedule
import time
import threading
import traceback
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import json
import os
from dotenv import load_dotenv
import logging
from flask import Flask
from threading import Thread

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_errors.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Setup Flask server
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))


# Start Flask in a separate thread
flask_thread = Thread(target=run_flask)
flask_thread.daemon = True
flask_thread.start()

logger.info("Flask server started")

# Load secrets from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")


# Ensure required variables exist
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN is missing. Check environment variables.")

# Convert ADMIN_CHAT_ID to integer
try:
    if not ADMIN_CHAT_ID or not ADMIN_CHAT_ID.strip():
        raise ValueError("ADMIN_CHAT_ID is missing or empty")
    
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)  # Convert string to int
except ValueError as e:
    ADMIN_CHAT_ID = None  # Set to None if conversion fails
    logger.error(f"❌ ADMIN_CHAT_ID Error: {str(e)}")

# Initialize Telegram Bot
bot = telebot.TeleBot(BOT_TOKEN)
logger.info("✅ Telegram bot initialized successfully.")


# Initialize Firebase
try:
    # Check if we're using environment variable for credentials (Render deployment)
    if FIREBASE_CREDENTIALS_JSON:
        logger.info("Using Firebase credentials from environment variable")
        # Parse the JSON string from environment variable
        firebase_credentials_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
        
        # Initialize with credentials from environment variable
        cred = credentials.Certificate(firebase_credentials_dict)
    else:
        # Fall back to file-based credentials for local development
        logger.info("Using Firebase credentials from local file")
        firebase_credentials_path = "firebase_credentials.json"

        # Validate JSON file before use
        with open(firebase_credentials_path, "r") as f:
            json.load(f)  # Just to verify it's valid JSON

        cred = credentials.Certificate(firebase_credentials_path)

    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred, {'databaseURL': DATABASE_URL})

    # Test Firebase connection
    ref = db.reference("/")
    test_data = ref.get()
    logger.info("✅ Successfully connected to Firebase!")
    logger.info(f"🔍 Sample Data: {test_data}")

except json.JSONDecodeError as e:
    logger.error(f"❌ Invalid JSON in Firebase credentials: {e}")
    raise
except Exception as e:
    logger.error(f"❌ Firebase Connection Error: {str(e)}")
    logger.error(traceback.format_exc())
    raise

# Global variables
user_registration = {}
transactions = {}
TRANSACTION_TIMEOUT = 15 * 60  # seconds
transaction_lock = threading.Lock()

# Supported USDT Networks
USDT_NETWORKS = ["TRC20", "ERC20", "BEP20"]

# Company USDT Wallets
COMPANY_WALLETS = {
    "TRC20": "TGpQAU6CcHo6rTHrf6gseZy6eu1qnQ4g5m",
    "BEP20": "0x9498665dc2ca80d8cd108fe76734989960ec85bc"
}

# Admin bank account details
ADMIN_ACCOUNT_DETAILS = "Bank: Zenith Bank Pc\nAcct Name: MECH XPERT AUTO SERVICES\nAcct No: 1219799200"

# Support email address
SUPPORT_EMAIL = "rehobotics.technologies@gmail.com"

# Error Handler Function
def error_handler(func):
    """Decorator to handle API errors and avoid crashes."""
    def wrapper(message, *args, **kwargs):
        try:
            return func(message, *args, **kwargs)
        except telebot.apihelper.ApiTelegramException as e:
            logger.error(f"Telegram API Error: {e}")
            if e.error_code == 429:
                wait_time = int(e.result_json['parameters']['retry_after'])
                logger.warning(f"⚠️ Rate limit hit! Waiting {wait_time} seconds...")
                time.sleep(wait_time)
                return func(message, *args, **kwargs)  # Retry after waiting
            else:
                time.sleep(1)  # Add delay before sending the error message
                try:
                    bot.send_message(message.chat.id, "❌ An error occurred. Please try again later.")
                except Exception as msg_error:
                    logger.error(f"Failed to send error message: {msg_error}")
                logger.error(f"Unhandled Error: {e}")
        except Exception as e:
            logger.error(f"Unexpected Error in {func.__name__}: {e}")
            logger.error(traceback.format_exc())
            try:
                time.sleep(1)  # Prevent rapid error messages
                bot.send_message(message.chat.id, "❌ A system error occurred. Contact support.")
            except Exception as msg_error:
                logger.error(f"Failed to send error message: {msg_error}")
    return wrapper

# Function to keep bot active by sending messages to admin
def keep_bot_alive():
    try:
        if not isinstance(ADMIN_CHAT_ID, int):
            raise ValueError("ADMIN_CHAT_ID is not a valid integer.")

        bot.send_message(ADMIN_CHAT_ID, "🚀 Bot is active and running!")
        logger.info("✅ Sent keep-alive message to admin")
    except Exception as e:
        logger.error(f"❌ Failed to send keep-alive message: {e}")

# Schedule keep-alive messages every 20 minutes
schedule.every(20).minutes.do(keep_bot_alive)

# Run scheduled tasks in a separate thread
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(10)

# Start scheduler in the background
scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
scheduler_thread.start()
logger.info("✅ Keep-alive mechanism activated. Bot will send messages every 20 minutes.")

def log_transaction(telegram_username, transaction_data):
    """Logs transaction details to Firebase."""
    try:
        transaction_ref = db.reference(f'transactions/{telegram_username}/{transaction_data["transaction_id"]}')
        transaction_ref.set(transaction_data)
        logger.info(f"Transaction logged for user {telegram_username}")
    except Exception as e:
        logger.error(f"Failed to log transaction: {e}")

def generate_transaction_id():
    """Generates a unique transaction ID."""
    return datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")

def logout_user(telegram_username):
    """Logs out a user by clearing their transaction data."""
    if telegram_username in transactions:
        del transactions[telegram_username]
        try:
            bot.send_message(telegram_username, "🔒 You have been logged out due to inactivity. Please /login to start a new transaction.")
        except Exception as e:
            logger.error(f"Failed to send logout message: {e}")

def start_countdown_timer(telegram_username):
    """Starts a countdown timer for the transaction."""
    with transaction_lock:
        if telegram_username not in transactions:
            transactions[telegram_username] = {}  # Ensure the user dictionary exists
        
        # Generate a new transaction ID if none exists
        if "transaction_id" not in transactions[telegram_username]:
            transactions[telegram_username]["transaction_id"] = generate_transaction_id()
            
        transaction_id = transactions[telegram_username]["transaction_id"]
        transactions[telegram_username]["timer"] = TRANSACTION_TIMEOUT

        # Ensure "timer_message_id" exists
        if "timer_message_id" not in transactions[telegram_username]:
            try:
                msg = bot.send_message(telegram_username, "⏳ Time remaining: 15:00")
                transactions[telegram_username]["timer_message_id"] = msg.message_id
            except Exception as e:
                logger.error(f"Failed to send timer message: {e}")

    def countdown():
        while True:
            with transaction_lock:
                if (
                    telegram_username not in transactions or 
                    transactions[telegram_username].get("transaction_id") != transaction_id or 
                    transactions[telegram_username].get("timer", 0) <= 0
                ):
                    break  # Stop countdown if user transaction no longer exists

                minutes, seconds = divmod(transactions[telegram_username]["timer"], 60)

                try:
                    bot.edit_message_text(
                        f"⏳ Time remaining: {minutes:02d}:{seconds:02d}",
                        chat_id=telegram_username,
                        message_id=transactions[telegram_username]["timer_message_id"]
                    )
                except Exception as e:
                    logger.error(f"Error editing timer message: {e}")
                    break  # Stop the timer if message can't be edited

                transactions[telegram_username]["timer"] -= 1

            time.sleep(1)

        with transaction_lock:
            if telegram_username in transactions and transactions[telegram_username].get("transaction_id") == transaction_id:
                try:
                    bot.send_message(telegram_username, "⏱️ Transaction timed out!")
                    logout_user(telegram_username)
                except Exception as e:
                    logger.error(f"Failed to send timeout message: {e}")

    timer_thread = threading.Thread(target=countdown, daemon=True)
    timer_thread.start()

# Email validation function
def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Name validation function
def is_valid_name(name):
    if len(name.split()) < 2:
        return False
    if len(name) < 3 or len(name) > 100:
        return False
    return True

# Account number validation function (10 digits for Nigerian banks)
def is_valid_account_number(account_number):
    return account_number.isdigit() and len(account_number) == 10

# Wallet address validation function (basic check)
def is_valid_wallet_address(wallet_address, network):
    if not wallet_address:
        return False

    # TRC20 (TRON) addresses typically start with T and are 34 characters long
    if network == "TRC20" and wallet_address[0] == "T" and len(wallet_address) == 34:
        return True

    # BEP20 and ERC20 (Ethereum-based) addresses are 42 characters and start with 0x
    if (network in ["BEP20", "ERC20"]) and wallet_address.startswith("0x") and len(wallet_address) == 42:
        return True

    return False

# Get exchange rate from CoinGecko and add markup
def get_exchange_rate(action="buy") -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=ngn"
    try:
        response = requests.get(url, timeout=30)
        data = response.json()

        # Get exchange rate safely
        coingecko_rate = data.get("tether", {}).get("ngn")

        if coingecko_rate is None:
            logger.warning("⚠️ API response did not return a valid exchange rate. Using fallback rate.")
            return 1400.0  # Fallback rate

        # Apply markup for buying and selling
        if action == "buy":
            return float(coingecko_rate) + 30  # Buying price
        elif action == "sell":
            return float(coingecko_rate) - 8  # Selling price
        else:
            logger.warning("⚠️ Invalid action passed to get_exchange_rate(). Using fallback rate.")
            return 1400.0
    except Exception as e:
        logger.error(f"⚠️ API Request Failed: {e}. Using fallback rate.")
        return 1400.0  # Fallback rate

@bot.message_handler(commands=['start'])
@error_handler
def send_welcome(message):
    warning_text = (
        "⚠️ *SCAM ALERT!* ⚠️\n\n"
        "🚨 *No transaction outside this bot is permitted or authorized.*\n"
        "🚫 *Admin will NEVER call or message you for transactions outside this bot.*\n"
        "❌ *Anyone who falls victim to scammers does so at their own risk. The admin will not be held responsible.*\n"
        "✅ *Always ensure your transactions are done within this bot for safety.* "
    )

    chat_id = message.chat.id
    
    # Message to be pinned
    pinned_text = "📌 * Tap 'Welcome' to start using this bot!* "

    # Send the pinned message first
    try:
        pinned_msg = bot.send_message(chat_id, pinned_text, parse_mode="Markdown")

        # Attempt to pin the message (Requires the bot to be admin)
        try:
            bot.pin_chat_message(chat_id, pinned_msg.message_id)
        except Exception as e:
            logger.warning(f"Failed to pin message: {e}")
    except Exception as e:
        logger.error(f"Failed to send welcome message: {e}")

    # Create a custom keyboard with a "Welcome" button
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    welcome_button = types.KeyboardButton("👋 Welcome")
    markup.add(welcome_button)

    # Send the warning message
    try:
        bot.send_message(message.chat.id, warning_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to send warning message: {e}")

    # Send the welcome message with the keyboard
    try:
        bot.send_message(chat_id, "👋 *Welcome to Crypto-Naira Exchange!*\n\n"
                                "Tap 'Welcome' below to proceed.", parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        logger.error(f"Failed to send welcome message with keyboard: {e}")


# Handle the "Welcome" button press
@bot.message_handler(func=lambda message: message.text == "👋 Welcome")
@error_handler
def handle_welcome_button(message):
    bot.reply_to(message, "🎉 You're now ready to use this bot! Use /register to create an account or /login to access your account.")

@bot.message_handler(commands=['register'])
@error_handler
def register_user_step1(message):
    user_id = str(message.from_user.id)
    telegram_username = message.from_user.username

    if not telegram_username:  
        bot.reply_to(message, "❌ You need a Telegram username to register. Please go to your Telegram >> Profile and set a User Name.")
        return

    user_ref = db.reference(f'Members/{telegram_username}')
    user_data = user_ref.get()

    if user_data:
        bot.reply_to(message, "⚠️ You are already registered! Use /login to access your account.")
        return

    bot.reply_to(message, "📝 Please enter your first Name and Last name:")
    user_registration[telegram_username] = {"step": 1, "user_id": user_id}

@bot.message_handler(func=lambda message: message.from_user.username in user_registration and user_registration[message.from_user.username]["step"] == 1)
@error_handler
def register_user_step2(message):
    telegram_username = message.from_user.username
    full_name = message.text.strip()

    if not is_valid_name(full_name):
        bot.reply_to(message, "❌ Invalid name format. Please enter your full name.")
        return

    user_registration[telegram_username]["full_name"] = full_name
    user_registration[telegram_username]["step"] = 2
    bot.reply_to(message, "📧 Please enter your email address:")

@bot.message_handler(func=lambda message: message.from_user.username in user_registration and user_registration[message.from_user.username]["step"] == 2)
@error_handler
def register_user_step3(message):
    telegram_username = message.from_user.username
    email = message.text.strip()

    if not is_valid_email(email):
        bot.reply_to(message, "❌ Invalid email format. Please enter a valid email address.")
        return

    user_registration[telegram_username]["email"] = email
    user_registration[telegram_username]["step"] = 3

    registration_details = f"👤 *Registration Details:*\n\n" \
                           f"📝 Full Name: {user_registration[telegram_username]['full_name']}\n" \
                           f"📧 Email: {user_registration[telegram_username]['email']}\n"

    keyboard = InlineKeyboardMarkup()
    keyboard.row(
        InlineKeyboardButton("✅ Confirm & Save", callback_data="confirm_registration"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_registration")
    )

    bot.send_message(message.chat.id, registration_details, parse_mode="Markdown", reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data in ["confirm_registration", "cancel_registration"])
def handle_registration_confirmation(call):
    telegram_username = call.from_user.username

    if telegram_username not in user_registration:
        bot.answer_callback_query(call.id, "Registration session expired. Please start again.")
        return

    if call.data == "confirm_registration":
        user_data = {
            "username": telegram_username,
            "user_id": user_registration[telegram_username]["user_id"],
            "full_name": user_registration[telegram_username]["full_name"],
            "email": user_registration[telegram_username]["email"],
            "registration_date": datetime.datetime.now().isoformat(),
            "registered": True
        }

        user_ref = db.reference(f'Members/{telegram_username}')
        user_ref.set(user_data)

        bot.send_message(call.message.chat.id, f"✅ Registration successful, {user_data['full_name']}!\n\n"
                                               f" welcome {user_data['email']}.\n"
                                               f"You can now use the bot services.")
        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_message(ADMIN_CHAT_ID, f"✅ 🚀 New user {user_data['username \n registered']} has registered \n")

        # Show buy/sell buttons
        show_buy_sell_buttons(call.message.chat.id)

    else:
        bot.send_message(call.message.chat.id, "❌ Registration cancelled. Use /register to start again when you're ready.")

    # Clear registration data
    if telegram_username in user_registration:
        del user_registration[telegram_username]

    bot.answer_callback_query(call.id)

@bot.message_handler(commands=['login'])
@error_handler
def login_user(message):
    telegram_username = message.from_user.username

    if not telegram_username:
        bot.reply_to(message, "❌ You need a Telegram username to register. Please go to your Telegram >> Profile and set a User Name.")
        return

    user_ref = db.reference(f'Members/{telegram_username}')
    user_data = user_ref.get()

    # Ensure `user_data` is a dictionary
    if not isinstance(user_data, dict):  
        bot.reply_to(message, "⚠️ Error retrieving your account. \n\n Please Register to use this service or \n contact support rehobotics.technologies@gmail.com \n  or on Telegram @CryptoNairaExchangeSupport \n if your are already registered and having issues \n accessing the service. /register ")
        return

    full_name = user_data.get("full_name", "Unknown")

    warning_text = ( "⚠️ *SCAM ALERT!* ⚠️\n\n"
                     "🚨 *No transaction outside this bot is permitted or authorized.*\n"
                     "🚫 *Admin will NEVER call or message you for transactions outside this bot.*\n"
                     "❌ *Anyone who falls victim to scammers does so at their own risk. The admin will not be held responsible.*\n"
                     "✅ *Always ensure your transactions are done within this bot for safety.*")

    bot.send_message(message.chat.id, warning_text, parse_mode="Markdown")
    bot.reply_to(message, f"🔑 Welcome back, {full_name}! You are now logged in.")

    show_buy_sell_buttons(message.chat.id)

# Display buy/sell buttons
def show_buy_sell_buttons(user_id):
    keyboard = InlineKeyboardMarkup()
    buy_button = InlineKeyboardButton("💰 Buy USDT", callback_data="buy_usdt")
    sell_button = InlineKeyboardButton("💵 Sell USDT", callback_data="sell_usdt")
    keyboard.row(buy_button, sell_button)
    bot.send_message(user_id, "What would you like to do?", reply_markup=keyboard)

# Rate command handler
@bot.message_handler(commands=['rate'])
@error_handler
def rate_command(message):
    buy_rate = get_exchange_rate("buy")
    sell_rate = get_exchange_rate("sell")
    bot.send_message(message.chat.id, 
                    f"Current Exchange Rates:\n\n"
                    f"Buy: 1 USDT = ₦{buy_rate}\n"
                    f"Sell: 1 USDT = ₦{sell_rate}")

# Buy/Sell selection handler
@bot.callback_query_handler(func=lambda call: call.data in ["buy_usdt", "sell_usdt"])
def handle_buy_sell(call):
    telegram_username = str(call.from_user.id)
    action = "Buy" if call.data == "buy_usdt" else "Sell"

    # Generate transaction ID
    transaction_id = generate_transaction_id()

    # Initialize transaction tracking for this user
    transactions[telegram_username] = {
        "transaction_id": transaction_id,
        "step": 1, 
        "action": action,
        "start_time": datetime.datetime.now().isoformat()
    }

    # Log the initialized transaction
    log_transaction(telegram_username, transactions[telegram_username])

    # Acknowledge the callback query
    bot.answer_callback_query(call.id)

    # Send initial timer message and store its message_id
    try:
        timer_message = bot.send_message(telegram_username, "⏳ Initializing timer...")
        transactions[telegram_username]["timer_message_id"] = timer_message.message_id

        # Start countdown timer
        start_countdown_timer(telegram_username)

        # Ask for amount
        bot.send_message(telegram_username, f"🎉 WOW, That's Awesome \n\n"
                        f"💰 You chose to {action} USDT.\n\nEnter the amount:")
    except Exception as e:
        logger.error(f"Error in buy/sell handler: {e}")

@bot.message_handler(func=lambda message: str(message.from_user.id) in transactions and transactions[str(message.from_user.id)].get("step") == 1)
@error_handler
def amount_input(message):
    user_id = str(message.from_user.id)

    try:
        amount = float(message.text)
        user_data = transactions.get(user_id, {})

        if not user_data:
            bot.send_message(user_id, "❌ Transaction not found. Please restart the process.")
            return

        action = user_data.get("action", "")

        # Convert action to lowercase for consistency
        action_lower = action.lower()

        rate = get_exchange_rate(action_lower)
        naira_amount = amount * rate

        transactions[user_id]["amount"] = amount
        transactions[user_id]["naira_amount"] = naira_amount
        transactions[user_id]["step"] = 2

        if action == "Buy":
            keyboard = InlineKeyboardMarkup()
            decline_button = InlineKeyboardButton("❌ Decline / Go to Sell USDT", callback_data="sell_usdt")
            keyboard.row(decline_button)
            bot.send_message(user_id, f"✅ Exchange Rate: ₦{rate}/USDT\n"
                                     f"💵 You will pay: ₦{naira_amount:.2f}\n\n"
                                     f"🔹 Transfer the amount to:\n{ADMIN_ACCOUNT_DETAILS}\n\n"
                                     f"Make your transfer into the Naira account provided \n"
                                     f"📎 Then Upload proof of payment after transfer.", reply_markup=keyboard)

        else:  # Selling case
            keyboard = InlineKeyboardMarkup()
            confirm_button = InlineKeyboardButton("✅ Confirm", callback_data="confirm_sell")
            cancel_button = InlineKeyboardButton("❌ Cancel", callback_data="cancel_transaction")
            keyboard.row(confirm_button, cancel_button)
            bot.send_message(user_id, f"✅ Exchange Rate: ₦{rate}/USDT\n"
                                     f"💰 You will receive: ₦{naira_amount:.2f}\n\n"
                                     f"⚠️ Are you sure you want to proceed?", reply_markup=keyboard)

    except ValueError:
        bot.reply_to(message, "❌ Invalid amount. Please enter a numeric value.")


# Photo upload handler for receipts
@bot.message_handler(content_types=['photo'])
@error_handler
def handle_receipt_upload(message):
    user_id = str(message.from_user.id)

    if user_id not in transactions:
        return

    user_data = transactions[user_id]

    # Handle Buy USDT receipt upload
    if user_data.get("step") == 2 and user_data.get("action") == "Buy":
        transactions[user_id]["step"] = 3
        transactions[user_id]["receipt"] = message.photo[-1].file_id

        keyboard = InlineKeyboardMarkup()
        approve_button = InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}")
        reject_button = InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
        pending_button = InlineKeyboardButton("⏳ Pending", callback_data=f"pending_{user_id}")
        keyboard.row(approve_button, reject_button, pending_button)

        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_photo(ADMIN_CHAT_ID, transactions[user_id]["receipt"], 
                          caption=f"📥 Payment proof received from {user_id}.\n to buy USDT"
                                  f"💵 Amount: ₦{transactions[user_id]['naira_amount']:.2f}\n"
                                  f"💰 USDT Amount: {transactions[user_id]['amount']}\n"
                                  f"🔍 Please verify and confirm.", 
                          reply_markup=keyboard)

        bot.send_message(user_id, "✅ Receipt uploaded successfully. Awaiting admin confirmation.")

    # Handle Sell USDT transaction proof upload
    elif user_data.get("step") == 8 and user_data.get("action") == "Sell":
        transactions[user_id]["step"] = 9
        transactions[user_id]["transaction_proof"] = message.photo[-1].file_id

        keyboard = InlineKeyboardMarkup()
        confirm_button = InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{user_id}")
        reject_button = InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}")
        pending_button = InlineKeyboardButton("⏳ Pending", callback_data=f"pending_{user_id}")
        keyboard.row(confirm_button, reject_button, pending_button)

        # Send to admin for verification
        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_photo(ADMIN_CHAT_ID, message.photo[-1].file_id,
                          caption=f"📥 USDT transfer proof from user {user_id}\n to sell USDT"
                                  f"💰 Amount: {transactions[user_id]['amount']} USDT\n"
                                  f"🔹 Network: {transactions[user_id].get('network', 'Unknown')}\n"
                                  f"🔍 Please verify and confirm.",
                          reply_markup=keyboard)

        bot.send_message(user_id, "✅ Proof received. Awaiting admin confirmation.")

# Admin response handler for receipt verification
@bot.callback_query_handler(func=lambda call: call.data.startswith(("approve_", "reject_", "pending_")))
@error_handler
def handle_admin_response(call):
    action, user_id = call.data.split("_")

    if user_id in transactions:
        if action == "approve":
            transactions[user_id]["step"] = 4
            bot.send_message(user_id, "✅ Payment confirmed!\n\n"
                                     "📌 Provide your wallet address for USDT transfer.")
            # Update transaction log
            log_transaction(user_id, transactions[user_id])

            bot.answer_callback_query(call.id, "Payment approved")

        elif action == "reject":
            transactions[user_id]["step"] = 0
            bot.send_message(user_id, "❌ Your proof of payment uploaded has been rejected. \n This could either be one or more reasons such as \n\n 1. Wrong upload: Please check to ascertain that your upload is correct\n 2. Un-clear (blur) upload: please re-upload a clearer image for verification \n \n However, If you think this is NOT right, Please contact support rehobotics.technologies@gmail.com or on Telegram @CryptoNairaExchangeSupport ")
            bot.answer_callback_query(call.id, "Payment rejected")

        elif action == "pending":
            bot.send_message(user_id, "⏳ Your payment is under review. \n Exchange Network transfer is yet to reflect. \n This could either be due to \n\n 1: Poor Internet Network connection \n 2: Due to inter-Bank transfer \n Please exercise patients")
            bot.answer_callback_query(call.id, "Status set to pending")

# Wallet address handler for Buy USDT
@bot.message_handler(func=lambda message: str(message.from_user.id) in transactions and transactions[str(message.from_user.id)].get("step") == 4 and transactions[str(message.from_user.id)].get("action") == "Buy")
def handle_wallet_address(message):
    user_id = str(message.from_user.id)
    transactions[user_id]["wallet_address"] = message.text

    keyboard = InlineKeyboardMarkup()
    trc20_button = InlineKeyboardButton("🔹 TRC20", callback_data="wallet_TRC20")
    bep20_button = InlineKeyboardButton("🔹 BEP20", callback_data="wallet_BEP20")
    keyboard.row(trc20_button, bep20_button)

    bot.send_message(user_id, "✅ Choose the USDT network:", reply_markup=keyboard)

# Network selection handler for Buy USDT
@bot.callback_query_handler(func=lambda call: call.data.startswith("wallet_"))
def handle_wallet_network(call):
    user_id = str(call.from_user.id)
    network = call.data.split("_")[1]

    if user_id in transactions and transactions[user_id].get("step") == 4 and transactions[user_id].get("action") == "Buy":
        transactions[user_id]["step"] = 5
        transactions[user_id]["network"] = network

        # Notify the user
        bot.send_message(user_id, f"✅ You selected *{network}* network.\n\n"
                                 f"📩 Please wait while the USDT transfer is done into your Wallet Address:\n\n"
                                 f"🔹 Address: {transactions[user_id]['wallet_address']}\n",
                         parse_mode="Markdown")

        # Notify Admin to confirm the transfer
        keyboard = InlineKeyboardMarkup()
        transfer_done_button = InlineKeyboardButton("✅ Transfer Done", callback_data=f"transfer_done_{user_id}")
        keyboard.row(transfer_done_button)

        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_message(ADMIN_CHAT_ID, f"📌 User {user_id} provided wallet details:\n"
                                           f"🔹 Address: {transactions[user_id]['wallet_address']}\n"
                                           f"🔹 Network: {network}\n"
                                           f"💰 Amount: {transactions[user_id]['amount']} USDT\n"
                                           f"📌 Proceed with USDT transfer and click below when done.",
                            reply_markup=keyboard)

            bot.send_message(ADMIN_CHAT_ID, f" \n {transactions[user_id]['wallet_address']}\n")

        bot.send_message(user_id, "⏳ Awaiting USDT transfer confirmation from the admin.")

        # Clear the callback query
        bot.answer_callback_query(call.id)

# Admin transfer done handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("transfer_done_"))
@error_handler
def handle_admin_transfer_done(call):
    user_id = call.data.split("_")[2]

    if user_id in transactions and transactions[user_id].get("step") == 5:
        transactions[user_id]["step"] = 6

        # Update transaction log
        log_transaction(user_id, transactions[user_id])

        # Ask the user to confirm receipt
        keyboard = InlineKeyboardMarkup()
        confirm_button = InlineKeyboardButton("✅ Confirm Received", callback_data="confirm_received")
        not_received_button = InlineKeyboardButton("❌ Not Received", callback_data="not_received")
        keyboard.row(confirm_button, not_received_button)

        bot.send_message(user_id, "✅ The admin has confirmed the USDT transfer.\n\n"
                                 "📌 Please confirm if you have received it.", reply_markup=keyboard)

        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_message(ADMIN_CHAT_ID, f"✅ You have confirmed the transfer for user {user_id}.\n\n"
                                           "Waiting for the user to acknowledge receipt.")

        # Clear the callback query
        bot.answer_callback_query(call.id)

# User receipt confirmation handler
@bot.callback_query_handler(func=lambda call: call.data in ["confirm_received", "not_received"])
def handle_transaction_end(call):
    user_id = str(call.from_user.id)

    if user_id in transactions:
        if call.data == "confirm_received":
            if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
                bot.send_message(ADMIN_CHAT_ID, f"✅ User {user_id} has confirmed receipt of {transactions[user_id]['amount']} USDT.")

            bot.send_message(user_id, "✅ Transaction completed successfully!\n\n"
                                     "Would you like to start another transaction?")

            # Update transaction log
            log_transaction(user_id, transactions[user_id])

            keyboard = InlineKeyboardMarkup()
            new_transaction_buy = InlineKeyboardButton("💰 Buy USDT", callback_data="buy_usdt")
            new_transaction_sell = InlineKeyboardButton("💵 Sell USDT", callback_data="sell_usdt")
            exit_button = InlineKeyboardButton("🚪 Exit", callback_data="exit")
            keyboard.row(new_transaction_buy, new_transaction_sell)
            keyboard.row(exit_button)

            bot.send_message(user_id, "Select an option:", reply_markup=keyboard)

        elif call.data == "not_received":
            if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
                bot.send_message(ADMIN_CHAT_ID, f"⚠️ User {user_id} reported NOT receiving USDT transfer.\n"
                                              f"Please verify and resolve the issue.")
            bot.send_message(user_id, "\n⚠️  We apologise for any delay as this could either be \n due to Poor Internet Network connection or due to inter-Bank transfer \n \n Please exercise patients and wait for some minutes for the transaction to reflect, then Click *CONFIRM RECEIVED* above \n Or you can contact admin: rehobotics.technologies@gmail.com or on Telegram @CryptoNairaExchangeSupport")

        # Clear the callback query
        bot.answer_callback_query(call.id)

# SELL USDT FLOW

# Sell confirmation handler
@bot.callback_query_handler(func=lambda call: call.data in ["confirm_sell", "cancel_transaction"])
@error_handler
def handle_sell_confirmation(call):
    telegram_username = str(call.from_user.id)

    if call.data == "confirm_sell":
        # Make sure we maintain the existing transaction data
        if telegram_username in transactions:
            current_data = transactions[telegram_username]
            current_data["step"] = 7
            transactions[telegram_username] = current_data
        else:
            transactions[telegram_username] = {"step": 7, "action": "Sell"}

            # Update transaction log
            log_transaction(telegram_username, transactions[telegram_username])

        keyboard = InlineKeyboardMarkup()
        keyboard.row(InlineKeyboardButton("TRC20", callback_data="network_TRC20"),
                     InlineKeyboardButton("BEP20", callback_data="network_BEP20"))
        bot.send_message(telegram_username, "📌 Please select the **network** for your USDT transfer:", reply_markup=keyboard)
    else:
        bot.send_message(telegram_username, "❌ Transaction has been canceled. \n I am sorry to see that you cancelled the transaction. \n Hope you use my service again?")
        # Clean up the transaction data
        if telegram_username in transactions:
            transactions.pop(telegram_username)

    # Clear the callback query
    bot.answer_callback_query(call.id)

# Network selection handler for Sell USDT
@bot.callback_query_handler(func=lambda call: call.data.startswith("network_"))
def handle_network_selection(call):
    user_id = str(call.from_user.id)
    network = call.data.split("_")[1]

    if user_id in transactions and transactions[user_id].get("step") == 7:
        wallet_address = COMPANY_WALLETS.get(network)

        if wallet_address:
            transactions[user_id].update({
                "network": network, 
                "company_wallet": wallet_address, 
                "step": 8
            })

            bot.send_message(user_id, f"✅ Please upload a 'clear/readable' screenshot of the transaction as proof here.\n\n"
                                     f"Pay this Amount: {transactions[user_id]['amount']} USDT\n"
                                     f"🔹 Network: {network}\n\n"
                                     f" Pay {transactions[user_id]['amount']} USDT into the below Wallet Address\n For ease, just copy the below Wallet address")

            bot.send_message(user_id, f"\n {wallet_address}\n")
        else:
            bot.send_message(user_id, "\n ⚠️ Oh! Gosh! you have entered or selected an Invalid Network")

    # Clear the callback query
    bot.answer_callback_query(call.id)

# Admin confirm USDT transfer for Sell USDT
@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_"))
@error_handler
def admin_confirm_transaction(call):
    telegram_username = call.data.split("_")[1]

    if telegram_username in transactions and transactions[telegram_username].get("step") == 9:
        transactions[telegram_username]["step"] = 10

        # Update transaction log
        log_transaction(telegram_username, transactions[telegram_username])

        bot.send_message(telegram_username, "✅ Transaction confirmed. Please provide your Naira bank details in this format:\n\n"
                                 "Bank Name\n"
                                 "Account Number\n"
                                 "Account Name")

    # Clear the callback query
    bot.answer_callback_query(call.id)

# Bank details handler for Sell USDT
@bot.message_handler(func=lambda msg: str(msg.from_user.id) in transactions and transactions[str(msg.from_user.id)].get("step") == 10)
def handle_bank_details(message):
    user_id = str(message.from_user.id)
    bank_info = message.text.split('\n')

    if len(bank_info) < 3:
        bot.send_message(user_id, "❌ Please provide your bank details in the correct format:\n\n"
                                 "Bank Name\n"
                                 "Account Number\n"
                                 "Account Name")
        return

    transactions[user_id]["bank_details"] = message.text
    transactions[user_id]["step"] = 11

    # Create a keyboard for admin
    keyboard = InlineKeyboardMarkup()
    transfer_done_button = InlineKeyboardButton("✅ Transfer Done", callback_data=f"naira_sent_{user_id}")
    keyboard.row(transfer_done_button)

    # Notify admin with bank details
    if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
        bot.send_message(ADMIN_CHAT_ID, 
                        f"🔹 User ID: {user_id} provided bank details:\n"
                        f"{message.text}\n\n"
                        f"💲 USDT Amount: {transactions[user_id]['amount']}\n"
                        f"💵 Naira Amount: ₦{transactions[user_id]['naira_amount']:.2f}\n\n"
                        f"✅ Click 'Transfer Done' after transferring Naira equivalent.",
                        reply_markup=keyboard)

    bot.send_message(user_id, "✅ Bank details received. Waiting for admin to process your payment.")

# Admin confirms Naira transfer for Sell USDT
@bot.callback_query_handler(func=lambda call: call.data.startswith("naira_sent_"))
@error_handler
def admin_naira_transfer_done(call):
    user_id = call.data.split("_")[2]

    if user_id in transactions and transactions[user_id].get("step") == 11:
        transactions[user_id]["step"] = 12

        # Update transaction log
        log_transaction(user_id, transactions[user_id])

        keyboard = InlineKeyboardMarkup()
        received_button = InlineKeyboardButton("✅ Received", callback_data=f"received_{user_id}")
        not_received_button = InlineKeyboardButton("❌ Not Received", callback_data=f"not_received_{user_id}")
        keyboard.row(received_button, not_received_button)

        bot.send_message(user_id, 
                        "✅ The admin has confirmed the Naira transfer to your bank account.\n\n"
                        "Please verify you received the funds and confirm below:",
                        reply_markup=keyboard)
    else:
        if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
            bot.send_message(ADMIN_CHAT_ID, f"❌ Error: Transaction data not found for user {user_id}")

    # Clear the callback query
    bot.answer_callback_query(call.id)

# User confirms receipt of Naira for Sell USDT
@bot.callback_query_handler(func=lambda call: call.data.startswith(("received_", "not_received_")))
def handle_naira_receipt_confirmation(call):
    action = call.data.split("_")[0]
    telegram_username = call.data.split("_")[1]

    if telegram_username in transactions and transactions[telegram_username].get("step") == 12:
        if action == "received":
            if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
                bot.send_message(ADMIN_CHAT_ID, f"✅ User {telegram_username} has confirmed receipt of ₦{transactions[telegram_username]['naira_amount']:.2f}")

            bot.send_message(telegram_username, "🎉 Thank you for confirming! Transaction completed successfully.")

            # Offer new transaction
            keyboard = InlineKeyboardMarkup()
            buy_button = InlineKeyboardButton("💰 Buy USDT", callback_data="buy_usdt")
            sell_button = InlineKeyboardButton("💵 Sell USDT", callback_data="sell_usdt")
            exit_button = InlineKeyboardButton("🚪 Exit", callback_data="exit")
            keyboard.row(buy_button, sell_button)
            keyboard.row(exit_button)

            bot.send_message(telegram_username, "Would you like to start another transaction?", reply_markup=keyboard)

            # Clear transaction data
            transactions.pop(telegram_username, None)

        elif action == "not_received":
            if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
                bot.send_message(ADMIN_CHAT_ID, 
                               f"⚠️ User {telegram_username} reported NOT receiving their Naira payment of ₦{transactions[telegram_username]['naira_amount']:.2f}.\n"
                               f"Please investigate and resolve this issue.")

                # Create pending notification button
                keyboard = InlineKeyboardMarkup()
                pending_button = InlineKeyboardButton("⏳ Notify User of Pending Status", callback_data=f"pending_payment_{telegram_username}")
                keyboard.row(pending_button)

                bot.send_message(ADMIN_CHAT_ID, "You can notify the user of a pending status:", reply_markup=keyboard)

            bot.send_message(telegram_username, "⚠️ Your issue has been reported to the admin or you can chat up support here on Telegram @CryptoNairaExchangeSupport. They will contact you shortly.")

    # Clear the callback query
    bot.answer_callback_query(call.id)

# Exit callback handler
@bot.callback_query_handler(func=lambda call: call.data == "exit")
@error_handler
def handle_exit(call):
    telegram_username = str(call.from_user.id)

    # Clear any transaction data
    if telegram_username in transactions:
        transactions.pop(telegram_username)

    bot.send_message(telegram_username, "👋 Thank you for using our service. Have a great day!")
    bot.answer_callback_query(call.id)

# Pending payment notification handler
@bot.callback_query_handler(func=lambda call: call.data.startswith("pending_payment_"))
def handle_pending_payment(call):
    user_id = call.data.split("_")[-1]  # Extract user ID from callback data

    # Notify the user with a persuasive message
    bot.send_message(user_id, "⏳ **Payment has already been processed!**\n\n"
                             "💡 *Please exercise patience.*\n"
                             "Bank network delays or inter-banking processes might cause slight delays.\n\n"
                             "We assure you that your funds are on the way. Kindly hold on while the transfer is completed. ✅")

    # Notify the admin that the user has been informed
    if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
        bot.send_message(ADMIN_CHAT_ID, f"⏳ User {user_id} has been informed to wait due to possible bank network delays.")

    # Clear the callback query
    bot.answer_callback_query(call.id)

# Handle all other messages
@bot.message_handler(func=lambda message: True)
@error_handler
def handle_all_messages(message):
    user_id = str(message.from_user.id)

    # Check if user is in a transaction
    if user_id in transactions:
        bot.send_message(user_id, "Please complete your current transaction first.")
    else:
        bot.send_message(user_id, "Welcome! Please use the buttons below to start a transaction:")
        show_buy_sell_buttons(user_id)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_transaction")
def cancel_transaction(call):
    telegram_username = str(call.from_user.id)

    if telegram_username in transactions:
        transactions[telegram_username]["status"] = "cancelled"

        # Update transaction log
        log_transaction(telegram_username, transactions[telegram_username])

        logout_user(telegram_username)
        bot.send_message(telegram_username, "❌ Transaction cancelled. \n I am sorry to see that you cancelled the transaction. \n Hope you use my service again?")
    else:
        bot.send_message(telegram_username, "❌ No active transaction found.")

# Start bot polling with proper error handling
if __name__ == "__main__":
    try:
        logger.info("🤖 Bot is starting...")
        # Send an initial message to admin to confirm bot is up
        try:
            if ADMIN_CHAT_ID and isinstance(ADMIN_CHAT_ID, int):
                bot.send_message(ADMIN_CHAT_ID, "🚀 Bot has been started and is now online!")
        except Exception as e:
            logger.error(f"Failed to send startup message to admin: {e}")
        
        # Start polling with better error handling
        logger.info("🤖 Bot is running... Press Ctrl+C to stop.")
        bot.infinity_polling(timeout=60, long_polling_timeout=30)
    except KeyboardInterrupt:
        logger.info("\n🛑 Bot stopped by admin.")
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        logger.critical(traceback.format_exc())
