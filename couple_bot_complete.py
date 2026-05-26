import discord
from discord.ext import commands, tasks
import json
import os
import asyncio
import re
import sqlite3
import requests
import pickle
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# 環境変数読み込み
load_dotenv()

# 設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

# SQLiteデータベース初期化
def init_db():
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    # 家計簿テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        amount REAL,
        category TEXT,
        description TEXT,
        date TEXT
    )''')

    # リマインダーテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_id INTEGER,
        message TEXT,
        remind_time TEXT,
        is_active INTEGER DEFAULT 1
    )''')

    # タスクテーブル
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        assignee TEXT,
        task TEXT,
        category TEXT,
        due_date TEXT,
        status TEXT DEFAULT 'pending'
    )''')

    # 設定テーブル
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )''')

    conn.commit()
    conn.close()

# AI機能クラス（簡易版）
class SimpleAI:
    def auto_categorize_expense(self, description):
        """簡易的な支出カテゴリ自動判定"""
        description_lower = description.lower()

        if any(word in description_lower for word in ['食事', '昼食', '夕食', '朝食', 'ランチ', '弁当', 'レストラン', 'カフェ', 'コンビニ', 'スーパー', '食材']):
            return '食費'
        elif any(word in description_lower for word in ['電車', 'バス', 'タクシー', '交通', 'ガソリン', '駐車']):
            return '交通費'
        elif any(word in description_lower for word in ['映画', 'ゲーム', '本', '書籍', 'エンタメ', '趣味']):
            return '娯楽'
        elif any(word in description_lower for word in ['薬', '病院', '医療', 'クリニック']):
            return '医療費'
        elif any(word in description_lower for word in ['シャンプー', '洗剤', 'ティッシュ', '日用品', '掃除']):
            return '日用品'
        elif any(word in description_lower for word in ['服', '靴', '衣類', 'ファッション']):
            return '衣類'
        else:
            return 'その他'

# グローバルAIインスタンス
simple_ai = SimpleAI()

# Bot起動時
@bot.event
async def on_ready():
    print(f'🤖 {bot.user} が起動しました！')
    print(f'   サーバー数: {len(bot.guilds)}')
    print(f'   ユーザー数: {len(set(bot.get_all_members()))}')
    print('='*50)

    init_db()
    reminder_check.start()

    # 起動通知
    for guild in bot.guilds:
        for channel in guild.text_channels:
            if 'general' in channel.name.lower() or 'bot' in channel.name.lower():
                embed = discord.Embed(
                    title="🤖 夫婦Bot起動完了",
                    description="おはようございます！Botの準備ができました。",
                    color=0x00ff00
                )
                embed.add_field(name="利用可能コマンド", value="`!ヘルプ` で確認", inline=True)
                embed.add_field(name="ステータス", value="🟢 正常稼働中", inline=True)
                try:
                    await channel.send(embed=embed)
                    break
                except:
                    continue

