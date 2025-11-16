import discord
from discord import app_commands, AllowedMentions
from discord.ext import tasks, commands
import os
from dotenv import load_dotenv
import asyncio
import logging, time, asyncio
from bot import StableIntelBot
from tools.multiplayerAPI import MultiplayerAPI
import json

class ChatLogging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._busy = False
        self._drop_count = 0
        self._failures = 0
        self._last_success_ts = 0.0
        self._last_msgid = None
        self._last_msgid_change_ts = time.time()
        self._tick_seq = 0
        self.log = logging.getLogger("eventhorizon.chat")

        # Initialize multiplayer session and configs
        load_dotenv()
        SESSION_ID = os.getenv('GEOFS_SESSION_ID')
        ACCOUNT_ID = os.getenv('GEOFS_ACCOUNT_ID')
        self.multiplayerAPI = MultiplayerAPI(SESSION_ID, ACCOUNT_ID)
        self.config = self.load_config("src/bot/config.json")

    def load_config(self, config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    
    async def cog_load(self):
        if self.config["displayChat"]:
            import asyncio
            try:
                # gets geofs ID through handshake
                await asyncio.to_thread(self.multiplayerAPI.handshake)
            except Exception as e:
                self.log.error("Handshake failed at startup: %s", e)
                return
            # seeds the no-progress watchdog from current state
            # and starts session
            self._last_msgid = getattr(self.multiplayerAPI, "lastMsgID", None)
            self._last_msgid_change_ts = time.time()
            self.printMessages.start()
            self.chatHeartbeat.start()
    
    chat_group = app_commands.Group(name="chat", description="GeoFS chat discord bridge (Historical BiFrost module)")

    @tasks.loop(seconds=5)
    async def printMessages(self):
        # Chat logging loop
        chat_channel_id = self.config["chatLogChannel"] # gets config

        # gets chat channel and verifies
        chat_channel = self.bot.get_channel(chat_channel_id)
        if chat_channel is None:
            chat_channel = await self.bot.fetch_channel(chat_channel_id)
        
        # logs overall information on chat loop performance
        t_tick = time.time()
        self._tick_seq += 1
        tick_id = self._tick_seq
        self.log.debug(
            "[tick %d] begin busy=%s drop=%d failures=%d myId=%s lastMsgId=%s",
            tick_id, self._busy, self._drop_count, self._failures,
            getattr(self.multiplayerAPI, "myID", None),
            getattr(self.multiplayerAPI, "lastMsgID", None)
        )
        if self._busy: # prevents build up of requests if it has not completed since the last event loop
            self._drop_count += 1
            self.log.debug("[tick %d] skipped (busy). drop_count=%d", tick_id, self._drop_count)
            return
        self._busy = True
        
        try:
            try:
                # logs information on most recent multiplayer request
                t0 = time.time()
                messages = await asyncio.wait_for(
                    asyncio.to_thread(self.multiplayerAPI.getMessages), timeout=20
                )
                dt = time.time() - t0
                self.log.debug("[tick %d] getMessages ok in %.2fs; msgs=%d", tick_id, dt, len(messages))
                self._failures = 0

                self._last_success_ts = time.time()
                
                # --- no-progress watchdog ---
                curr = getattr(self.multiplayerAPI, "lastMsgID", None)
                now = time.time()
                if curr != self._last_msgid:
                    self._last_msgid = curr
                    self._last_msgid_change_ts = now
                else:
                    # If had OK calls but no lastMsgId movement for 10 minutes, re-handshake
                    if self._last_msgid_change_ts and (now - self._last_msgid_change_ts) > 600:
                        self.log.warning("[tick %d] No chat progress for 10m (lastMsgId=%s). Forcing re-handshake.", tick_id, curr)
                        try:
                            await asyncio.to_thread(self.multiplayerAPI.handshake)
                            # after successful handshake, refreshes markers
                            self._last_msgid = getattr(self.multiplayerAPI, "lastMsgID", curr)
                            self._last_msgid_change_ts = time.time()
                            self._failures = 0
                            self.log.info("[tick %d] Re-handshake after no-progress succeeded (lastMsgId=%s).", tick_id, self._last_msgid)
                        except Exception as e:
                            self.log.error("[tick %d] Re-handshake after no-progress FAILED: %s", tick_id, e)

            except asyncio.TimeoutError:
                # another opportunity for rehandshake
                self._failures = getattr(self, "_failures", 0) + 1
                self.log.warning("[tick %d] getMessages TIMEOUT (failures=%d)", tick_id, self._failures)
                if self._failures >= 3:
                    try:
                        await asyncio.to_thread(self.multiplayerAPI.handshake)
                        self._failures = 0
                        self.log.info("[tick %d] re-handshake succeeded", tick_id)
                    except Exception as e:
                        self.log.error("[tick %d] re-handshake failed: %s", tick_id, e)
                messages = []
            
            # builds message string for discord
            discord_message = ""
            for msg in messages:
                discord_message += f"({msg.get('acid', '?')}) | {msg.get('cs','?')}: {msg.get('msg','')}\n"
            if discord_message != "":
                await chat_channel.send(discord_message, allowed_mentions=AllowedMentions.none())
        except Exception as e:
            self.log.exception("[tick %d] printMessages error: %s", tick_id, e)
        finally:
            self._busy = False
            self.log.debug("[tick %d] end elapsed=%.2fs", tick_id, time.time() - t_tick)
            # If skipped ticks while busy, send a single summary notice now.
            if self._drop_count:
                try:
                    await chat_channel.send(f"⚠️ Chat backlog: skipped {self._drop_count} cycle(s). Some messages may be missing.")
                except Exception as e:
                    self.log.error("[tick %d] drop notice error: %s", tick_id, e)
                finally:
                    self._drop_count = 0

    @printMessages.error
    async def printMessages_error(self, error):
        # surfaces unhandled task exceptions
        self.log.exception("[task] printMessages crashed: %s", error)
    
    @tasks.loop(minutes=5)
    async def chatHeartbeat(self):
        # periodic “state-of-the-world” log to correlate with Discord RESUMEDs
        last_ok_age = time.time() - self._last_success_ts if self._last_success_ts else None
        msg_age = (time.time() - self._last_msgid_change_ts) if self._last_msgid_change_ts else None
        self.log.info(
            "[hb] failures=%d drop=%d busy=%s last_ok_age=%s last_msgid_age=%s myId=%s lastMsgId=%s",
            self._failures, self._drop_count, self._busy,
            f"{last_ok_age:.0f}s" if last_ok_age is not None else "never",
            f"{msg_age:.0f}s" if msg_age is not None else "never",
            getattr(self.multiplayerAPI, "myID", None),
            getattr(self.multiplayerAPI, "lastMsgID", None)
        )

    @chat_group.command(name="send-msg", description="Send a message to GeoFS Chat")
    async def send_msg(self, interaction: discord.Interaction, msg: str):
        # Verify permissions
        if interaction.guild.get_role(self.config["developerRole"]) not in interaction.user.roles:
            embed = discord.Embed(
                title="Failed",
                description=(
                    "You may not use this command."
                ),
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed)
            return
        
        try:
            await asyncio.to_thread(self.multiplayerAPI.sendMsg, msg)
        except Exception as e:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Error",
                    description=str(e),
                    color=discord.Color.red()
                )
            )
            return
        embed = discord.Embed(
            title="Success",
            description="Sent",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

async def setup(bot: StableIntelBot):
    await bot.add_cog(ChatLogging(bot))