import os
import logging
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

from database.db import (
    init_db,
    add_or_get_user,
    add_expense,
    get_user_balance,
    get_all_expenses,
    set_partner_by_username,
    get_partner_id,
    get_username,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize database
init_db()

# Conversation states
ADD_EXPENSE, AMOUNT, DESCRIPTION, CATEGORY, SPLIT_USERS = range(5)

def main_menu_markup():
    keyboard = [
        ['➕ Add Expense', '💰 My Balance'],
        ['📊 View Expenses', '🔗 Set Partner'],
        ['❓ Help']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    user = update.effective_user
    add_or_get_user(user.id, user.username or f"user_{user.id}")

    reply_markup = main_menu_markup()

    await update.message.reply_text(
        f"Hi {user.first_name}! 👋\n\n"
        "I'm your expense tracker bot. I help you and your girlfriend track shared expenses.\n\n"
        "Use the buttons below to get started!",
        reply_markup=reply_markup
    )

async def set_partner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Link current user with their partner by username."""
    user = update.effective_user
    add_or_get_user(user.id, user.username or f"user_{user.id}")

    if not context.args:
        await update.message.reply_text(
            "Use: /partner @username\n"
            "Example: /partner @jane"
        )
        return

    partner_raw = context.args[0]
    ok, result = set_partner_by_username(user.id, partner_raw)
    if not ok:
        await update.message.reply_text(result)
        return

    await update.message.reply_text(
        f"Partner linked with @{result}.\n"
        "You can now see each other's expenses and shared items split 50/50."
    )

# Add expense flow
async def add_expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the add expense conversation."""
    await update.message.reply_text(
        "How much did you spend? (Enter amount in numbers, e.g., 25.50)"
    )
    return AMOUNT

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the amount input."""
    try:
        amount = float(update.message.text)
        if amount <= 0:
            await update.message.reply_text("Amount must be greater than 0. Please try again.")
            return AMOUNT

        context.user_data['amount'] = amount
        await update.message.reply_text("What did you spend it on? (description)")
        return DESCRIPTION
    except ValueError:
        await update.message.reply_text("Invalid amount. Please enter a number (e.g., 25.50)")
        return AMOUNT

async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the description input."""
    context.user_data['description'] = update.message.text

    categories = ['Food', 'Transport', 'Entertainment', 'Utilities', 'Other']
    keyboard = [[cat] for cat in categories]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Select a category:",
        reply_markup=reply_markup
    )
    return CATEGORY

async def category_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the category selection."""
    context.user_data['category'] = update.message.text

    keyboard = [['Shared (split 50/50)', 'Personal']]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Is this a shared expense?",
        reply_markup=reply_markup
    )
    return SPLIT_USERS

async def split_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the split decision."""
    user = update.effective_user
    amount = context.user_data['amount']
    description = context.user_data['description']
    category = context.user_data['category']

    is_shared = update.message.text == 'Shared (split 50/50)'

    if is_shared and get_partner_id(user.id) is None:
        await update.message.reply_text(
            "Set your partner first so shared expenses can split automatically.\n"
            "Use: /partner @username",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

    # Record the expense
    expense_id = add_expense(
        user.id,
        amount,
        description,
        category,
        is_shared=is_shared,
        paid_by=user.username or f"user_{user.id}"
    )

    message = f"✅ Expense recorded!\n\n"
    message += f"Amount: ${amount:.2f}\n"
    message += f"Description: {description}\n"
    message += f"Category: {category}\n"
    message += f"Type: {'Shared' if is_shared else 'Personal'}"

    if is_shared:
        half = amount / 2.0
        message += f"\nSplit: ${half:.2f} added to each account"

    reply_markup = main_menu_markup()

    await update.message.reply_text(message, reply_markup=reply_markup)

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation."""
    reply_markup = main_menu_markup()

    await update.message.reply_text("Cancelled.", reply_markup=reply_markup)
    return ConversationHandler.END

# View balance
async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user's current balance."""
    user = update.effective_user
    balance = get_user_balance(user.id)

    message = "💰 Your Account\n\n"
    message += f"Personal spending: ${balance['personal_total']:.2f}\n"
    message += f"Your shared paid: ${balance['shared_paid']:.2f}\n"
    message += f"Your shared half: ${balance['shared_owed']:.2f}\n"
    message += f"Your total account: ${balance['overall_total']:.2f}\n\n"

    if balance['partner_username']:
        message += f"Partner: @{balance['partner_username']}\n"
        message += f"Shared settle net: ${balance['shared_balance']:.2f}\n"
        if balance['shared_balance'] > 0:
            message += "Your partner owes you for shared spending."
        elif balance['shared_balance'] < 0:
            message += "You owe your partner for shared spending."
        else:
            message += "Shared spending is currently even."
    else:
        message += "No partner linked yet. Use /partner @username"

    await update.message.reply_text(message)

# View expenses
async def view_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all expenses for the user and partner if linked."""
    user = update.effective_user
    expenses = get_all_expenses(user.id)

    if not expenses:
        await update.message.reply_text("No expenses recorded yet.")
        return

    partner_id = get_partner_id(user.id)
    partner_name = get_username(partner_id) if partner_id else None

    message = "📊 Couple Expenses\n\n" if partner_id else "📊 Your Expenses\n\n"
    for expense in expenses[:10]:  # Show last 10
        owner = expense['paid_by'] or f"user_{expense['user_id']}"
        scope = 'Shared' if expense['is_shared'] else 'Personal'
        message += f"• ${expense['amount']:.2f} - {expense['description']} ({expense['category']})\n"
        message += f"  paid by @{owner} | {scope}\n"
        message += f"  on {expense['created_at']}\n\n"

    if partner_name:
        message += f"Linked with @{partner_name}"

    await update.message.reply_text(message)

# Help command
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = """
🤖 Expense Tracker Bot Help

Commands:
/start - Start the bot and see the menu
/help - Show this help message
/balance - Check your current balance
/expenses - View your recent expenses
/partner @username - Link your partner account

Buttons:
➕ Add Expense - Record a new expense
💰 My Balance - Check your balance
📊 View Expenses - See recent expenses
❓ Help - Show this help message

How it works:
1. Each person keeps personal expenses in their own account
2. Link accounts once with /partner @username
3. Shared expenses are automatically split 50/50 for both accounts
4. Both partners can see each other's spending history

For questions, contact the bot admin.
    """
    await update.message.reply_text(help_text)

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors."""
    logger.error(f"Update {update} caused error {context.error}")

# Main function
def main():
    """Start the bot."""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")

    app = Application.builder().token(token).build()

    # Conversation handler for adding expenses
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^➕ Add Expense$'), add_expense_start),
            CommandHandler('add', add_expense_start)
        ],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, category_received)],
            SPLIT_USERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", view_balance))
    app.add_handler(CommandHandler("expenses", view_expenses))
    app.add_handler(CommandHandler("partner", set_partner_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.Regex('^💰 My Balance$'), view_balance))
    app.add_handler(MessageHandler(filters.Regex('^📊 View Expenses$'), view_expenses))
    app.add_handler(MessageHandler(filters.Regex('^🔗 Set Partner$'), set_partner_command))
    app.add_handler(MessageHandler(filters.Regex('^❓ Help$'), help_command))

    app.add_error_handler(error_handler)

    # Start the bot
    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