# === 家計簿機能 ===
@bot.command(name='支出', aliases=['expense', 'exp'])
async def add_expense(ctx, amount: float, *, description: str = ""):
    """支出を記録: !支出 1500 昼食"""
    # AI自動カテゴリ判定
    category = simple_ai.auto_categorize_expense(description)

    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    c.execute('''INSERT INTO expenses (user_id, user_name, amount, category, description, date)
                 VALUES (?, ?, ?, ?, ?, ?)''',
              (ctx.author.id, ctx.author.display_name, amount, category, description,
               datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="💰 支出記録完了",
        description=f"**{amount:,}円** を記録しました",
        color=0xff6b6b
    )
    embed.add_field(name="🤖 AI判定カテゴリ", value=category, inline=True)
    embed.add_field(name="📝 詳細", value=description or "なし", inline=True)
    embed.add_field(name="👤 記録者", value=ctx.author.display_name, inline=True)

    await ctx.send(embed=embed)

@bot.command(name='家計簿', aliases=['expenses', 'money'])
async def show_expenses(ctx, period: str = "today"):
    """家計簿確認: !家計簿 [today/week/month]"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    if period == "today":
        date_filter = datetime.now().strftime('%Y-%m-%d')
        c.execute('SELECT * FROM expenses WHERE date LIKE ?', (f"{date_filter}%",))
        title = "📊 今日の支出"
    elif period == "week":
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        c.execute('SELECT * FROM expenses WHERE date >= ?', (week_ago,))
        title = "📊 今週の支出"
    else:  # month
        month_start = datetime.now().replace(day=1).strftime('%Y-%m-%d')
        c.execute('SELECT * FROM expenses WHERE date >= ?', (month_start,))
        title = "📊 今月の支出"

    expenses = c.fetchall()
    conn.close()

    if not expenses:
        await ctx.send("記録されている支出がありません。")
        return

    # カテゴリ別集計
    category_totals = {}
    total = 0
    daily_avg = 0

    for expense in expenses:
        category = expense[4]
        amount = expense[3]
        total += amount
        category_totals[category] = category_totals.get(category, 0) + amount

    # 日割り平均計算
    if period == "month":
        daily_avg = total / datetime.now().day
    elif period == "week":
        daily_avg = total / 7

    embed = discord.Embed(title=title, color=0x4ecdc4)
    embed.add_field(name="💳 合計", value=f"{total:,}円", inline=True)

    if daily_avg > 0:
        embed.add_field(name="📈 日平均", value=f"{daily_avg:,.0f}円", inline=True)

    embed.add_field(name="📊 記録件数", value=f"{len(expenses)}件", inline=True)

    # 上位カテゴリを表示
    sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    for i, (category, amount) in enumerate(sorted_categories[:6]):
        percentage = (amount / total * 100) if total > 0 else 0
        embed.add_field(
            name=f"📂 {category}",
            value=f"{amount:,}円 ({percentage:.1f}%)",
            inline=True
        )

    await ctx.send(embed=embed)

# === リマインダー機能 ===
@bot.command(name='リマインダー', aliases=['remind', 'rm'])
async def set_reminder(ctx, time: str, *, message: str):
    """リマインダー設定: !リマインダー 30m ゴミ出し"""
    time_match = re.match(r'(\d+)([mhd])', time)
    if not time_match:
        await ctx.send("❌ 時間の形式が正しくありません。\n**例:** `30m` (30分後), `2h` (2時間後), `1d` (1日後)")
        return

    amount, unit = time_match.groups()
    amount = int(amount)

    if unit == 'm':
        remind_time = datetime.now() + timedelta(minutes=amount)
        time_str = f"{amount}分後"
    elif unit == 'h':
        remind_time = datetime.now() + timedelta(hours=amount)
        time_str = f"{amount}時間後"
    else:  # d
        remind_time = datetime.now() + timedelta(days=amount)
        time_str = f"{amount}日後"

    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()
    c.execute('''INSERT INTO reminders (user_id, channel_id, message, remind_time)
                 VALUES (?, ?, ?, ?)''',
              (ctx.author.id, ctx.channel.id, message, remind_time.isoformat()))
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="⏰ リマインダー設定完了",
        description=f"**{remind_time.strftime('%m/%d %H:%M')}** にリマインドします",
        color=0x95a5a6
    )
    embed.add_field(name="⏱️ タイミング", value=time_str, inline=True)
    embed.add_field(name="📝 内容", value=message, inline=False)

    await ctx.send(embed=embed)

# === タスク管理機能 ===
@bot.command(name='タスク', aliases=['task', 'todo'])
async def add_task(ctx, assignee: str, *, task_desc: str):
    """タスク追加: !タスク @夫 洗濯物を畳む"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    # カテゴリ自動判定
    category = "その他"
    if any(word in task_desc for word in ["料理", "食事", "買い物", "食材"]):
        category = "食事・買い物"
    elif any(word in task_desc for word in ["洗濯", "掃除", "片付け", "整理"]):
        category = "掃除・洗濯"
    elif any(word in task_desc for word in ["修理", "点検", "メンテナンス"]):
        category = "メンテナンス"

    c.execute('''INSERT INTO tasks (user_id, assignee, task, category)
                 VALUES (?, ?, ?, ?)''',
              (ctx.author.id, assignee, task_desc, category))

    task_id = c.lastrowid
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="📝 タスク追加完了",
        description=f"**{assignee}** にタスクを割り当てました",
        color=0x3498db
    )
    embed.add_field(name="🆔 タスクID", value=f"#{task_id}", inline=True)
    embed.add_field(name="📂 カテゴリ", value=category, inline=True)
    embed.add_field(name="📋 内容", value=task_desc, inline=False)

    await ctx.send(embed=embed)

