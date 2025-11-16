import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import os
from flask import Flask, request
from threading import Thread
import asyncio
import logging
import sys
import json

load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
class StableIntelBot(commands.Bot):
    def __init__(self, botToken):
        # sets up logger
        self.logger = logging.getLogger("STABLE INTEL")
        self.logger.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        self.logger.addHandler(console_handler)

        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='=', intents=intents)

        self.flaskApp = Flask(__name__)
        self.flaskApp.logger.handlers = self.logger.handlers
        self.flaskApp.logger.setLevel(self.logger.level)

        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.handlers.clear()
        werkzeug_logger.propagate = False
        werkzeug_logger.setLevel(self.logger.level)
        werkzeug_logger.addHandler(console_handler)

        # sets up the event loop

        self.throttleInterval = 0.2
        self.task_queue = asyncio.Queue()

        self.lock = asyncio.Lock()
        self.config = self.load_config("config.json")
        self.setup_routes()

    def load_config(self, config_path):
        with open(config_path, "r") as f:
            return json.load(f)

    async def clear_queue_for_event(self, event_type):
        new_queue = asyncio.Queue()

        # iterate through the queue and put all events that are not the specified type into the new queue
        while not self.task_queue.empty():
            task_type, data = await self.task_queue.get()
            if task_type != event_type:
                await new_queue.put((task_type, data))
            self.task_queue.task_done()
        
        self.task_queue = new_queue

    async def on_ready(self):
        self.logger.log(20, f'{self.user} has connected to Discord!')
    
    async def setup_hook(self) -> None:
        self.logger.log(20, "Starting up...")
        self.logger.log(20, "Loading extensions...")
        await self._load_extensions()
        self.logger.log(20, "Syncing commands...")
        try:
            synced = await self.tree.sync()
            self.logger.log(20, f"Synced {len(synced)} command(s)")
        except Exception as e:
            self.logger.log(40, f"Exception while syncing commands. Error: {e}")

        self.logger.log(20, "Launching Flask server...")
        Thread(target=self.flaskApp.run, kwargs={"host": "127.0.0.1", "port": 5002}).start()
        self.logger.log(20, "Connecting to discord...")

        self.logger.log(20, "Starting task processing loops...")
        self.loop.create_task(self.process_tasks())

    def setup_routes(self):
        @self.flaskApp.route("/bot-mention", methods=["POST"])
        def bot_mention():
            asyncio.run_coroutine_threadsafe(self.task_queue.put(("mention", None)), self.loop)
            return "", 204
        @self.flaskApp.route("/aircraft-change", methods=["POST"])
        def aircraft_change():
            data = request.json
            if not isinstance(data, list):
                return 'Invalid data format. Expected a list.', 400
            asyncio.run_coroutine_threadsafe(self.task_queue.put(("aircraft-change", data)), self.loop)
            return "", 204
        @self.flaskApp.route("/new-account", methods=["POST"])
        def new_account():
            data = request.json
            if not isinstance(data, list):
                return 'Invalid data format. Expected a list.', 400
            asyncio.run_coroutine_threadsafe(self.task_queue.put(("new-account", data)), self.loop)
            return "", 204
        @self.flaskApp.route("/callsign-change", methods=["POST"])
        def callsign_change():
            data = request.json
            if not isinstance(data, list):
                return 'Invalid data format. Expected a list.', 400
            asyncio.run_coroutine_threadsafe(self.task_queue.put(("callsign-change", data)), self.loop)
            return "", 204

    async def process_tasks(self):
        # process tasks from the queue
        while True:
            task_type, data = await self.task_queue.get()
            if task_type == "aircraft-change":
                await self.process_aircraft_change(data)
            elif task_type == "new-account":
                await self.process_new_account(data)
            elif task_type == "callsign-change":
                await self.process_callsign_change(data)
            self.task_queue.task_done()
    
    async def process_aircraft_change(self, data):
        channel = self.get_channel_config("aircraft-change")
        if not channel or not self.config.get("displayAircraftChanges", True):
            return
        
        embeds = [
            discord.Embed(
                title="Aircraft Change",
                description=f"Callsign: {change_data['callsign']}\n Old Aircraft: {change_data['oldAircraft']}\n New Aircraft: {change_data['newAircraft']}",
                color=discord.Color.green()
            ) for change_data in data
        ]
        await self.send_embeds(channel, embeds)

    async def process_new_account(self, data):
        channel = self.get_channel_config("new-account")
        if not channel or not self.config.get("displayNewAccounts", True):
            return
        
        embeds = [
            discord.Embed(
                title="New Account",
                description=f"Acoount ID: {account_data['acid']}\n Callsign: {account_data['callsign']}",
                color=discord.Color.green()
            ) for account_data in data
        ]
        await self.send_embeds(channel, embeds)

    async def process_callsign_change(self, data):
        channel = self.get_channel_config("callsign-change")
        if not channel or not self.config.get("displayCallsignChanges", True):
            return
        
        embeds = [
            discord.Embed(
                title="Callsign Change",
                description=f"Acoount ID: {callsign_data['acid']}\n Old Callsign: {callsign_data['oldCallsign']}\n New Callsign: {callsign_data['newCallsign']}",
                color=discord.Color.green()
            ) for callsign_data in data
        ]
        await self.send_embeds(channel, embeds)
    
    async def send_embeds(self, channel, embeds):
        async with self.lock:
            for embed in embeds:
                await channel.send(embed=embed)
                await asyncio.sleep(self.throttleInterval)

    def get_channel_config(self, event_type): # gets the channel for the event type
        if event_type == "aircraft-change":
            channel = self.get_channel(self.config["aircraftChangeLogChannel"])
        elif event_type == "new-account":
            channel = self.get_channel(self.config["newAccountLogChannel"])
        elif event_type == "callsign-change":
            channel = self.get_channel(self.config["callsignChangeLogChannel"])
        else:
            self.logger.log(40, f"Invalid event type: {event_type}")
            return None

        if not channel:
            self.logger.warning(f"Channel ID for '{event_type}' is not set in the configuration.")
            return None
        return channel
        
    async def _load_extensions(self) -> None:
        for extension in ():
            await self.load_extension(f"cogs.{extension}")

bot = StableIntelBot(BOT_TOKEN)

@bot.event
async def on_guild_join(guild):
    async for entry in guild.audit_logs(action=discord.AuditLogAction.bot_add):
        bot.logger.log(20, f"Joined {guild.name}")

@bot.tree.command(name="ping", description="Check bot connection and latency.")
async def ping(interaction: discord.Interaction):
    delay = round(bot.latency * 1000)
    embed = discord.Embed(title="Pong!", description=f"Latency: {delay}ms", color=discord.Color.green())
    await interaction.response.send_message(embed=embed)

def main():
    bot.run(BOT_TOKEN)

if __name__ == "__main__":
    main()