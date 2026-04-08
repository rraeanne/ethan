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
    get_user_expenses,
    delete_expense,
    update_expense,
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
DELETE_SELECT = 5
EDIT_SELECT, EDIT_FIELD, EDIT_VALUE = 6, 7, 8

CATEGORIES = ['Food', 'Transport', 'Entertainment', 'Utilities', 'Other']

def main_menu_markup():
    keyboard = [
        ['➕ Add Expense', '💰 My Balance'],
        ['📊 View Expenses', '🔗 Set Partner'],
        ['✏️ Edit Expense', '🗑 Delete Expense'],
        ['❓ Help']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def format_expense_list(expenses):
    """Format a numbered list of expenses for display."""
    lines = []
    for i, e in enumerate(expenses, 1):
        scope = 'Shared' if e['is_shared'] else 'Personal'
        lines.append(
            f"{i}. ${e['amount']:.2f} — {e['description']} ({e['category']}, {scope})\n"
            f"   {e['created_at'][:10]}"
        )
    return '\n\n'.join(lines)

# ── Start ──────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_get_user(user.id, user.username or f"user_{user.id}")
    await update.message.reply_text(
        f"Hi {user.first_name}! 👋\n\n"
        "I'm your expense tracker bot. I help you and your partner track shared expenses.\n\n"
        "Use the buttons below to get started!",
        reply_markup=main_menu_markup()
    )

# ── Partner ────────────────────────────────────────────────────────────────────

async def set_partner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_or_get_user(user.id, user.username or f"user_{user.id}")

    if not context.args:
        await update.message.reply_text(
            "Use: /partner @username\nExample: /partner @jane"
        )
        return

    ok, result = set_partner_by_username(user.id, context.args[0])
    if not ok:
        await update.message.reply_text(result)
        return

    await update.message.reply_text(
        f"Partner linked with @{result}.\n"
        "You can now see each other's expenses and shared items split 50/50."
    )

# ── Add Expense ────────────────────────────────────────────────────────────────

async def add_expense_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "How much did you spend? (e.g. 25.50)"
    )
    return AMOUNT

async def amount_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text)
        if amount <= 0:
            await update.message.reply_text("Amount must be greater than 0. Try again.")
            return AMOUNT
        context.user_data['amount'] = amount
        await update.message.reply_text("What did you spend it on? (description)")
        return DESCRIPTION
    except ValueError:
        await update.message.reply_text("Invalid amount. Enter a number (e.g. 25.50)")
        return AMOUNT