@bot.command(name='完了', aliases=['done', 'finish'])
async def complete_task(ctx, task_id: int):
    """タスク完了: !完了 1"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    c.execute('SELECT task, assignee FROM tasks WHERE id = ? AND status = "pending"', (task_id,))
    task_info = c.fetchone()

    if not task_info:
        await ctx.send("❌ 指定されたタスクが見つからないか、すでに完了しています。")
        conn.close()
        return

    c.execute('UPDATE tasks SET status = "completed" WHERE id = ?', (task_id,))
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="✅ タスク完了",
        description=f"**{task_info[0]}** が完了しました！",
        color=0x2ecc71
    )
    embed.add_field(name="👤 担当者", value=task_info[1], inline=True)
    embed.add_field(name="✅ 完了者", value=ctx.author.display_name, inline=True)

    # 完了リアクション
    await ctx.message.add_reaction('🎉')
    await ctx.send(embed=embed)

@bot.command(name='タスク一覧', aliases=['tasks', 'todolist'])
async def show_tasks(ctx):
    """未完了タスク一覧表示"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()
    c.execute('SELECT id, assignee, task, category FROM tasks WHERE status = "pending" ORDER BY id')
    tasks = c.fetchall()
    conn.close()

    if not tasks:
        embed = discord.Embed(
            title="📋 タスク一覧",
            description="🎉 すべてのタスクが完了しています！",
            color=0x2ecc71
        )
        await ctx.send(embed=embed)
        return

    embed = discord.Embed(title="📋 未完了タスク一覧", color=0xe74c3c)

    for task in tasks[:10]:  # 最大10件表示
        embed.add_field(
            name=f"#{task[0]} - {task[1]}",
            value=f"**{task[2]}**\n`📂 {task[3]}`",
            inline=False
        )

    if len(tasks) > 10:
        embed.set_footer(text=f"他に{len(tasks) - 10}件のタスクがあります | !完了 [番号] でタスク完了")
    else:
        embed.set_footer(text="!完了 [番号] でタスク完了")

    await ctx.send(embed=embed)

