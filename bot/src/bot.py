import os
import logging
from datetime import date, time, timedelta
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from database.db import (
    init_db,
    add_or_get_user,
    add_expense,
    get_user_balance,
    get_all_expenses,
    get_expense_weeks,
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

def current_week_start():
    today = date.today()
    return today - timedelta(days=today.weekday())

def normalize_week_start(week_start=None):
    if week_start is None:
        return current_week_start()
    if isinstance(week_start, date):
        return week_start - timedelta(days=week_start.weekday())
    parsed = date.fromisoformat(str(week_start))
    return parsed - timedelta(days=parsed.weekday())

def format_week_range(week_start):
    start = normalize_week_start(week_start)
    end = start + timedelta(days=6)
    return f"{start.strftime('%b')} {start.day}, {start.year} - {end.strftime('%b')} {end.day}, {end.year}"

def main_menu_markup():
    keyboard = [
        ['➕ Add Expense', '💰 Our Balance'],
        ['📊 View Expenses', '📅 WEEKLY'],
        ['🔗 Set Partner'],
        ['✏️ Edit Expense', '🗑 Delete Expense'],
        ['❓ Help']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def format_expense_list(expenses):
    """Format a numbered list of expenses for display."""
    lines = []
    for i, e in enumerate(expenses, 1):
        lines.append(
            f"{i}. ${e['amount']:.2f} — {e['description']} ({e['category']})"
        )
    return '\n\n'.join(lines)

def build_weekly_keyboard(user_id, selected_week_start):
    current_start = current_week_start()
    previous_week = selected_week_start - timedelta(days=7)
    next_week = selected_week_start + timedelta(days=7)

    rows = [
        [
            InlineKeyboardButton('⬅️ Previous Week', callback_data=f'weekly:{previous_week.isoformat()}'),
            InlineKeyboardButton('This Week', callback_data=f'weekly:{current_start.isoformat()}'),
        ]
    ]

    if selected_week_start < current_start:
        rows[0].append(
            InlineKeyboardButton('Next Week', callback_data=f'weekly:{next_week.isoformat()}')
        )

    recent_weeks = [
        week_start
        for week_start in get_expense_weeks(user_id, limit=6)
        if week_start != selected_week_start.isoformat()
    ]

    for index in range(0, len(recent_weeks), 2):
        row = []
        for week_start in recent_weeks[index:index + 2]:
            row.append(
                InlineKeyboardButton(
                    format_week_range(week_start),
                    callback_data=f'weekly:{week_start}',
                )
            )
        rows.append(row)

    return InlineKeyboardMarkup(rows)

async def send_weekly_report(update: Update, context: ContextTypes.DEFAULT_TYPE, week_start=None):
    user = update.effective_user
    selected_week = normalize_week_start(week_start)
    selected_week_iso = selected_week.isoformat()
    balance = get_user_balance(user.id, selected_week_iso)
    expenses = list(get_all_expenses(user.id, selected_week_iso))

    partner_id = get_partner_id(user.id)
    partner_name = get_username(partner_id) if partner_id else None
    week_label = format_week_range(selected_week)

    msg = f"📅 WEEKLY\n\nWeek: {week_label}\n\n"
    msg += f"Total spending: ${balance['total']:.2f}\n"

    if partner_name:
        msg += f"\nPartner: @{partner_name}"
    else:
        msg += "\nNo partner linked yet. Use /partner @username"

    msg += "\n\nExpenses this week:\n"
    if expenses:
        visible_expenses = expenses[:10]
        msg += f"\n{format_expense_list(visible_expenses)}"
        if len(expenses) > len(visible_expenses):
            msg += f"\n\nShowing latest {len(visible_expenses)} of {len(expenses)} expenses."
    else:
        msg += "\nNo expenses recorded for this week yet."

    markup = build_weekly_keyboard(user.id, selected_week)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(msg, reply_markup=markup)
    else:
        await update.message.reply_text(msg, reply_markup=markup)

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
    keyboard = [['Split with Partner 50/50', 'Personal Only']]
    await update.message.reply_text(
        "Who is paying?",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    )
    return SPLIT_USERS

async def split_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    amount = context.user_data['amount']
    description = context.user_data['description']
    category = context.user_data['category']
    is_split = update.message.text == 'Split with Partner 50/50'

    if is_split and get_partner_id(user.id) is None:
        await update.message.reply_text(
            "Set your partner first.\nUse: /partner @username",
            reply_markup=main_menu_markup()
        )
        return ConversationHandler.END

    add_expense(
        user.id, amount, description, category,
        is_shared=is_split,
        paid_by=user.username or f"user_{user.id}"
    )

    if is_split:
        msg = f"✅ Expense recorded!\n\nAmount: ${amount:.2f} total\nYour share: ${amount/2:.2f}\nPartner's share: ${amount/2:.2f}\nDescription: {description}\nCategory: {category}"
    else:
        msg = f"✅ Expense recorded!\n\nAmount: ${amount:.2f}\nDescription: {description}\nCategory: {category}\nType: Personal"

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
    week_label = format_week_range(current_week_start())

    msg = f"💰 This Week\n\nWeek: {week_label}\n\n"
    msg += f"You spent: ${balance['total']:.2f}\n"

    if balance['partner_username']:
        partner_id = get_partner_id(user.id)
        partner_balance = get_user_balance(partner_id)
        msg += f"@{balance['partner_username']} spent: ${partner_balance['total']:.2f}\n"
        msg += f"\nTotal: ${balance['total'] + partner_balance['total']:.2f}"
    else:
        msg += "\nNo partner linked yet. Use /partner @username"

    await update.message.reply_text(msg)

# ── View Expenses ──────────────────────────────────────────────────────────────

async def view_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    expenses = get_all_expenses(user.id)

    if not expenses:
        await update.message.reply_text("No expenses recorded this week yet.")
        return

    partner_id = get_partner_id(user.id)
    partner_name = get_username(partner_id) if partner_id else None

    msg = "📊 This Week\n\n" if partner_id else "📊 Your Week\n\n"
    msg += f"Week: {format_week_range(current_week_start())}\n\n"
    for e in expenses[:10]:
        owner = e['paid_by'] or f"user_{e['user_id']}"
        msg += f"• ${e['amount']:.2f} — {e['description']} ({e['category']})\n"
        msg += f"  by @{owner} on {e['created_at'][:10]}\n\n"

    if len(expenses) > 10:
        msg += f"Showing latest 10 of {len(expenses)} expenses.\n\n"

    if partner_name:
        msg += f"Linked with @{partner_name}"

    await update.message.reply_text(msg)

# ── Weekly ────────────────────────────────────────────────────────────────────

async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await send_weekly_report(update, context)
        return

    raw = context.args[0].strip().lower()
    if raw in {'current', 'this', 'now'}:
        await send_weekly_report(update, context, current_week_start())
        return

    if raw in {'last', 'previous', 'prev'}:
        await send_weekly_report(update, context, current_week_start() - timedelta(days=7))
        return

    if raw.isdigit():
        await send_weekly_report(update, context, current_week_start() - timedelta(days=7 * int(raw)))
        return

    try:
        await send_weekly_report(update, context, date.fromisoformat(context.args[0]))
    except ValueError:
        await update.message.reply_text(
            "Use /weekly, /weekly last, /weekly 2, or /weekly YYYY-MM-DD to open a specific week."
        )

async def weekly_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    _, week_start = query.data.split(':', 1)
    await send_weekly_report(update, context, week_start)

# ── Weekly Reset ──────────────────────────────────────────────────────────────

async def weekly_reset_job():
    """Log weekly reset (data persists, just marking the boundary)."""
    logger.info("📅 Weekly reset triggered at end of Sunday. Fresh week starting Monday!")

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
💰 Our Balance - Check your balance
📊 View Expenses - See recent expenses
📅 WEEKLY - Browse expenses by week
✏️ Edit Expense - Edit one of your expenses
🗑 Delete Expense - Remove an expense
🔗 Set Partner - Link your partner account
❓ Help - Show this message

How it works:
1. Each person has a personal account for tracking expenses
2. Link accounts once with /partner @username
3. Mark expenses as personal or split 50/50 with partner
4. Split expenses automatically add to both personal accounts
5. Weekly totals reset automatically every Monday
6. Use /weekly or the WEEKLY button to browse past weeks
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
    app.add_handler(CommandHandler("weekly", weekly_command))
    app.add_handler(CommandHandler("partner", set_partner_command))
    app.add_handler(add_conv)
    app.add_handler(delete_conv)
    app.add_handler(edit_conv)
    app.add_handler(MessageHandler(filters.Regex('^💰 Our Balance$'), view_balance))
    app.add_handler(MessageHandler(filters.Regex('^📊 View Expenses$'), view_expenses))
    app.add_handler(MessageHandler(filters.Regex('^📅 WEEKLY$'), weekly_command))
    app.add_handler(MessageHandler(filters.Regex('^🔗 Set Partner$'), set_partner_command))
    app.add_handler(MessageHandler(filters.Regex('^❓ Help$'), help_command))
    app.add_handler(CallbackQueryHandler(weekly_callback, pattern=r'^weekly:'))
    app.add_error_handler(error_handler)

    # Set up weekly reset scheduler for Sunday at 23:59
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        weekly_reset_job,
        CronTrigger(day_of_week=6, hour=23, minute=59),  # Sunday at 23:59
        name='weekly_reset'
    )
    app.post_init = lambda: scheduler.start()

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped.")