async def description_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['description'] = update.message.text
    keyboard = [[cat] for cat in CATEGORIES]
    await update.message.reply_text(
        "Select a category:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return CATEGORY

async def category_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['category'] = update.message.text
    keyboard = [['Shared (split 50/50)', 'Personal']]
    await update.message.reply_text(
        "Is this a shared expense?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return SPLIT_USERS

async def split_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    amount = context.user_data['amount']
    description = context.user_data['description']
    category = context.user_data['category']
    is_shared = update.message.text == 'Shared (split 50/50)'

    if is_shared and get_partner_id(user.id) is None:
        await update.message.reply_text(
            "Set your partner first.\nUse: /partner @username",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

    add_expense(
        user.id, amount, description, category,
        is_shared=is_shared,
        paid_by=user.username or f"user_{user.id}"
    )

    msg = f"✅ Expense recorded!\n\nAmount: ${amount:.2f}\nDescription: {description}\nCategory: {category}\nType: {'Shared' if is_shared else 'Personal'}"
    if is_shared:
        msg += f"\nSplit: ${amount/2:.2f} added to each account"

    await update.message.reply_text(msg, reply_markup=main_menu_markup())
    return ConversationHandler.END

# ── Delete Expense ─────────────────────────────────────────────────────────────

async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    expenses = get_user_expenses(user.id)

    if not expenses:
        await update.message.reply_text(
            "You have no recorded expenses to delete.",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

    context.user_data['delete_expenses'] = [dict(e) for e in expenses]
    listing = format_expense_list(expenses)
    await update.message.reply_text(
        f"Your recent expenses:\n\n{listing}\n\nReply with the number of the expense to delete, or /cancel to go back."
    )
    return DELETE_SELECT

async def delete_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expenses = context.user_data.get('delete_expenses', [])
    try:
        idx = int(update.message.text.strip()) - 1
        if idx < 0 or idx >= len(expenses):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"Please enter a number between 1 and {len(expenses)}.")
        return DELETE_SELECT

    expense = expenses[idx]
    user = update.effective_user
    deleted = delete_expense(expense['id'], user.id)

    if deleted:
        await update.message.reply_text(
            f"🗑 Deleted: ${expense['amount']:.2f} — {expense['description']} ({expense['category']})",
            reply_markup=main_menu_markup()
        )
    else:
        await update.message.reply_text(
            "Could not delete that expense. It may have already been removed.",
            reply_markup=main_menu_markup()
        )
    return ConversationHandler.END

# ── Edit Expense ───────────────────────────────────────────────────────────────

async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    expenses = get_user_expenses(user.id)

    if not expenses:
        await update.message.reply_text(
            "You have no recorded expenses to edit.",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

    context.user_data['edit_expenses'] = [dict(e) for e in expenses]
    listing = format_expense_list(expenses)
    await update.message.reply_text(
        f"Your recent expenses:\n\n{listing}\n\nReply with the number of the expense to edit, or /cancel to go back."
    )
    return EDIT_SELECT

async def edit_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expenses = context.user_data.get('edit_expenses', [])
    try:
        idx = int(update.message.text.strip()) - 1
        if idx < 0 or idx >= len(expenses):
            raise ValueError
    except ValueError:
        await update.message.reply_text(f"Please enter a number between 1 and {len(expenses)}.")
        return EDIT_SELECT

    context.user_data['edit_target'] = expenses[idx]
    e = expenses[idx]
    keyboard = [['Amount', 'Description', 'Category']]
    await update.message.reply_text(
        f"Editing: ${e['amount']:.2f} — {e['description']} ({e['category']})\n\nWhat would you like to change?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return EDIT_FIELD

async def edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    field = update.message.text.strip().lower()
    if field not in ('amount', 'description', 'category'):
        keyboard = [['Amount', 'Description', 'Category']]
        await update.message.reply_text(
            "Please choose: Amount, Description, or Category.",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
        return EDIT_FIELD

    context.user_data['edit_field'] = field

    if field == 'category':
        keyboard = [[cat] for cat in CATEGORIES]
        await update.message.reply_text(
            "Select the new category:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        )
    elif field == 'amount':
        await update.message.reply_text("Enter the new amount (e.g. 42.00):")
    else:
        await update.message.reply_text("Enter the new description:")

    return EDIT_VALUE

async def edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    field = context.user_data['edit_field']
    expense = context.user_data['edit_target']
    raw = update.message.text.strip()

    amount = description = category = None

    if field == 'amount':
        try:
            amount = float(raw)
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("Invalid amount. Enter a positive number (e.g. 42.00):")
            return EDIT_VALUE
    elif field == 'description':
        description = raw
    elif field == 'category':
        if raw not in CATEGORIES:
            keyboard = [[cat] for cat in CATEGORIES]
            await update.message.reply_text(
                "Please choose a valid category:",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
            )
            return EDIT_VALUE
        category = raw

    updated = update_expense(expense['id'], user.id, amount=amount, description=description, category=category)

    if updated:
        new_val = amount if amount is not None else (description if description else category)
        await update.message.reply_text(
            f"✅ Updated {field} to: {new_val}",
            reply_markup=main_menu_markup()
        )
    else:
        await update.message.reply_text(
            "Could not update that expense.",
            reply_markup=main_menu_markup()
        )
    return ConversationHandler.END

# ── Cancel ─────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_menu_markup())
    return ConversationHandler.END

# ── Balance ────────────────────────────────────────────────────────────────────

async def view_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    balance = get_user_balance(user.id)

    msg = "💰 Your Account\n\n"
    msg += f"Personal spending: ${balance['personal_total']:.2f}\n"
    msg += f"Your shared paid: ${balance['shared_paid']:.2f}\n"
    msg += f"Your shared half: ${balance['shared_owed']:.2f}\n"
    msg += f"Your total: ${balance['overall_total']:.2f}\n\n"

    if balance['partner_username']:
        msg += f"Partner: @{balance['partner_username']}\n"
        msg += f"Shared net: ${balance['shared_balance']:.2f}\n"
        if balance['shared_balance'] > 0:
            msg += "Your partner owes you."
        elif balance['shared_balance'] < 0:
            msg += "You owe your partner."
        else:
            msg += "Shared spending is even."
    else:
        msg += "No partner linked yet. Use /partner @username"

    await update.message.reply_text(msg)

# ── View Expenses ──────────────────────────────────────────────────────────────

async def view_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    expenses = get_all_expenses(user.id)

    if not expenses:
        await update.message.reply_text("No expenses recorded yet.")
        return

    partner_id = get_partner_id(user.id)
    partner_name = get_username(partner_id) if partner_id else None

    msg = "📊 Couple Expenses\n\n" if partner_id else "📊 Your Expenses\n\n"
    for e in expenses[:10]:
        owner = e['paid_by'] or f"user_{e['user_id']}"
        scope = 'Shared' if e['is_shared'] else 'Personal'
        msg += f"• ${e['amount']:.2f} — {e['description']} ({e['category']})\n"
        msg += f"  paid by @{owner} | {scope}\n"
        msg += f"  {e['created_at'][:10]}\n\n"

    if partner_name:
        msg += f"Linked with @{partner_name}"

    await update.message.reply_text(msg)

# ── Help ───────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("""
🤖 Expense Tracker Bot Help

Commands:
/start - Start the bot
/help - Show this help message
/balance - Check your balance
/expenses - View recent expenses
/partner @username - Link your partner
/add - Add a new expense
/cancel - Cancel current action

Buttons:
➕ Add Expense - Record a new expense
💰 My Balance - Check your balance
📊 View Expenses - See recent expenses
✏️ Edit Expense - Edit one of your expenses
🗑 Delete Expense - Remove an expense
🔗 Set Partner - Link your partner account
❓ Help - Show this message

How it works:
1. Each person tracks their own expenses
2. Link accounts once with /partner @username
3. Shared expenses split 50/50 automatically
4. Both partners can view each other's history
""")

# ── Error handler ──────────────────────────────────────────────────────────────

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set!")

    app = Application.builder().token(token).build()

    add_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^➕ Add Expense$'), add_expense_start),
            CommandHandler('add', add_expense_start)
        ],
        states={
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_received)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, description_received)],
            CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, category_received)],
            SPLIT_USERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, split_received)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    delete_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^🗑 Delete Expense$'), delete_start),
            CommandHandler('delete', delete_start)
        ],
        states={
            DELETE_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_select)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    edit_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex('^✏️ Edit Expense$'), edit_start),
            CommandHandler('edit', edit_start)
        ],
        states={
            EDIT_SELECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select)],
            EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field)],
            EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_value)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", view_balance))
    app.add_handler(CommandHandler("expenses", view_expenses))
    app.add_handler(CommandHandler("partner", set_partner_command))
    app.add_handler(add_conv)
    app.add_handler(delete_conv)
    app.add_handler(edit_conv)
    app.add_handler(MessageHandler(filters.Regex('^💰 My Balance$'), view_balance))
    app.add_handler(MessageHandler(filters.Regex('^📊 View Expenses$'), view_expenses))
    app.add_handler(MessageHandler(filters.Regex('^🔗 Set Partner$'), set_partner_command))
    app.add_handler(MessageHandler(filters.Regex('^❓ Help$'), help_command))
    app.add_error_handler(error_handler)

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