# === 天気予報機能 ===
@bot.command(name='天気', aliases=['weather'])
async def get_weather(ctx, city: str = "Tokyo"):
    """天気予報: !天気 [都市名]"""
    API_KEY = os.getenv('WEATHER_API_KEY')
    if not API_KEY:
        await ctx.send("❌ 天気予報APIが設定されていません。管理者に設定を依頼してください。")
        return

    try:
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric&lang=ja"
        response = requests.get(url, timeout=10)
        data = response.json()

        if response.status_code == 200:
            weather = data['weather'][0]['description']
            temp = data['main']['temp']
            feels_like = data['main']['feels_like']
            humidity = data['main']['humidity']

            # 天気アイコン
            weather_emoji = "☀️"
            if "雨" in weather:
                weather_emoji = "🌧️"
            elif "雲" in weather:
                weather_emoji = "☁️"
            elif "雪" in weather:
                weather_emoji = "❄️"

            embed = discord.Embed(
                title=f"{weather_emoji} {city}の天気",
                description=weather.capitalize(),
                color=0x87ceeb
            )
            embed.add_field(name="🌡️ 気温", value=f"{temp}°C", inline=True)
            embed.add_field(name="🤒 体感温度", value=f"{feels_like}°C", inline=True)
            embed.add_field(name="💧 湿度", value=f"{humidity}%", inline=True)

            # 服装アドバイス
            if temp < 5:
                advice = "🧥 厚手のコート必須"
            elif temp < 15:
                advice = "🧥 上着があると安心"
            elif temp < 25:
                advice = "👕 長袖がおすすめ"
            else:
                advice = "👕 半袖でOK"

            embed.add_field(name="👔 服装", value=advice, inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send(f"❌ {city}の天気情報の取得に失敗しました。都市名を確認してください。")
    except Exception as e:
        await ctx.send(f"❌ エラーが発生しました: {e}")

# === 買い物リスト機能 ===
@bot.command(name='買い物追加', aliases=['shopping_add', 'shop_add'])
async def add_shopping_item(ctx, *, item: str):
    """買い物リスト追加: !買い物追加 牛乳"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    # 買い物リストテーブルがなければ作成
    c.execute('''CREATE TABLE IF NOT EXISTS shopping_list (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item TEXT,
        added_by TEXT,
        added_date TEXT,
        status TEXT DEFAULT 'pending'
    )''')

    c.execute('''INSERT INTO shopping_list (item, added_by, added_date)
                 VALUES (?, ?, ?)''',
              (item, ctx.author.display_name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    item_id = c.lastrowid
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🛒 買い物リスト追加",
        description=f"**{item}** を追加しました",
        color=0x3498db
    )
    embed.add_field(name="🆔 ID", value=f"#{item_id}", inline=True)
    embed.add_field(name="👤 追加者", value=ctx.author.display_name, inline=True)

    await ctx.send(embed=embed)

@bot.command(name='買い物一覧', aliases=['shopping_list', 'shop_list'])
async def show_shopping_list(ctx):
    """買い物リスト表示"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()
    c.execute('SELECT id, item, added_by FROM shopping_list WHERE status = "pending" ORDER BY id')
    items = c.fetchall()
    conn.close()

    if not items:
        await ctx.send("🛒 買い物リストは空です。")
        return

    embed = discord.Embed(title="🛒 買い物リスト", color=0x3498db)

    item_list = []
    for item in items:
        item_list.append(f"#{item[0]} **{item[1]}** (by {item[2]})")

    embed.description = "\n".join(item_list)
    embed.set_footer(text="!買い物完了 [ID] で購入完了")

    await ctx.send(embed=embed)

@bot.command(name='買い物完了', aliases=['shopping_done', 'shop_done'])
async def complete_shopping_item(ctx, item_id: int):
    """買い物完了: !買い物完了 1"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    c.execute('SELECT item FROM shopping_list WHERE id = ? AND status = "pending"', (item_id,))
    item_info = c.fetchone()

    if not item_info:
        await ctx.send("❌ 指定された商品が見つからないか、すでに購入済みです。")
        conn.close()
        return

    c.execute('UPDATE shopping_list SET status = "completed" WHERE id = ?', (item_id,))
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="✅ 購入完了",
        description=f"**{item_info[0]}** を購入しました！",
        color=0x2ecc71
    )

    await ctx.message.add_reaction('🛍️')
    await ctx.send(embed=embed)

# === 定期実行タスク ===
@tasks.loop(minutes=1)
async def reminder_check():
    """リマインダーチェック（1分毎）"""
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    current_time = datetime.now()
    c.execute('''SELECT id, user_id, channel_id, message FROM reminders
                 WHERE remind_time <= ? AND is_active = 1''',
              (current_time.isoformat(),))

    due_reminders = c.fetchall()

    for reminder in due_reminders:
        reminder_id, user_id, channel_id, message = reminder
        channel = bot.get_channel(channel_id)
        user = bot.get_user(user_id)

        if channel:
            embed = discord.Embed(
                title="⏰ リマインダー",
                description=message,
                color=0xf39c12
            )
            if user:
                embed.set_footer(text=f"設定者: {user.display_name}")

            await channel.send(f"<@{user_id}>", embed=embed)

        # リマインダーを無効化
        c.execute('UPDATE reminders SET is_active = 0 WHERE id = ?', (reminder_id,))

    conn.commit()
    conn.close()

# === カスタムヘルプ ===
@bot.command(name='ヘルプ', aliases=['help'])
async def custom_help(ctx, command: str = None):
    """コマンドヘルプ"""
    embed = discord.Embed(
        title="🤖 夫婦Discord Bot コマンド一覧",
        description="各コマンドの詳細使用例も表示されます",
        color=0x9b59b6
    )

    embed.add_field(
        name="💰 家計管理",
        value="`!支出 1500 昼食` - 支出記録(AI自動分類)\n`!家計簿 [today/week/month]` - 支出確認",
        inline=False
    )
    embed.add_field(
        name="⏰ リマインダー",
        value="`!リマインダー 30m ゴミ出し` - 通知設定\n`!リマインダー 2h 洗濯物` - 2時間後通知",
        inline=False
    )
    embed.add_field(
        name="📝 タスク管理",
        value="`!タスク @夫 洗濯物畳み` - タスク追加\n`!完了 1` - タスク完了\n`!タスク一覧` - 一覧表示",
        inline=False
    )
    embed.add_field(
        name="🛒 買い物リスト",
        value="`!買い物追加 牛乳` - 商品追加\n`!買い物一覧` - リスト表示\n`!買い物完了 1` - 購入完了",
        inline=False
    )
    embed.add_field(
        name="🌤️ その他",
        value="`!天気 Tokyo` - 天気予報\n`!ステータス` - Bot状態確認",
        inline=False
    )

    embed.set_footer(text="💡 使用例: !支出 500 コンビニ弁当")
    await ctx.send(embed=embed)

# === ステータス確認 ===
@bot.command(name='ステータス', aliases=['status', 'ping'])
async def bot_status(ctx):
    """Bot状態確認"""
    # データベース件数取得
    conn = sqlite3.connect('couple_data.db')
    c = conn.cursor()

    c.execute('SELECT COUNT(*) FROM expenses')
    expense_count = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM tasks WHERE status = "pending"')
    task_count = c.fetchone()[0]

    c.execute('SELECT COUNT(*) FROM reminders WHERE is_active = 1')
    reminder_count = c.fetchone()[0]

    conn.close()

    # レイテンシ計算
    latency = round(bot.latency * 1000)

    embed = discord.Embed(
        title="🤖 Botステータス",
        color=0x00ff00
    )
    embed.add_field(name="🏓 レイテンシ", value=f"{latency}ms", inline=True)
    embed.add_field(name="💰 支出記録", value=f"{expense_count}件", inline=True)
    embed.add_field(name="📝 未完了タスク", value=f"{task_count}件", inline=True)
    embed.add_field(name="⏰ アクティブリマインダー", value=f"{reminder_count}件", inline=True)
    embed.add_field(name="🌐 接続状態", value="🟢 正常", inline=True)
    embed.add_field(name="📊 サーバー数", value=f"{len(bot.guilds)}", inline=True)

    embed.set_footer(text=f"起動時刻: Bot起動から{datetime.now().strftime('%H:%M')}")

    await ctx.send(embed=embed)

# === エラーハンドリング ===
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 引数が不足しています。`!ヘルプ` でコマンドの使い方を確認してください。")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ そのコマンドは存在しません。`!ヘルプ` でコマンド一覧を確認してください。")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ 引数の形式が正しくありません。`!ヘルプ` で正しい形式を確認してください。")
    else:
        print(f"予期しないエラー: {error}")
        await ctx.send("❌ 予期しないエラーが発生しました。管理者に連絡してください。")

# === メイン実行部 ===
if __name__ == "__main__":
    print("🤖 夫婦Discord Bot を起動しています...")
    print("=" * 50)

    # 環境変数チェック
    token = os.getenv('DISCORD_BOT_TOKEN')
    if not token:
        print("❌ エラー: DISCORD_BOT_TOKEN が設定されていません")
        print("   .envファイルを確認してください")
        exit(1)

    print("✅ 設定確認完了")
    print("🚀 Bot起動中...")

    try:
        bot.run(token)
    except Exception as e:
        print(f"❌ Bot起動エラー: {e}")
        print("   トークンや設定を確認してください")