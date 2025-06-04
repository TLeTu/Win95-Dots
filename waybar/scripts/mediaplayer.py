#!/usr/bin/env python3
import gi
gi.require_version("Playerctl", "2.0")
from gi.repository import Playerctl, GLib
from gi.repository.Playerctl import Player
import argparse
import logging
import sys
import signal
import json
import os
from typing import List

logger = logging.getLogger(__name__)

def signal_handler(sig, frame):
    logger.info("Received signal to stop, exiting")
    sys.stdout.write("\n")
    sys.stdout.flush()
    sys.exit(0)

class PlayerManager:
    def __init__(self, selected_player=None, excluded_player=[]):
        self.manager = Playerctl.PlayerManager()
        self.loop = GLib.MainLoop()
        self.manager.connect("name-appeared", lambda *args: self.on_player_appeared(*args))
        self.manager.connect("player-vanished", lambda *args: self.on_player_vanished(*args))

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        self.selected_player = selected_player
        self.excluded_player = excluded_player.split(',') if excluded_player else []

        # Scrolling state
        self.current_text = ""
        self.display_text = ""
        self.scroll_position = 0
        self.scroll_timeout_id = None
        self.current_player = None
        self.max_length = 40  # Match Waybar's max-length and min-length
        self.default_text = "You're filled with determination."

        self.init_players()

    def init_players(self):
        for player in self.manager.props.player_names:
            if player.name in self.excluded_player:
                continue
            if self.selected_player is not None and self.selected_player != player.name:
                logger.debug(f"{player.name} is not the filtered player, skipping it")
                continue
            self.init_player(player)
        # If no players are initialized, start scrolling default text
        if not self.get_players():
            self.start_scrolling(self.default_text, None)

    def run(self):
        logger.info("Starting main loop")
        self.loop.run()

    def init_player(self, player):
        logger.info(f"Initialize new player: {player.name}")
        player = Playerctl.Player.new_from_name(player)
        player.connect("playback-status", self.on_playback_status_changed, None)
        player.connect("metadata", self.on_metadata_changed, None)
        self.manager.manage_player(player)
        self.on_metadata_changed(player, player.props.metadata)

    def get_players(self) -> List[Player]:
        return self.manager.props.players

    def write_output(self, text, player):
        logger.debug(f"Writing output: {text}")
        output = {
            "text": text,
            "class": f"custom-{'no-player' if player is None else player.props.player_name + '-' + str(player.props.status).lower()}",
            "alt": "no-player" if player is None else player.props.player_name
        }
        sys.stdout.write(json.dumps(output) + "\n")
        sys.stdout.flush()

    def clear_output(self):
        sys.stdout.write("\n")
        sys.stdout.flush()

    def on_playback_status_changed(self, player, status, _=None):
        logger.debug(f"Playback status changed for player {player.props.player_name}: {status}")
        # Check if this is the currently displayed player
        if self.current_player and self.current_player.props.player_name == player.props.player_name:
            if status == "Paused":
                # Stop scrolling when paused
                self.stop_scrolling()
                # Write the current text without scrolling
                if self.current_text:
                    self.write_output(self.current_text[:self.max_length], player)
            elif status == "Playing":
                # Resume scrolling when playing
                if self.current_text:
                    self.start_scrolling(self.current_text, player)
        self.on_metadata_changed(player, player.props.metadata)

    def get_first_playing_player(self):
        players = self.get_players()
        logger.debug(f"Getting first playing player from {len(players)} players")
        if len(players) > 0:
            for player in players[::-1]:
                if player.props.status == "Playing":
                    return player
            return players[0]
        else:
            logger.debug("No players found")
            return None

    def show_most_important_player(self):
        logger.debug("Showing most important player")
        current_player = self.get_first_playing_player()
        if current_player is not None:
            self.on_metadata_changed(current_player, current_player.props.metadata)
        else:
            self.start_scrolling(self.default_text, None)

    def start_scrolling(self, text, player):
        """Start or update the scrolling text with seamless wrapping and delay."""
        if self.scroll_timeout_id:
            GLib.source_remove(self.scroll_timeout_id)
            self.scroll_timeout_id = None
        self.current_text = text
        self.current_player = player
        self.scroll_position = 0
        if text:
            # Add spaces for delay between repetitions
            base_text = text + "     "  # 5 spaces for noticeable delay
            # Ensure the text is long enough for smooth scrolling
            repeat_count = max(2, (self.max_length // len(base_text)) + 2)
            self.current_text = base_text * repeat_count
            self.update_scroll()
            # Update every 300ms for smoother scrolling
            self.scroll_timeout_id = GLib.timeout_add(300, self.update_scroll)
        else:
            self.stop_scrolling()
            self.clear_output()

    def stop_scrolling(self):
        """Stop the scrolling animation."""
        if self.scroll_timeout_id:
            GLib.source_remove(self.scroll_timeout_id)
            self.scroll_timeout_id = None
        self.current_text = ""
        self.display_text = ""
        self.scroll_position = 0
        self.current_player = None

    def update_scroll(self):
        """Update the displayed text by sliding the window left."""
        if not self.current_text or (self.current_player is not None and self.get_first_playing_player() is None):
            return False  # Stop the timeout if no text or player
        text_length = len(self.current_text)
        # Extract exactly max_length characters, wrapping around if needed
        self.display_text = ""
        for i in range(self.max_length):
            index = (self.scroll_position + i) % text_length
            self.display_text += self.current_text[index]
        self.write_output(self.display_text, self.current_player)
        # Move scroll position, wrap around at the end of the full repeated text
        self.scroll_position = (self.scroll_position + 1) % text_length
        return True  # Continue the timeout

    def on_metadata_changed(self, player, metadata, _=None):
        logger.debug(f"Metadata changed for player {player.props.player_name}")
        player_name = player.props.player_name
        artist = player.get_artist()
        title = player.get_title()
        title = title.replace("&", "&") if title else ""

        track_info = ""
        if player_name == "spotify" and "mpris:trackid" in metadata.keys() and ":ad:" in player.props.metadata["mpris:trackid"]:
            track_info = "Advertisement"
        elif artist and title:
            track_info = f"{title} - {artist}"
        else:
            track_info = title

        # Only update if no other player is playing
        current_playing = self.get_first_playing_player()
        if current_playing is None or current_playing.props.player_name == player.props.player_name:
            if track_info:
                if player.props.status == "Playing":
                    self.start_scrolling(track_info, player)
                else:
                    self.stop_scrolling()
                    self.write_output(track_info[:self.max_length], player)
            else:
                self.start_scrolling(self.default_text, None)
        else:
            logger.debug(f"Other player {current_playing.props.player_name} is playing, skipping")
            self.stop_scrolling()

    def on_player_appeared(self, _, player):
        logger.info(f"Player has appeared: {player.name}")
        if player.name in self.excluded_player:
            logger.debug("New player appeared, but it's in exclude player list, skipping")
            return
        if player is not None and (self.selected_player is None or player.name == self.selected_player):
            self.init_player(player)
        else:
            logger.debug("New player appeared, but it's not the selected player, skipping")

    def on_player_vanished(self, _, player):
        logger.info(f"Player {player.props.player_name} has vanished")
        if self.current_player and self.current_player.props.player_name == player.props.player_name:
            self.stop_scrolling()
        self.show_most_important_player()

def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-x", "--exclude", help="Comma-separated list of excluded players")
    parser.add_argument("--player")
    parser.add_argument("--enable-logging", action="store_true")
    return parser.parse_args()

def main():
    arguments = parse_arguments()
    if arguments.enable_logging:
        logfile = os.path.join(os.path.dirname(os.path.realpath(__file__)), "media-player.log")
        logging.basicConfig(filename=logfile, level=logging.DEBUG,
                            format="%(asctime)s %(name)s %(levelname)s:%(lineno)d %(message)s")
    logger.setLevel(max((3 - arguments.verbose) * 10, 0))
    logger.info("Creating player manager")
    if arguments.player:
        logger.info(f"Filtering for player: {arguments.player}")
    if arguments.exclude:
        logger.info(f"Exclude player {arguments.exclude}")
    player = PlayerManager(arguments.player, arguments.exclude)
    player.run()

if __name__ == "__main__":
    main()