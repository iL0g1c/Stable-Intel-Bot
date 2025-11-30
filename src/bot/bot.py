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
        self.config = self.load_config("src/bot/config.json")
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
        t = Thread(target=self.flaskApp.run, kwargs={"host": "127.0.0.1", "port": 5002}, daemon=True)
        t.start()
        self.logger.log(20, "Flask server launched on 127.0.0.1:5002")
        self.loop.create_task(self._task_supervisor())

        self.logger.log(20, "Starting task processing loops...")
        self.loop.create_task(self.process_tasks())

    async def _task_supervisor(self):
        while True:
            await asyncio.sleep(60)
            # crude check: ensure task exists in all_tasks
            found = any(
                t.get_coro().__name__ == "process_tasks"
                for t in asyncio.all_tasks(loop=self.loop)
            )
            self.logger.debug("supervisor: process_tasks running=%s", found)

    def setup_routes(self):
        def schedule_to_loop(coro):
            fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
            # log when done (if it raised)
            def _cb(f):
                try:
                    f.result()
                except Exception as e:
                    self.logger.exception("Scheduled task raised: %s", e)
            fut.add_done_callback(_cb)
            return fut
        @self.flaskApp.route("/aircraft-change", methods=["POST"])
        def aircraft_change():
            data = request.json
            self.logger.info("[Flask] /aircraft-change POST received (len=%s)", len(data) if data is not None else "None")
            if not isinstance(data, list):
                self.logger.warning("[Flask] /aircraft-change invalid payload type: %s", type(data))
                return 'Invalid data format. Expected a list.', 400
            schedule_to_loop(self.task_queue.put(("aircraft-change", data)))
            return "", 204

        @self.flaskApp.route("/new-account", methods=["POST"])
        def new_account():
            data = request.json
            self.logger.info("[Flask] /new-account POST received (len=%s)", len(data) if data is not None else "None")
            if not isinstance(data, list):
                self.logger.warning("[Flask] /new-account invalid payload type: %s", type(data))
                return 'Invalid data format. Expected a list.', 400
            schedule_to_loop(self.task_queue.put(("new-account", data)))
            return "", 204

        @self.flaskApp.route("/callsign-change", methods=["POST"])
        def callsign_change():
            data = request.json
            self.logger.info("[Flask] /callsign-change POST received (len=%s)", len(data) if data is not None else "None")
            if not isinstance(data, list):
                self.logger.warning("[Flask] /callsign-change invalid payload type: %s", type(data))
                return 'Invalid data format. Expected a list.', 400
            schedule_to_loop(self.task_queue.put(("callsign-change", data)))
            return "", 204

        @self.flaskApp.route("/teleporation", methods=["POST"])
        def teleporation():
            data = request.json
            self.logger.info("[Flask] /teleporation POST received (len=%s)", len(data) if data is not None else "None")
            if not isinstance(data, list):
                self.logger.warning("[Flask] /teleporation invalid payload type: %s", type(data))
                return 'Invalid data format. Expected a list.', 400
            schedule_to_loop(self.task_queue.put(("teleporation", data)))
            return "", 204

        @self.flaskApp.route("/activity-change", methods=["POST"])
        def activity_change():
            data = request.json
            self.logger.info("[Flask] /activity-change POST received (len=%s)", len(data) if data is not None else "None")
            if not isinstance(data, list):
                self.logger.warning("[Flask] /activity-change invalid payload type: %s", type(data))
                return 'Invalid data format. Expected a list.', 400
            schedule_to_loop(self.task_queue.put(("activity-change", data)))
            return "", 204
        
    async def process_tasks(self):
        # process tasks from the queue
        self.logger.info("process_tasks loop started")
        while True:
            task_type, data = await self.task_queue.get()
            self.logger.info("Dequeued task '%s' (items=%s)", task_type, (len(data) if hasattr(data, "__len__") else "unknown"))
            try:
                try:
                    if task_type == "aircraft-change":
                        await self.process_aircraft_change(data)
                    elif task_type == "new-account":
                        await self.process_new_account(data)
                    elif task_type == "callsign-change":
                        await self.process_callsign_change(data)
                    elif task_type == "teleporation":
                        await self.process_teleportation(data)
                    elif task_type == "activity-change":
                        await self.process_activity_change(data)
                    else:
                        self.logger.warning("Unknown task type in queue: %s", task_type)
                except Exception as e:
                    # Log exception but don't let the worker die
                    self.logger.exception("Exception while handling task '%s': %s", task_type, e)
            finally:
                try:
                    self.task_queue.task_done()
                except Exception:
                    # defensive
                    self.logger.exception("task_done() failed for '%s'", task_type)
    
    async def process_aircraft_change(self, data):
        channel = self.get_channel_config("aircraft-change")
        if not channel or not self.config.get("displayAircraftChanges", True):
            return
        
        embeds = [
            discord.Embed(
                title="Aircraft Change",
                description=f"**Callsign:** {change_data['callsign']}\n **Old Aircraft:** {change_data['oldAircraft']}\n **New Aircraft:** {change_data['newAircraft']}",
                color=discord.Color.green()
            ) for change_data in data
        ]
        await self.send_embeds(channel, embeds)

    async def process_new_account(self, data):
        channel = self.get_channel_config("new-account")
        if not channel or not self.config.get("displayNewAccounts", True):
            return
        
        embeds = []
        for account_data in data:
            if str(account_data['callsign']) == "":
                callsign = "*NO CALLSIGN SET*"
            else:
                callsign = account_data['callsign']

            embeds.append(
                discord.Embed(
                    title="New Account",
                    description=f"**Account ID:** {account_data['acid']}\n **Callsign:** {callsign}",
                    color=discord.Color.green()
                )
            )
        await self.send_embeds(channel, embeds)

    async def process_callsign_change(self, data):
        channel = self.get_channel_config("callsign-change")
        if not channel or not self.config.get("displayCallsignChanges", True):
            return
        
        embeds = [
            discord.Embed(
                title="Callsign Change",
                description=f"**Acoount ID:** {callsign_data['acid']}\n **Old Callsign:** {callsign_data['oldCallsign']}\n **New Callsign:** {callsign_data['newCallsign']}",
                color=discord.Color.green()
            ) for callsign_data in data
        ]
        await self.send_embeds(channel, embeds)

    async def process_teleportation(self, data):
        channel = self.get_channel_config("teleporation")
        if not channel or not self.config.get("displayTeleporations", True):
            return
        
        embeds = [
            discord.Embed(
                title="Teleporation",
                description=f"**Account ID**: {teleporation_data['acid']}\n **Old Position:** {teleporation_data['oldLatitude']}, {teleporation_data['oldLongitude']}\n **New Position:** {teleporation_data['newLatitude']}, {teleporation_data['newLongitude']}\n **Distance:** {round(teleporation_data['distance'])} km",
                color=discord.Color.green()
            ) for teleporation_data in data
        ]
        await self.send_embeds(channel, embeds)

    async def process_activity_change(self, data):
        channel = self.get_channel_config("activity-change")
        if not channel or not self.config.get("displayActivityChanges", True):
            return
        for activity_data in data:
            print(type(activity_data['acid']))
            print(type(activity_data['status']))
            if activity_data['acid'] == 400813 and activity_data['status'] == 'online':
                await self.get_channel_config("xavier-detected").send(f"<@&{self.config['xavier-ping']}> is online.")
        embeds = [
            discord.Embed(
                title="Activity Change",
                description=f"**Account ID:** {activity_data['acid']}\n **Status:** {activity_data['status']}",
                color=discord.Color.green()
            ) for activity_data in data
        ]
        await self.send_embeds(channel, embeds)
    
    async def send_embeds(self, channel, embeds):
        async with self.lock:
            for idx, embed in enumerate(embeds, start=1):
                try:
                    if not channel:
                        self.logger.warning("send_embeds: channel is None, skipping embed #%d", idx)
                        continue
                    await channel.send(embed=embed)
                    await asyncio.sleep(self.throttleInterval)
                except Exception as e:
                    self.logger.exception("Failed to send embed #%d to channel %s: %s", idx, getattr(channel, "id", "unknown"), e)
                    # continue to next embed (do not raise)


    def get_channel_config(self, event_type): # gets the channel for the event type
        if event_type == "aircraft-change":
            channel = self.get_channel(self.config["aircraftChangeLogChannel"])
        elif event_type == "new-account":
            channel = self.get_channel(self.config["newAccountLogChannel"])
        elif event_type == "callsign-change":
            channel = self.get_channel(self.config["callsignChangeLogChannel"])
        elif event_type == "teleporation":
            channel = self.get_channel(self.config["teleporationLogChannel"])
        elif event_type == "activity-change":
            channel = self.get_channel(self.config["activityChangeLogChannel"])
        elif event_type == "xavier-detected":
            channel = self.get_channel(self.config["xavier-detected"])
        else:
            self.logger.log(40, f"Invalid event type: {event_type}")
            return None

        if not channel:
            self.logger.warning(f"Channel ID for '{event_type}' is not set in the configuration.")
            return None
        return channel
        
    async def _load_extensions(self) -> None:
        for extension in ("chatLogging",):
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