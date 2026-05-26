#
# Copyright (c) 2021, 2022 Astroncia
# Copyright (c) 2023, 2024 Liya Astrova <liyaastrova@proton.me>
#
# This file is part of yuki-iptv.
#
# yuki-iptv is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# yuki-iptv is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with yuki-iptv. If not, see <https://www.gnu.org/licenses/>.
#
# The Font Awesome pictograms are licensed under the CC BY 4.0 License.
# Font Awesome Free 5.15.4 by @fontawesome - https://fontawesome.com
# License - https://creativecommons.org/licenses/by/4.0/
#
import sys
import os
import os.path
import time
import datetime
import math
import json
import locale
import uuid
import gettext
import logging
import signal
import atexit
import argparse
import subprocess
import re
import textwrap
import hashlib
import urllib
import urllib.parse
import threading
import traceback
import setproctitle
from pathlib import Path
from multiprocessing import Manager, active_children, get_context
from functools import partial
from unidecode import unidecode
from gi.repository import Gio, GLib
from yuki_iptv.qt import get_qt_library, show_exception
from yuki_iptv.epg import (
    worker,
    is_program_actual,
    load_epg_cache,
    save_epg_cache,
    exists_in_epg,
    get_epg,
)
from yuki_iptv.record import (
    record,
    record_return,
    stop_record,
    is_ffmpeg_recording,
    init_record,
    terminate_record_process,
    is_youtube_url,
)
from yuki_iptv.menubar import (
    init_yuki_iptv_menubar,
    init_menubar_player,
    populate_menubar,
    update_menubar,
    get_active_vf_filters,
    get_first_run,
    get_seq,
    reload_menubar_shortcuts,
)
from yuki_iptv.catchup import (
    get_catchup_url,
    parse_specifiers_now_url,
    format_url_clean,
    format_catchup_array,
)
from yuki_iptv.misc import (
    get_current_time,
    format_bytes,
    format_seconds,
    convert_size,
    async_gui_blocking_function,
    AUDIO_SAMPLE_FORMATS,
    BCOLOR,
    DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH,
    DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW,
    DOCKWIDGET_PLAYLIST_WIDTH,
    MAIN_WINDOW_TITLE,
    MPV_OPTIONS_LINK,
    stream_info,
    TVGUIDE_WIDTH,
    UPDATE_BR_INTERVAL,
    WINDOW_SIZE,
    YukiData,
)
from yuki_iptv.playlist import load_playlist
from yuki_iptv.channel_logos import channel_logos_worker, get_custom_channel_logo
from yuki_iptv.settings import parse_settings
from yuki_iptv.qt6compat import _exec
from yuki_iptv.playlist_editor import PlaylistEditor
from yuki_iptv.options import read_option, write_option
from yuki_iptv.keybinds import main_keybinds_internal, main_keybinds_default
from yuki_iptv.xdg import LOCAL_DIR, SAVE_FOLDER_DEFAULT
from yuki_iptv.mpv_opengl import MPVOpenGLWidget
from yuki_iptv.mpris import start_mpris, emit_mpris_change, mpris_seeked
from yuki_iptv.gui import YukiGUIClass
from thirdparty.xtream import XTream

os.chdir(os.path.dirname(os.path.abspath(__file__)))

if "PULSE_PROP" not in os.environ:
    os.environ["PULSE_PROP"] = "media.role=video"

parser = argparse.ArgumentParser(prog="yuki-iptv", description="yuki-iptv")
parser.add_argument("--version", action="store_true", help="Show version")
parser.add_argument(
    "--loglevel",
    action="store",
    help="Log level (CRITICAL, ERROR, WARNING, INFO, DEBUG) default: INFO",
)
parser.add_argument("URL", help="Playlist URL or file", nargs="?")
args1, _unparsed_args = parser.parse_known_args()

loglevel = args1.loglevel if args1.loglevel else "INFO"
numeric_level = getattr(logging, loglevel.upper(), None)
if not isinstance(numeric_level, int):
    raise ValueError("Invalid log level: %s" % loglevel)

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s: %(message)s",
    level=numeric_level,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("yuki-iptv")
mpv_logger = logging.getLogger("libmpv")

qt_library, QtWidgets, QtCore, QtGui, QShortcut, QtOpenGLWidgets = get_qt_library()

if "PyQt6" in sys.modules or "PyQt5" in sys.modules:
    Signal = QtCore.pyqtSignal
else:
    Signal = QtCore.Signal

if qt_library == "PyQt5":
    qt_icon_critical = 3
    qt_icon_warning = 2
    qt_icon_information = 1
else:
    qt_icon_critical = QtWidgets.QMessageBox.Icon.Critical
    qt_icon_warning = QtWidgets.QMessageBox.Icon.Warning
    qt_icon_information = QtWidgets.QMessageBox.Icon.Information

APP_VERSION = "__DEB_VERSION__"

setproctitle.setproctitle("yuki-iptv")
try:
    setproctitle.setthreadtitle("yuki-iptv")
except Exception:
    pass

# i18n start


class YukiLang:
    cache = {}


APP = "yuki-iptv"
LOCALE_DIR = str(Path(os.getcwd(), "..", "..", "share", "locale"))
locale.bindtextdomain(APP, LOCALE_DIR)
gettext.bindtextdomain(APP, LOCALE_DIR)
gettext.textdomain(APP)


def cached_gettext(gettext_str):
    if gettext_str not in YukiLang.cache:
        YukiLang.cache[gettext_str] = gettext.gettext(gettext_str)
    return YukiLang.cache[gettext_str]


_ = cached_gettext
# i18n end

if args1.version:
    print(f"{MAIN_WINDOW_TITLE} {APP_VERSION}")
    sys.exit(0)

Path(LOCAL_DIR).mkdir(parents=True, exist_ok=True)
Path(SAVE_FOLDER_DEFAULT).mkdir(parents=True, exist_ok=True)


# Used as a decorator to run things in the main loop, from another thread
def idle_function(func):
    def wrapper(*args):
        exInMainThread_partial(partial(func, *args))

    return wrapper


if __name__ == "__main__":

    def exit_handler(*args):
        try:
            logger.info("exit_handler called")
            try:
                if multiprocessing_manager:
                    multiprocessing_manager.shutdown()
            except Exception:
                pass
            for process_3 in active_children():
                try:
                    process_3.kill()
                except Exception:
                    try:
                        process_3.terminate()
                    except Exception:
                        pass
            stop_record()
            for rec_1 in sch_recordings:
                do_stop_record(rec_1)
            if YukiData.mpris_loop:
                YukiData.mpris_running = False
                YukiData.mpris_loop.quit()
            if YukiData.epg_thread_2:
                try:
                    YukiData.epg_thread_2.kill()
                except Exception:
                    try:
                        YukiData.epg_thread_2.terminate()
                    except Exception:
                        pass
            if multiprocessing_manager:
                multiprocessing_manager.shutdown()
            for process_3 in active_children():
                try:
                    process_3.kill()
                except Exception:
                    try:
                        process_3.terminate()
                    except Exception:
                        pass
            logger.info("exit_handler completed")
        except BaseException:
            pass

    atexit.register(exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)
    signal.signal(signal.SIGINT, exit_handler)

    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()

    setAppFusion = True
    try:
        if os.path.isfile(str(Path(LOCAL_DIR, "settings.json"))):
            with open(
                str(Path(LOCAL_DIR, "settings.json")), encoding="utf8"
            ) as settings_tmp:
                settings_tmp_json = json.loads(settings_tmp.read())
                if "styleredefoff" in settings_tmp_json:
                    setAppFusion = settings_tmp_json["styleredefoff"]
    except Exception:
        logger.warning("failed to read settings.json")

    try:
        if setAppFusion:
            app.setStyle("fusion")
        else:
            logger.info("fusion style turned OFF")
    except Exception:
        logger.warning('app.setStyle("fusion") FAILED')

    # This is necessary since PyQT stomps over the locale settings needed by libmpv.
    # This needs to happen after importing PyQT before
    # creating the first mpv.MPV instance.
    locale.setlocale(locale.LC_NUMERIC, "C")

    try:
        logger.info(f"Version: {APP_VERSION}")
        logger.info("Using Python " + sys.version.replace("\n", ""))
        logger.info(f"Qt library: {qt_library}")
        logger.info(f"Qt version: {QtCore.qVersion()}")
        QT_PLATFORM = ""
        try:
            logger.info(f"Qt platform: {app.platformName()}")
            QT_PLATFORM = f" ({app.platformName()})"
        except Exception:
            logger.warning("Failed to determine Qt platform!")
        logger.info("")

        if qt_library == "PyQt6":
            try:
                translator = QtCore.QTranslator()
                if not translator.load(
                    QtCore.QLocale.system(),
                    "qtbase",
                    "_",
                    os.path.abspath(
                        QtCore.QLibraryInfo.path(
                            QtCore.QLibraryInfo.LibraryPath.TranslationsPath
                        )
                    ),
                    ".qm",
                ):
                    logger.warning("System translations for Qt not loaded")
                app.installTranslator(translator)
            except Exception:
                logger.warning("Failed to set up system translations for Qt")
                logger.warning(traceback.format_exc())

        enable_libmpv_render_context = False  # TODO: for native Wayland

        multiprocessing_manager = Manager()
        YukiData.mp_manager_dict = multiprocessing_manager.dict()

        from thirdparty import mpv

        if not os.path.isfile(str(Path(LOCAL_DIR, "favplaylist.m3u"))):
            file01 = open(str(Path(LOCAL_DIR, "favplaylist.m3u")), "w", encoding="utf8")
            file01.write("#EXTM3U\n#EXTINF:-1,-\nhttp://255.255.255.255\n")
            file01.close()

        YukiData.channel_sets = {}
        YukiData.prog_ids = {}
        YukiData.epg_icons = {}

        def save_channel_sets():
            file2 = open(
                str(Path(LOCAL_DIR, "channelsettings.json")), "w", encoding="utf8"
            )
            file2.write(json.dumps(YukiData.channel_sets))
            file2.close()

        if not os.path.isfile(str(Path(LOCAL_DIR, "channelsettings.json"))):
            save_channel_sets()
        else:
            file1 = open(str(Path(LOCAL_DIR, "channelsettings.json")), encoding="utf8")
            YukiData.channel_sets = json.loads(file1.read())
            file1.close()

        YukiData.settings, settings_loaded = parse_settings()

        YukiData.favourite_sets = []

        def save_favourite_sets():
            favourite_sets_2 = {}
            if os.path.isfile(Path(LOCAL_DIR, "favouritechannels.json")):
                with open(
                    Path(LOCAL_DIR, "favouritechannels.json"), encoding="utf8"
                ) as fsetfile:
                    favourite_sets_2 = json.loads(fsetfile.read())
            if YukiData.settings["m3u"]:
                favourite_sets_2[YukiData.settings["m3u"]] = YukiData.favourite_sets
            file2 = open(
                Path(LOCAL_DIR, "favouritechannels.json"), "w", encoding="utf8"
            )
            file2.write(json.dumps(favourite_sets_2))
            file2.close()

        if not os.path.isfile(str(Path(LOCAL_DIR, "favouritechannels.json"))):
            save_favourite_sets()
        else:
            file1 = open(Path(LOCAL_DIR, "favouritechannels.json"), encoding="utf8")
            favourite_sets1 = json.loads(file1.read())
            if YukiData.settings["m3u"] in favourite_sets1:
                YukiData.favourite_sets = favourite_sets1[YukiData.settings["m3u"]]
            file1.close()

        YukiData.player_tracks = {}

        def save_player_tracks():
            player_tracks_2 = {}
            if os.path.isfile(Path(LOCAL_DIR, "tracks.json")):
                with open(
                    Path(LOCAL_DIR, "tracks.json"), encoding="utf8"
                ) as tracks_file0:
                    player_tracks_2 = json.loads(tracks_file0.read())
            if YukiData.settings["m3u"]:
                player_tracks_2[YukiData.settings["m3u"]] = YukiData.player_tracks
            tracks_file1 = open(Path(LOCAL_DIR, "tracks.json"), "w", encoding="utf8")
            tracks_file1.write(json.dumps(player_tracks_2))
            tracks_file1.close()

        if os.path.isfile(str(Path(LOCAL_DIR, "tracks.json"))):
            tracks_file = open(Path(LOCAL_DIR, "tracks.json"), encoding="utf8")
            player_tracks1 = json.loads(tracks_file.read())
            if YukiData.settings["m3u"] in player_tracks1:
                YukiData.player_tracks = player_tracks1[YukiData.settings["m3u"]]
            tracks_file.close()

        if YukiData.settings["hwaccel"]:
            logger.info("Hardware acceleration enabled")
        else:
            logger.info("Hardware acceleration disabled")

        try:
            from wand.image import Image  # noqa: F401
        except Exception:
            logger.warning(
                "Wand is not available! Falling back to Pillow."
                " Logos in svg and avif formats will not be available!"
            )

        # https://www.qt.io/blog/dark-mode-on-windows-11-with-qt-6.5#before-qt-65
        current_palette = QtGui.QPalette()
        is_dark_theme = (
            current_palette.color(QtGui.QPalette.ColorRole.WindowText).lightness()
            > current_palette.color(QtGui.QPalette.ColorRole.Window).lightness()
        )
        if is_dark_theme:
            logger.info("Detected dark window theme")
            YukiData.use_dark_icon_theme = True
        else:
            YukiData.use_dark_icon_theme = False

        if YukiData.settings["catchupenable"]:
            logger.info("Catchup enabled")
        else:
            logger.info("Catchup disabled")

        # URL override for command line
        if args1.URL:
            YukiData.settings["m3u"] = args1.URL
            YukiData.settings["epg"] = ""

        YukiData.tvguide_sets = {}

        YukiData.epg_thread_2 = None

        @idle_function
        def start_epg_hdd_animation(unused=None):
            try:
                YukiGUI.hdd_gif_label.setVisible(True)
            except Exception:
                pass

        @idle_function
        def stop_epg_hdd_animation(unused=None):
            try:
                YukiGUI.hdd_gif_label.setVisible(False)
            except Exception:
                pass

        @async_gui_blocking_function
        def save_tvguide_sets():
            try:
                start_epg_hdd_animation()
            except Exception:
                pass
            logger.info("Writing EPG cache...")
            YukiData.epg_thread_2 = get_context("spawn").Process(
                name="[yuki-iptv] save_epg_cache",
                target=save_epg_cache,
                daemon=True,
                args=(
                    YukiData.tvguide_sets,
                    YukiData.settings,
                    YukiData.prog_ids,
                    YukiData.epg_icons,
                ),
            )
            YukiData.epg_thread_2.start()
            YukiData.epg_thread_2.join()
            logger.info("Writing EPG cache done")
            try:
                stop_epg_hdd_animation()
            except Exception:
                pass

        YukiData.first_boot = False
        YukiData.epg_updating = False

        @idle_function
        def force_update_epg(unused=None):
            if os.path.exists(str(Path(LOCAL_DIR, "epg.cache"))):
                os.remove(str(Path(LOCAL_DIR, "epg.cache")))
            YukiData.use_local_tvguide = False
            if not YukiData.epg_updating:
                YukiData.first_boot = False

        YukiData.epg_update_allowed = True

        if YukiData.settings["donotupdateepg"]:
            YukiData.epg_update_allowed = False

        def force_update_epg_act():
            logger.info("Force update EPG triggered")
            YukiData.epg_update_allowed = True
            if YukiData.epg_failed:
                YukiData.epg_failed = False
            force_update_epg()

        YukiData.use_local_tvguide = True
        YukiData.epg_ready = False

        def mainwindow_isvisible():
            try:
                return win.isVisible()
            except Exception:
                return False

        @idle_function
        def update_epg_func_static_enable(unused=None):
            YukiData.state.setStaticYuki(True)
            YukiData.state.show()
            YukiData.static_text = _("Loading TV guide cache...")
            YukiData.state.setTextYuki("")
            YukiData.time_stop = time.time() + 3

        @idle_function
        def update_epg_func_static_disable(unused=None):
            YukiData.state.setStaticYuki(False)
            YukiData.state.hide()
            YukiData.state.setTextYuki("")
            YukiData.time_stop = time.time()

        @idle_function
        def btn_update_click(unused=None):
            YukiGUI.btn_update.click()

        @async_gui_blocking_function
        def update_epg_func():
            if YukiData.settings["nocacheepg"]:
                logger.info("No cache EPG active, deleting old EPG cache file")
                try:
                    if os.path.isfile(str(Path(LOCAL_DIR, "epg.cache"))):
                        os.remove(str(Path(LOCAL_DIR, "epg.cache")))
                except Exception:
                    pass
            tvguide_read_time = time.time()
            if os.path.isfile(str(Path(LOCAL_DIR, "epg.cache"))):
                logger.info("Reading cached TV guide...")

                update_epg_func_static_enable()

                # Loading epg.cache
                tvguide_json = (
                    get_context("spawn")
                    .Pool(1)
                    .apply(
                        load_epg_cache,
                        (
                            YukiData.settings["m3u"],
                            YukiData.settings["epg"],
                            YukiData.epg_ready,
                        ),
                    )
                )
                is_program_actual1 = False
                if tvguide_json:
                    if "tvguide_sets" in tvguide_json:
                        YukiData.tvguide_sets = tvguide_json["tvguide_sets"]
                    if "prog_ids" in tvguide_json:
                        YukiData.prog_ids = tvguide_json["prog_ids"]
                    if "epg_icons" in tvguide_json:
                        YukiData.epg_icons = tvguide_json["epg_icons"]
                    if "is_program_actual" in tvguide_json:
                        is_program_actual1 = tvguide_json["is_program_actual"]
                    if "programmes_1" in tvguide_json:
                        YukiData.programmes = tvguide_json["programmes_1"]
                    tvguide_json = None
                if not is_program_actual1:
                    logger.info("EPG cache expired, updating...")
                    YukiData.epg_ready = True
                    force_update_epg()
                YukiData.epg_ready = True

                update_epg_func_static_disable()

                logger.info(
                    "TV guide read done, took "
                    f"{round(time.time() - tvguide_read_time, 2)} seconds"
                )
                btn_update_click()
            else:
                logger.info("No EPG cache found")
                YukiData.epg_ready = True
                force_update_epg()

        if YukiData.use_dark_icon_theme:
            ICONS_FOLDER = str(
                Path("..", "..", "..", "share", "yuki-iptv", "icons_dark")
            )
        else:
            ICONS_FOLDER = str(Path("..", "..", "..", "share", "yuki-iptv", "icons"))

        YukiGUI = YukiGUIClass(
            _, ICONS_FOLDER, YukiData.use_dark_icon_theme, MPV_OPTIONS_LINK
        )

        channels = {}
        YukiData.programmes = {}

        playlist_editor = PlaylistEditor(
            _=_,
            icon=YukiGUI.main_icon,
            icons_folder=ICONS_FOLDER,
            settings=YukiData.settings,
        )

        def show_playlist_editor():
            if playlist_editor.isVisible():
                playlist_editor.hide()
            else:
                moveWindowToCenter(playlist_editor)
                playlist_editor.show()
                moveWindowToCenter(playlist_editor)

        save_folder = YukiData.settings["save_folder"]

        if not os.path.isdir(str(Path(save_folder))):
            try:
                Path(save_folder).mkdir(parents=True, exist_ok=True)
            except Exception:
                logger.warning("Failed to create save folder!")
                show_exception("Failed to create save folder!")
                save_folder = SAVE_FOLDER_DEFAULT
                if not os.path.isdir(str(Path(save_folder))):
                    Path(save_folder).mkdir(parents=True, exist_ok=True)

        if not os.access(save_folder, os.W_OK | os.X_OK):
            save_folder = SAVE_FOLDER_DEFAULT
            logger.warning(
                "Save folder is not writable (os.access), using default save folder"
            )
            show_exception(
                "Save folder is not writable (os.access), using default save folder"
            )

        if not YukiData.settings["scrrecnosubfolders"]:
            try:
                Path(save_folder, "screenshots").mkdir(parents=True, exist_ok=True)
                Path(save_folder, "recordings").mkdir(parents=True, exist_ok=True)
            except Exception:
                save_folder = SAVE_FOLDER_DEFAULT
                logger.warning(
                    "Save folder is not writable (subfolders), "
                    "using default save folder"
                )
                show_exception(
                    "Save folder is not writable (subfolders), "
                    "using default save folder"
                )
        else:
            if os.path.isdir(str(Path(save_folder, "screenshots"))):
                try:
                    os.rmdir(str(Path(save_folder, "screenshots")))
                except Exception:
                    pass
            if os.path.isdir(str(Path(save_folder, "recordings"))):
                try:
                    os.rmdir(str(Path(save_folder, "recordings")))
                except Exception:
                    pass

        def getArrayItem(arr_item):
            arr_item_ret = None
            if arr_item:
                if arr_item in YukiData.array:
                    arr_item_ret = YukiData.array[arr_item]
                elif arr_item in YukiData.movies:
                    arr_item_ret = YukiData.movies[arr_item]
                else:
                    try:
                        if " ::: " in arr_item:
                            arr_item_split = arr_item.split(" ::: ")
                            for season_name in YukiData.series[
                                arr_item_split[2]
                            ].seasons.keys():
                                season = YukiData.series[arr_item_split[2]].seasons[
                                    season_name
                                ]
                                if season.name == arr_item_split[1]:
                                    for episode_name in season.episodes.keys():
                                        episode = season.episodes[episode_name]
                                        if episode.title == arr_item_split[0]:
                                            arr_item_ret = {
                                                "title": episode.title,
                                                "tvg-name": "",
                                                "tvg-ID": "",
                                                "tvg-logo": "",
                                                "tvg-group": _("All channels"),
                                                "tvg-url": "",
                                                "catchup": "default",
                                                "catchup-source": "",
                                                "catchup-days": "7",
                                                "useragent": "",
                                                "referer": "",
                                                "url": episode.url,
                                            }
                                            break
                                    break
                    except Exception:
                        logger.warning("Exception in getArrayItem (series)")
                        logger.warning(traceback.format_exc())
            return arr_item_ret

        class EmptyClass:
            pass

        def log_xtream(*args):
            logger.info(" ".join([str(arg2) for arg2 in args]))

        def load_xtream(m3u_url):
            (
                _xtream_unused,
                xtream_username,
                xtream_password,
                xtream_url,
            ) = m3u_url.split("::::::::::::::")
            Path(LOCAL_DIR, "xtream").mkdir(parents=True, exist_ok=True)
            xtream_headers = {"User-Agent": YukiData.settings["ua"]}
            if YukiData.settings["referer"]:
                xtream_headers["Referer"] = YukiData.settings["referer"]
            try:
                xt = XTream(
                    log_xtream,
                    hashlib.sha512(
                        YukiData.settings["m3u"].encode("utf-8")
                    ).hexdigest(),
                    xtream_username,
                    xtream_password,
                    xtream_url,
                    headers=xtream_headers,
                    hide_adult_content=False,
                    cache_path="",
                )
            except Exception:
                exc = traceback.format_exc()
                logger.warning("XTream init failure")
                logger.warning(exc)
                msg3 = QtWidgets.QMessageBox(
                    qt_icon_warning,
                    _("Error"),
                    exc,
                    QtWidgets.QMessageBox.StandardButton.Ok,
                )
                msg3.exec()
                xt = EmptyClass()
                xt.auth_data = {}
            return xt, xtream_username, xtream_password, xtream_url

        YukiData.channel_sort = {}
        if os.path.isfile(str(Path(LOCAL_DIR, "sortchannels.json"))):
            with open(
                str(Path(LOCAL_DIR, "sortchannels.json")), encoding="utf8"
            ) as channel_sort_file1:
                channel_sort3 = json.loads(channel_sort_file1.read())
                if YukiData.settings["m3u"] in channel_sort3:
                    YukiData.channel_sort = channel_sort3[YukiData.settings["m3u"]]

        YukiData.array, array_sorted, groups, m3u_exists, xt, YukiData = load_playlist(
            _,
            YukiData.settings,
            YukiData,
            load_xtream,
            YukiData.channel_sets,
            YukiData.channel_sort,
        )

        try:
            if os.path.isfile(str(Path(LOCAL_DIR, "settings.json"))):
                settings_file2 = open(
                    str(Path(LOCAL_DIR, "settings.json")), encoding="utf8"
                )
                settings_file2_json = json.loads(settings_file2.read())
                settings_file2.close()
                if YukiData.settings["epg"] and not settings_file2_json["epg"]:
                    settings_file2_json["epg"] = YukiData.settings["epg"]
                    settings_file4 = open(
                        str(Path(LOCAL_DIR, "settings.json")), "w", encoding="utf8"
                    )
                    settings_file4.write(json.dumps(settings_file2_json))
                    settings_file4.close()
        except Exception:
            pass

        def sigint_handler(*args):
            """Handler for the SIGINT signal."""
            if YukiData.mpris_loop:
                YukiData.mpris_running = False
                YukiData.mpris_loop.quit()
            app.quit()

        signal.signal(signal.SIGINT, sigint_handler)
        signal.signal(signal.SIGTERM, sigint_handler)

        YukiGUI.create_windows()

        def resettodefaults_btn_clicked():
            resettodefaults_btn_clicked_msg = QtWidgets.QMessageBox.question(
                None,
                MAIN_WINDOW_TITLE,
                _("Are you sure?"),
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.Yes,
            )
            if (
                resettodefaults_btn_clicked_msg
                == QtWidgets.QMessageBox.StandardButton.Yes
            ):
                logger.info("Restoring default keybinds")
                YukiData.main_keybinds = main_keybinds_default.copy()
                YukiGUI.shortcuts_table.setRowCount(len(YukiData.main_keybinds))
                keybind_i = -1
                for keybind in YukiData.main_keybinds:
                    keybind_i += 1
                    YukiGUI.shortcuts_table.setItem(
                        keybind_i,
                        0,
                        get_widget_item(main_keybinds_translations[keybind]),
                    )
                    if isinstance(YukiData.main_keybinds[keybind], str):
                        keybind_str = YukiData.main_keybinds[keybind]
                    else:
                        keybind_str = QtGui.QKeySequence(
                            YukiData.main_keybinds[keybind]
                        ).toString()
                    kbd_widget = get_widget_item(keybind_str)
                    kbd_widget.setToolTip(_("Double click to change"))
                    YukiGUI.shortcuts_table.setItem(keybind_i, 1, kbd_widget)
                YukiGUI.shortcuts_table.resizeColumnsToContents()
                hotkeys_file_1 = open(
                    str(Path(LOCAL_DIR, "hotkeys.json")), "w", encoding="utf8"
                )
                hotkeys_file_1.write(
                    json.dumps({"current_profile": {"keys": YukiData.main_keybinds}})
                )
                hotkeys_file_1.close()
                reload_keybinds()

        YukiGUI.resettodefaults_btn.clicked.connect(resettodefaults_btn_clicked)

        class KeySequenceEdit(QtWidgets.QKeySequenceEdit):
            def keyPressEvent(self, event):
                super().keyPressEvent(event)
                self.setKeySequence(QtGui.QKeySequence(self.keySequence()))

        keyseq = KeySequenceEdit()

        def keyseq_ok_clicked():
            if YukiData.selected_shortcut_row != -1:
                sel_keyseq = keyseq.keySequence().toString()
                search_value = YukiGUI.shortcuts_table.item(
                    YukiData.selected_shortcut_row, 0
                ).text()
                shortcut_taken = False
                for sci1 in range(YukiGUI.shortcuts_table.rowCount()):
                    if sci1 != YukiData.selected_shortcut_row:
                        if YukiGUI.shortcuts_table.item(sci1, 1).text() == sel_keyseq:
                            shortcut_taken = True
                forbidden_hotkeys = [
                    "Return",
                    "Key.Key_MediaNext",
                    "Key.Key_MediaPause",
                    "Key.Key_MediaPlay",
                    "Key.Key_MediaPrevious",
                    "Key.Key_MediaRecord",
                    "Key.Key_MediaStop",
                    "Key.Key_MediaTogglePlayPause",
                    "Key.Key_Play",
                    "Key.Key_Stop",
                    "Key.Key_VolumeDown",
                    "Key.Key_VolumeMute",
                    "Key.Key_VolumeUp",
                ]
                if sel_keyseq in forbidden_hotkeys:
                    shortcut_taken = True
                if not shortcut_taken:
                    YukiGUI.shortcuts_table.item(
                        YukiData.selected_shortcut_row, 1
                    ).setText(sel_keyseq)
                    for name55, value55 in main_keybinds_translations.items():
                        if value55 == search_value:
                            YukiData.main_keybinds[name55] = sel_keyseq
                            hotkeys_file = open(
                                str(Path(LOCAL_DIR, "hotkeys.json")),
                                "w",
                                encoding="utf8",
                            )
                            hotkeys_file.write(
                                json.dumps(
                                    {
                                        "current_profile": {
                                            "keys": YukiData.main_keybinds
                                        }
                                    }
                                )
                            )
                            hotkeys_file.close()
                            reload_keybinds()
                    YukiGUI.shortcuts_win_2.hide()
                else:
                    msg_shortcut_taken = QtWidgets.QMessageBox(
                        qt_icon_warning,
                        MAIN_WINDOW_TITLE,
                        _("Shortcut already used"),
                        QtWidgets.QMessageBox.StandardButton.Ok,
                    )
                    msg_shortcut_taken.exec()

        class StreaminfoWin(QtWidgets.QMainWindow):
            def showEvent(self, event4):
                YukiData.streaminfo_win_visible = True

            def hideEvent(self, event4):
                YukiData.streaminfo_win_visible = False

        def epg_win_checkbox_changed():
            YukiGUI.tvguide_lbl_2.verticalScrollBar().setSliderPosition(
                YukiGUI.tvguide_lbl_2.verticalScrollBar().minimum()
            )
            YukiGUI.tvguide_lbl_2.setText(_("No TV guide for channel"))
            try:
                ch_3 = YukiGUI.epg_win_checkbox.currentText()
                ch_3_guide = update_tvguide(
                    ch_3, True, date_selected=YukiData.epg_selected_date
                ).replace("!@#$%^^&*(", "\n")
                ch_3_guide = ch_3_guide.replace("\n", "<br>").replace("<br>", "", 1)
                if ch_3_guide.strip():
                    YukiGUI.tvguide_lbl_2.setText(ch_3_guide)
                else:
                    YukiGUI.tvguide_lbl_2.setText(_("No TV guide for channel"))
            except Exception:
                logger.warning("Exception in epg_win_checkbox_changed")

        def showonlychplaylist_chk_clk():
            update_tvguide_2()

        def tvguide_channelfilter_do():
            try:
                filter_txt3 = YukiGUI.tvguidechannelfilter.text()
            except Exception:
                filter_txt3 = ""
            for item6 in range(YukiGUI.epg_win_checkbox.count()):
                if (
                    unidecode(filter_txt3).lower().strip()
                    in unidecode(YukiGUI.epg_win_checkbox.itemText(item6))
                    .lower()
                    .strip()
                ):
                    YukiGUI.epg_win_checkbox.view().setRowHidden(item6, False)
                else:
                    YukiGUI.epg_win_checkbox.view().setRowHidden(item6, True)

        def epg_date_changed(epg_date):
            YukiData.epg_selected_date = datetime.datetime.fromordinal(
                epg_date.toPyDate().toordinal()
            ).timestamp()
            epg_win_checkbox_changed()

        YukiData.archive_epg = None

        def do_open_archive(link):
            if "#__archive__" in link:
                archive_json = json.loads(
                    urllib.parse.unquote_plus(link.split("#__archive__")[1])
                )
                arr1 = getArrayItem(archive_json[0])
                arr1 = format_catchup_array(arr1)

                channel_url = getArrayItem(archive_json[0])["url"]
                start_time = archive_json[1]
                end_time = archive_json[2]
                prog_index = archive_json[3]

                if "#__rewind__" not in link:
                    YukiData.archive_epg = archive_json

                catchup_id = ""
                try:
                    match1 = archive_json[0].lower()
                    try:
                        match1 = YukiData.prog_match_arr[match1]
                    except Exception:
                        pass
                    if exists_in_epg(match1, YukiData.programmes):
                        if get_epg(YukiData.programmes, match1):
                            if (
                                "catchup-id"
                                in get_epg(YukiData.programmes, match1)[int(prog_index)]
                            ):
                                catchup_id = get_epg(YukiData.programmes, match1)[
                                    int(prog_index)
                                ]["catchup-id"]
                except Exception:
                    logger.warning("do_open_archive / catchup_id parsing failed")
                    logger.warning(traceback.format_exc())

                arr2 = arr1

                if YukiData.is_xtream:
                    arr2["catchup"] = "xc"

                play_url = get_catchup_url(
                    channel_url, arr2, start_time, end_time, catchup_id
                )

                itemClicked_event(
                    archive_json[0], play_url, True, is_rewind=(len(archive_json) == 5)
                )
                setChannelText("({}) {}".format(_("Archive"), archive_json[0]), True)
                YukiGUI.progress.hide()
                YukiGUI.start_label.setText("")
                YukiGUI.start_label.hide()
                YukiGUI.stop_label.setText("")
                YukiGUI.stop_label.hide()
                YukiGUI.epg_win.hide()

                return False

        class playlists_data:
            pass

        playlists_data.oldName = ""

        def create_playlist_item_widget(name3):
            name3_n = name3
            if name3_n in YukiData.xtream_expiration_list:
                name3_n = (
                    name3_n
                    + "\n("
                    + _("Expiration date")
                    + ": "
                    + YukiData.xtream_expiration_list[name3_n.split("\n")[0]]
                    + ")"
                )
            playlist_item_widget = QtWidgets.QListWidgetItem(
                YukiGUI.tv_icon_small, name3_n
            )
            playlist_item_widget.setData(QtCore.Qt.ItemDataRole.UserRole, name3)
            return playlist_item_widget

        def get_xtream_expiration_date(xt2):
            xtream_exp_date = _("Unknown")
            try:
                xtream_exp_date = datetime.datetime.fromtimestamp(
                    int(xt2.auth_data["user_info"]["exp_date"])
                ).strftime("%d.%m.%Y %H:%M:%S")
            except Exception:
                try:
                    xtream_exp_date = str(xt2.auth_data["user_info"]["exp_date"])
                except Exception:
                    pass
            return xtream_exp_date

        @idle_function
        def show_xtream_playlists_expiration_pt2(unused=None):
            try:
                for i10 in range(0, YukiGUI.playlists_list.count()):
                    if (
                        YukiGUI.playlists_list.item(i10).data(
                            QtCore.Qt.ItemDataRole.UserRole
                        )
                        in YukiData.xtream_expiration_list
                    ):
                        YukiGUI.playlists_list.item(i10).setText(
                            YukiGUI.playlists_list.item(i10).text().split("\n")[0]
                            + "\n("
                            + _("Expiration date")
                            + ": "
                            + YukiData.xtream_expiration_list[
                                YukiGUI.playlists_list.item(i10).text().split("\n")[0]
                            ]
                            + ")"
                        )
            except Exception:
                logger.warning("exception in show_xtream_playlists_expiration_pt2")
                logger.warning(traceback.format_exc())

        @idle_function
        def xtream_expiration_show_loading(unused=None):
            try:
                for i10 in range(0, YukiGUI.playlists_list.count()):
                    i10_name = YukiGUI.playlists_list.item(i10).data(
                        QtCore.Qt.ItemDataRole.UserRole
                    )
                    if playlists_data.playlists_used[i10_name]["m3u"].startswith(
                        "XTREAM::::::::::::::"
                    ):
                        YukiGUI.playlists_list.item(i10).setIcon(
                            YukiGUI.loading_icon_small
                        )
            except Exception:
                logger.warning("exception in xtream_expiration_show_loading")
                logger.warning(traceback.format_exc())

        @idle_function
        def xtream_expiration_hide_loading(unused=None):
            try:
                for i10 in range(0, YukiGUI.playlists_list.count()):
                    i10_name = YukiGUI.playlists_list.item(i10).data(
                        QtCore.Qt.ItemDataRole.UserRole
                    )
                    if playlists_data.playlists_used[i10_name]["m3u"].startswith(
                        "XTREAM::::::::::::::"
                    ):
                        YukiGUI.playlists_list.item(i10).setIcon(YukiGUI.tv_icon_small)
            except Exception:
                logger.warning("exception in xtream_expiration_hide_loading")
                logger.warning(traceback.format_exc())

        @async_gui_blocking_function
        def show_xtream_playlists_expiration(unused=None):
            try:
                if not YukiData.xtream_list_lock:
                    YukiData.xtream_list_lock = True
                    xtream_list = set()
                    for i8 in playlists_data.playlists_used:
                        if playlists_data.playlists_used[i8]["m3u"].startswith(
                            "XTREAM::::::::::::::"
                        ):
                            xtream_list.add(
                                i8
                                + ":^:^:^:^:^:^:^:^:^:^:"
                                + playlists_data.playlists_used[i8]["m3u"]
                            )
                    if xtream_list != YukiData.xtream_list_old:
                        YukiData.xtream_list_old = xtream_list
                        xtream_expiration_show_loading()
                        expiration_list = {}
                        for i9 in xtream_list:
                            (
                                xt2,
                                _xtream_username,
                                _xtream_password,
                                _xtream_url,
                            ) = load_xtream(
                                playlists_data.playlists_used[
                                    i9.split(":^:^:^:^:^:^:^:^:^:^:")[0]
                                ]["m3u"]
                            )
                            expiration_list[
                                i9.split(":^:^:^:^:^:^:^:^:^:^:")[0]
                            ] = get_xtream_expiration_date(xt2)
                        YukiData.xtream_expiration_list = expiration_list
                        xtream_expiration_hide_loading()
                        show_xtream_playlists_expiration_pt2()
                    YukiData.xtream_list_lock = False
            except Exception:
                logger.warning("Exception in show_xtream_playlists_expiration")
                logger.warning(traceback.format_exc())

        def playlists_win_save():
            if YukiGUI.m3u_edit_1.text():
                channel_text_prov = YukiGUI.name_edit_1.text()
                if channel_text_prov:
                    if playlists_data.oldName == "":
                        YukiGUI.playlists_list.addItem(
                            create_playlist_item_widget(channel_text_prov)
                        )
                    else:
                        if channel_text_prov != playlists_data.oldName:
                            for i6 in range(0, YukiGUI.playlists_list.count()):
                                if (
                                    YukiGUI.playlists_list.item(i6).data(
                                        QtCore.Qt.ItemDataRole.UserRole
                                    )
                                    == playlists_data.oldName
                                ):
                                    YukiGUI.playlists_list.item(i6).setText(
                                        channel_text_prov
                                    )
                                    YukiGUI.playlists_list.item(i6).setData(
                                        QtCore.Qt.ItemDataRole.UserRole,
                                        channel_text_prov,
                                    )
                                    break
                            playlists_data.playlists_used.pop(playlists_data.oldName)
                    playlists_data.playlists_used[channel_text_prov] = {
                        "m3u": YukiGUI.m3u_edit_1.text().strip(),
                        "epg": YukiGUI.epg_edit_1.text().strip(),
                        "epgoffset": YukiGUI.soffset_1.value(),
                    }
                    playlists_save_json()
                    YukiGUI.playlists_win_edit.hide()
                    show_xtream_playlists_expiration()
                else:
                    noemptyname_msg = QtWidgets.QMessageBox(
                        qt_icon_warning,
                        MAIN_WINDOW_TITLE,
                        _("Name should not be empty!"),
                        QtWidgets.QMessageBox.StandardButton.Ok,
                    )
                    noemptyname_msg.exec()
            else:
                nourlset_msg = QtWidgets.QMessageBox(
                    qt_icon_warning,
                    MAIN_WINDOW_TITLE,
                    _("URL not specified!"),
                    QtWidgets.QMessageBox.StandardButton.Ok,
                )
                nourlset_msg.exec()

        def m3u_file_1_clicked():
            fname_1 = QtWidgets.QFileDialog.getOpenFileName(
                YukiGUI.playlists_win_edit,
                _("Select playlist"),
                home_folder,
                "All Files (*);;M3U (*.m3u *.m3u8);;XSPF (*.xspf)",
            )[0]
            if fname_1:
                YukiGUI.m3u_edit_1.setText(fname_1)

        def epg_file_1_clicked():
            fname_2 = QtWidgets.QFileDialog.getOpenFileName(
                YukiGUI.playlists_win_edit,
                _("Select EPG file"),
                home_folder,
                "All Files (*);;XMLTV (*.xml *.xml.gz *.xml.xz);;JTV (*.zip)",
            )[0]
            if fname_2:
                YukiGUI.epg_edit_1.setText(fname_2)

        def lo_xtream_select():
            xtream_select()

        def esw_input_edit():
            esw_input_text = YukiGUI.esw_input.text().lower()
            for est_w in range(0, YukiGUI.esw_select.count()):
                if (
                    YukiGUI.esw_select.item(est_w)
                    .text()
                    .lower()
                    .startswith(esw_input_text)
                ):
                    YukiGUI.esw_select.item(est_w).setHidden(False)
                else:
                    YukiGUI.esw_select.item(est_w).setHidden(True)

        def esw_select_clicked(item1):
            YukiGUI.epg_select_win.hide()
            if item1.text():
                YukiGUI.epgname_lbl.setText(item1.text())
            else:
                YukiGUI.epgname_lbl.setText(_("Default"))

        def ext_open_btn_clicked():
            write_option("extplayer", YukiGUI.ext_player_txt.text().strip())
            YukiGUI.ext_win.close()
            try:
                subprocess.Popen(
                    YukiGUI.ext_player_txt.text().strip().split(" ")
                    + [getArrayItem(YukiData.item_selected)["url"]]
                )
            except Exception:
                logger.warning("Failed to open external player!")
                logger.warning(traceback.format_exc())
                show_exception(
                    traceback.format_exc(), _("Failed to open external player!")
                )

        YukiGUI.create4(keyseq, StreaminfoWin, ICONS_FOLDER)

        YukiData.epg_selected_date = datetime.datetime.fromordinal(
            datetime.date.today().toordinal()
        ).timestamp()

        YukiGUI.keyseq_cancel.clicked.connect(YukiGUI.shortcuts_win_2.hide)
        YukiGUI.keyseq_ok.clicked.connect(keyseq_ok_clicked)
        YukiGUI.tvguidechannelfiltersearch.clicked.connect(tvguide_channelfilter_do)
        YukiGUI.tvguidechannelfilter.returnPressed.connect(tvguide_channelfilter_do)
        YukiGUI.showonlychplaylist_chk.clicked.connect(showonlychplaylist_chk_clk)
        YukiGUI.epg_win_checkbox.currentIndexChanged.connect(epg_win_checkbox_changed)
        YukiGUI.epg_select_date.activated.connect(epg_date_changed)
        YukiGUI.epg_select_date.clicked.connect(epg_date_changed)
        YukiGUI.tvguide_lbl_2.label.linkActivated.connect(do_open_archive)
        YukiGUI.m3u_file_1.clicked.connect(m3u_file_1_clicked)
        YukiGUI.epg_file_1.clicked.connect(epg_file_1_clicked)
        YukiGUI.save_btn_1.clicked.connect(playlists_win_save)
        YukiGUI.xtream_btn_1.clicked.connect(lo_xtream_select)
        YukiGUI.esw_button.clicked.connect(esw_input_edit)
        YukiGUI.esw_select.itemDoubleClicked.connect(esw_select_clicked)
        YukiGUI.ext_open_btn.clicked.connect(ext_open_btn_clicked)

        extplayer = read_option("extplayer")
        if extplayer is None:
            extplayer = "mpv"
        YukiGUI.ext_player_txt.setText(extplayer)

        playlists_saved = {}

        if os.path.isfile(str(Path(LOCAL_DIR, "playlists.json"))):
            playlists_json = open(
                str(Path(LOCAL_DIR, "playlists.json")), encoding="utf8"
            )
            playlists_saved = json.loads(playlists_json.read())
            playlists_json.close()

        def playlists_favourites_do():
            YukiGUI.playlists_win.close()
            YukiGUI.m3u = str(Path(LOCAL_DIR, "favplaylist.m3u"))
            YukiGUI.epg = ""
            save_settings()

        YukiGUI.playlists_favourites.clicked.connect(playlists_favourites_do)

        def playlists_json_save(playlists_save0=None):
            if not playlists_save0:
                playlists_save0 = playlists_saved
            playlists_json1 = open(
                str(Path(LOCAL_DIR, "playlists.json")), "w", encoding="utf8"
            )
            playlists_json1.write(json.dumps(playlists_save0))
            playlists_json1.close()

        YukiData.time_stop = 0

        def moveWindowToCenter(win_arg, force=False):
            used_screen = QtWidgets.QApplication.primaryScreen()
            if not force:
                try:
                    used_screen = win.screen()
                except Exception:
                    pass
            qr0 = win_arg.frameGeometry()
            qr0.moveCenter(QtGui.QScreen.availableGeometry(used_screen).center())
            win_arg.move(qr0.topLeft())

        qr = YukiGUI.settings_win.frameGeometry()
        qr.moveCenter(
            QtGui.QScreen.availableGeometry(
                QtWidgets.QApplication.primaryScreen()
            ).center()
        )
        settings_win_l = qr.topLeft()
        origY = settings_win_l.y() - 150
        settings_win_l.setY(origY)
        YukiGUI.settings_win.move(qr.topLeft())

        moveWindowToCenter(YukiGUI.epg_win)

        YukiData.ffmpeg_processes = []

        init_record(show_exception, YukiData.ffmpeg_processes)

        def convert_time(times_1):
            yr = time.strftime("%Y", time.localtime())
            yr = yr[0] + yr[1]
            times_1_sp = times_1.split(" ")
            times_1_sp_0 = times_1_sp[0].split(".")
            times_1_sp_0[2] = yr + times_1_sp_0[2]
            times_1_sp[0] = ".".join(times_1_sp_0)
            return " ".join(times_1_sp)

        def programme_clicked(item):
            times = item.text().split("\n")[0]
            start_time = convert_time(times.split(" - ")[0])
            end_time = convert_time(times.split(" - ")[1])
            YukiGUI.starttime_w.setDateTime(
                QtCore.QDateTime.fromString(start_time, "d.M.yyyy hh:mm")
            )
            YukiGUI.endtime_w.setDateTime(
                QtCore.QDateTime.fromString(end_time, "d.M.yyyy hh:mm")
            )

        def addrecord_clicked():
            selected_channel = YukiGUI.choosechannel_ch.currentText()
            start_time_r = (
                YukiGUI.starttime_w.dateTime().toPyDateTime().strftime("%d.%m.%y %H:%M")
            )
            end_time_r = (
                YukiGUI.endtime_w.dateTime().toPyDateTime().strftime("%d.%m.%y %H:%M")
            )
            YukiGUI.schedulers.addItem(
                _("Channel") + ": " + selected_channel + "\n"
                "{}: ".format(_("Start record time")) + start_time_r + "\n"
                "{}: ".format(_("End record time")) + end_time_r + "\n"
            )

        sch_recordings = {}

        def do_start_record(name1):
            ch_name = name1.split("_")[0]
            ch = ch_name.replace(" ", "_")
            for char in FORBIDDEN_CHARS:
                ch = ch.replace(char, "")
            cur_time = datetime.datetime.now().strftime("%d%m%Y_%H%M%S")
            record_url = getArrayItem(ch_name)["url"]
            record_format = ".ts"
            if is_youtube_url(record_url):
                record_format = ".mkv"
            if not YukiData.settings["scrrecnosubfolders"]:
                out_file = str(
                    Path(
                        save_folder,
                        "recordings",
                        "recording_-_" + cur_time + "_-_" + ch + record_format,
                    )
                )
            else:
                out_file = str(
                    Path(
                        save_folder,
                        "recording_-_" + cur_time + "_-_" + ch + record_format,
                    )
                )
            return [
                record_return(
                    record_url,
                    out_file,
                    ch_name,
                    f"Referer: {YukiData.settings['referer']}",
                    get_ua_ref_for_channel,
                ),
                time.time(),
                out_file,
                ch_name,
            ]

        def do_stop_record(name2):
            if name2 in sch_recordings:
                ffmpeg_process = sch_recordings[name2][0]
                if ffmpeg_process:
                    terminate_record_process(ffmpeg_process)

        YukiData.recViaScheduler = False

        @idle_function
        def record_post_action_after(unused=None):
            logger.info("Record via scheduler ended, executing post-action...")
            # 0 - nothing to do
            if YukiGUI.praction_choose.currentIndex() == 1:  # 1 - Press Stop
                mpv_stop()

        @async_gui_blocking_function
        def record_post_action():
            while True:
                if is_recording_func() is True:
                    break
                time.sleep(1)
            record_post_action_after()

        def record_timer_2():
            try:
                activerec_list_value = (
                    YukiGUI.activerec_list.verticalScrollBar().value()
                )
                YukiGUI.activerec_list.clear()
                for sch0 in sch_recordings:
                    counted_time0 = format_seconds(
                        time.time() - sch_recordings[sch0][1]
                    )
                    channel_name0 = sch_recordings[sch0][3]
                    file_name0 = sch_recordings[sch0][2]
                    file_size0 = "WAITING"
                    if os.path.isfile(file_name0):
                        file_size0 = convert_size(os.path.getsize(file_name0))
                    YukiGUI.activerec_list.addItem(
                        channel_name0 + "\n" + counted_time0 + " " + file_size0
                    )
                YukiGUI.activerec_list.verticalScrollBar().setValue(
                    activerec_list_value
                )
                pl_text = "REC / " + _("Scheduler")
                if YukiGUI.activerec_list.count() != 0:
                    YukiData.recViaScheduler = True
                    YukiGUI.lbl2.setText(pl_text)
                    YukiGUI.lbl2.show()
                else:
                    if YukiData.recViaScheduler:
                        logger.info(
                            "Record via scheduler ended, waiting"
                            " for ffmpeg process completion..."
                        )
                        record_post_action()
                    YukiData.recViaScheduler = False
                    if YukiGUI.lbl2.text() == pl_text:
                        YukiGUI.lbl2.hide()
            except Exception:
                pass

        YukiData.is_recording_old = False

        @idle_function
        def set_record_icon(unused=None):
            YukiGUI.btn_record.setIcon(YukiGUI.record_icon)

        @idle_function
        def set_record_stop_icon(unused=None):
            YukiGUI.btn_record.setIcon(YukiGUI.record_stop_icon)

        def record_timer():
            try:
                if YukiData.is_recording != YukiData.is_recording_old:
                    YukiData.is_recording_old = YukiData.is_recording
                    if YukiData.is_recording:
                        set_record_stop_icon()
                    else:
                        set_record_icon()
                status = _("No planned recordings")
                sch_items = [
                    str(YukiGUI.schedulers.item(i1).text())
                    for i1 in range(YukiGUI.schedulers.count())
                ]
                i3 = -1
                for sch_item in sch_items:
                    i3 += 1
                    status = _("Waiting for record")
                    sch_item = [i2.split(": ")[1] for i2 in sch_item.split("\n") if i2]
                    channel_name_rec = sch_item[0]
                    current_time = time.strftime("%d.%m.%y %H:%M", time.localtime())
                    start_time_1 = sch_item[1]
                    end_time_1 = sch_item[2]
                    array_name = (
                        str(channel_name_rec)
                        + "_"
                        + str(start_time_1)
                        + "_"
                        + str(end_time_1)
                    )
                    if start_time_1 == current_time:
                        if array_name not in sch_recordings:
                            st_planned = (
                                "Starting planned record"
                                + " (start_time='{}' end_time='{}' channel='{}')"
                            )
                            logger.info(
                                st_planned.format(
                                    start_time_1, end_time_1, channel_name_rec
                                )
                            )
                            sch_recordings[array_name] = do_start_record(array_name)
                            YukiData.ffmpeg_processes.append(sch_recordings[array_name])
                    if end_time_1 == current_time:
                        if array_name in sch_recordings:
                            YukiGUI.schedulers.takeItem(i3)
                            stop_planned = (
                                "Stopping planned record"
                                + " (start_time='{}' end_time='{}' channel='{}')"
                            )
                            logger.info(
                                stop_planned.format(
                                    start_time_1, end_time_1, channel_name_rec
                                )
                            )
                            do_stop_record(array_name)
                            sch_recordings.pop(array_name)
                    if sch_recordings:
                        status = _("Recording")
                YukiGUI.statusrec_lbl.setText("{}: {}".format(_("Status"), status))
            except Exception:
                pass

        def delrecord_clicked():
            schCurrentRow = YukiGUI.schedulers.currentRow()
            if schCurrentRow != -1:
                sch_index = "_".join(
                    [
                        xs.split(": ")[1]
                        for xs in YukiGUI.schedulers.item(schCurrentRow)
                        .text()
                        .split("\n")
                        if xs
                    ]
                )
                YukiGUI.schedulers.takeItem(schCurrentRow)
                if sch_index in sch_recordings:
                    do_stop_record(sch_index)
                    sch_recordings.pop(sch_index)

        def scheduler_channelfilter_do():
            try:
                filter_txt2 = YukiGUI.schedulerchannelfilter.text()
            except Exception:
                filter_txt2 = ""
            for item5 in range(YukiGUI.choosechannel_ch.count()):
                if (
                    unidecode(filter_txt2).lower().strip()
                    in unidecode(YukiGUI.choosechannel_ch.itemText(item5))
                    .lower()
                    .strip()
                ):
                    YukiGUI.choosechannel_ch.view().setRowHidden(item5, False)
                else:
                    YukiGUI.choosechannel_ch.view().setRowHidden(item5, True)

        YukiGUI.create_scheduler_widgets(get_current_time())

        def save_sort():
            YukiData.channel_sort = [
                YukiGUI.sort_list.item(z0).text()
                for z0 in range(YukiGUI.sort_list.count())
            ]
            channel_sort2 = {}
            if os.path.isfile(Path(LOCAL_DIR, "sortchannels.json")):
                with open(
                    Path(LOCAL_DIR, "sortchannels.json"), encoding="utf8"
                ) as file5:
                    channel_sort2 = json.loads(file5.read())
            channel_sort2[YukiData.settings["m3u"]] = YukiData.channel_sort
            with open(
                Path(LOCAL_DIR, "sortchannels.json"), "w", encoding="utf8"
            ) as channel_sort_file:
                channel_sort_file.write(json.dumps(channel_sort2))
            YukiGUI.sort_win.hide()

        YukiGUI.create_sort_widgets()
        YukiGUI.save_sort_btn.clicked.connect(save_sort)

        YukiGUI.tvguide_sch.itemClicked.connect(programme_clicked)
        YukiGUI.addrecord_btn.clicked.connect(addrecord_clicked)
        YukiGUI.delrecord_btn.clicked.connect(delrecord_clicked)
        YukiGUI.schedulerchannelfiltersearch.clicked.connect(scheduler_channelfilter_do)
        YukiGUI.schedulerchannelfilter.returnPressed.connect(scheduler_channelfilter_do)

        home_folder = ""
        try:
            home_folder = os.environ["HOME"]
        except Exception:
            pass

        def save_folder_select():
            folder_name = QtWidgets.QFileDialog.getExistingDirectory(
                YukiGUI.settings_win,
                _("Select folder for recordings and screenshots"),
                options=QtWidgets.QFileDialog.Option.ShowDirsOnly,
            )
            if folder_name:
                YukiGUI.sfld.setText(folder_name)

        # Channel settings window
        def epgname_btn_action():
            prog_ids_0 = []
            for x0 in YukiData.prog_ids:
                for x1 in YukiData.prog_ids[x0]:
                    if x1 not in prog_ids_0:
                        prog_ids_0.append(x1)
            YukiGUI.esw_select.clear()
            YukiGUI.esw_select.addItem("")
            for prog_ids_0_dat in prog_ids_0:
                YukiGUI.esw_select.addItem(prog_ids_0_dat)
            esw_input_edit()
            moveWindowToCenter(YukiGUI.epg_select_win)
            YukiGUI.epg_select_win.show()

        YukiGUI.epgname_btn.clicked.connect(epgname_btn_action)

        def_user_agent = YukiData.settings["ua"]
        logger.info(f"Default user agent: {def_user_agent}")
        if YukiData.settings["referer"]:
            logger.info(f"Default HTTP referer: {YukiData.settings['referer']}")
        else:
            logger.info("Default HTTP referer: (empty)")

        YukiData.bitrate_failed = False

        def on_bitrate(prop, bitrate):
            try:
                if not bitrate or prop not in ["video-bitrate", "audio-bitrate"]:
                    return

                if _("Average Bitrate") in stream_info.video_properties:
                    if _("Average Bitrate") in stream_info.audio_properties:
                        if not YukiData.streaminfo_win_visible:
                            return

                rates = {
                    "video": stream_info.video_bitrates,
                    "audio": stream_info.audio_bitrates,
                }
                rate = "video"
                if prop == "audio-bitrate":
                    rate = "audio"

                rates[rate].append(int(bitrate) / 1000.0)
                rates[rate] = rates[rate][-30:]
                br = sum(rates[rate]) / float(len(rates[rate]))

                if rate == "video":
                    stream_info.video_properties[_("General")][_("Average Bitrate")] = (
                        "%.f " + _("kbps")
                    ) % br
                else:
                    stream_info.audio_properties[_("General")][_("Average Bitrate")] = (
                        "%.f " + _("kbps")
                    ) % br
            except Exception:
                if not YukiData.bitrate_failed:
                    YukiData.bitrate_failed = True
                    logger.warning("on_bitrate FAILED with exception!")
                    logger.warning(traceback.format_exc())

        def on_video_params(property1, params):
            try:
                if not params or not isinstance(params, dict):
                    return
                if "w" in params and "h" in params:
                    stream_info.video_properties[_("General")][
                        _("Dimensions")
                    ] = "{}x{}".format(params["w"], params["h"])
                if "aspect" in params:
                    aspect = round(float(params["aspect"]), 2)
                    stream_info.video_properties[_("General")][_("Aspect")] = (
                        "%s" % aspect
                    )
                if "pixelformat" in params:
                    stream_info.video_properties[_("Color")][
                        _("Pixel Format")
                    ] = params["pixelformat"]
                if "gamma" in params:
                    stream_info.video_properties[_("Color")][_("Gamma")] = params[
                        "gamma"
                    ]
                if "average-bpp" in params:
                    stream_info.video_properties[_("Color")][
                        _("Bits Per Pixel")
                    ] = params["average-bpp"]
            except Exception:
                pass

        def on_video_format(property1, vformat):
            try:
                if not vformat:
                    return
                stream_info.video_properties[_("General")][_("Codec")] = vformat
            except Exception:
                pass

        def on_audio_params(property1, params):
            try:
                if not params or not isinstance(params, dict):
                    return
                if "channels" in params:
                    layout_channels = params["channels"]
                    if "5.1" in layout_channels or "7.1" in layout_channels:
                        layout_channels += " " + _("surround sound")
                    stream_info.audio_properties[_("Layout")][
                        _("Channels")
                    ] = layout_channels
                if "samplerate" in params:
                    sr = float(params["samplerate"]) / 1000.0
                    stream_info.audio_properties[_("General")][_("Sample Rate")] = (
                        "%.1f KHz" % sr
                    )
                if "format" in params:
                    fmt = params["format"]
                    fmt = AUDIO_SAMPLE_FORMATS.get(fmt, fmt)
                    stream_info.audio_properties[_("General")][_("Format")] = fmt
                if "channel-count" in params:
                    stream_info.audio_properties[_("Layout")][
                        _("Channel Count")
                    ] = params["channel-count"]
            except Exception:
                pass

        def on_audio_codec(property1, codec):
            try:
                if not codec:
                    return
                stream_info.audio_properties[_("General")][_("Codec")] = codec.split()[
                    0
                ]
            except Exception:
                pass

        @async_gui_blocking_function
        def monitor_playback():
            try:
                YukiData.player.wait_until_playing()
                YukiData.player.observe_property("video-params", on_video_params)
                YukiData.player.observe_property("video-format", on_video_format)
                YukiData.player.observe_property("audio-params", on_audio_params)
                YukiData.player.observe_property("audio-codec", on_audio_codec)
                YukiData.player.observe_property("video-bitrate", on_bitrate)
                YukiData.player.observe_property("audio-bitrate", on_bitrate)
            except Exception:
                pass

        def hideLoading():
            YukiData.is_loading = False
            loading.hide()
            YukiGUI.loading_movie.stop()
            YukiGUI.loading1.hide()
            idle_on_metadata()

        def showLoading():
            YukiData.is_loading = True
            centerwidget(YukiGUI.loading1)
            loading.show()
            YukiGUI.loading_movie.start()
            YukiGUI.loading1.show()
            idle_on_metadata()

        YukiData.event_handler = None

        def on_before_play():
            YukiGUI.streaminfo_win.hide()
            stream_info.video_properties.clear()
            stream_info.video_properties[_("General")] = {}
            stream_info.video_properties[_("Color")] = {}

            stream_info.audio_properties.clear()
            stream_info.audio_properties[_("General")] = {}
            stream_info.audio_properties[_("Layout")] = {}

            stream_info.video_bitrates.clear()
            stream_info.audio_bitrates.clear()

        def get_ua_ref_for_channel(channel_name1):
            useragent_ref = YukiData.settings["ua"]
            referer_ref = YukiData.settings["referer"]
            if channel_name1:
                channel_item = getArrayItem(channel_name1)
                if channel_item:
                    useragent_ref = (
                        channel_item["useragent"]
                        if "useragent" in channel_item and channel_item["useragent"]
                        else YukiData.settings["ua"]
                    )
                    referer_ref = (
                        channel_item["referer"]
                        if "referer" in channel_item and channel_item["referer"]
                        else YukiData.settings["referer"]
                    )
            if YukiData.settings["m3u"] in YukiData.channel_sets:
                channel_set = YukiData.channel_sets[YukiData.settings["m3u"]]
                if channel_name1 and channel_name1 in channel_set:
                    channel_config = channel_set[channel_name1]
                    if (
                        "ua" in channel_config
                        and channel_config["ua"]
                        and channel_config["ua"] != YukiData.settings["ua"]
                    ):
                        useragent_ref = channel_config["ua"]
                    if (
                        "ref" in channel_config
                        and channel_config["ref"]
                        and channel_config["ref"] != YukiData.settings["referer"]
                    ):
                        referer_ref = channel_config["ref"]
            return useragent_ref, referer_ref

        def mpv_override_play(arg_override_play, channel_name1=""):
            on_before_play()
            useragent_ref, referer_ref = get_ua_ref_for_channel(channel_name1)
            YukiData.player.user_agent = useragent_ref
            if referer_ref:
                YukiData.player.http_header_fields = f"Referer: {referer_ref}"
            else:
                YukiData.player.http_header_fields = ""

            is_main = arg_override_play.endswith(
                "/main.png"
            ) or arg_override_play.endswith("\\main.png")
            if not is_main:
                logger.info(f"Using User-Agent: {YukiData.player.user_agent}")
                cur_ref = ""
                try:
                    for ref1 in YukiData.player.http_header_fields:
                        if ref1.startswith("Referer: "):
                            ref1 = ref1.replace("Referer: ", "", 1)
                            cur_ref = ref1
                except Exception:
                    pass
                if cur_ref:
                    logger.info(f"Using HTTP Referer: {cur_ref}")
                else:
                    logger.info("Using HTTP Referer: (empty)")

            if "uuid" in YukiData.settings:
                if YukiData.settings["uuid"] and not is_main:
                    logger.info("Set X-Playback-Session-Id header")
                    YukiData.player.http_header_fields = (
                        "X-Playback-Session-Id: " + str(uuid.uuid1())
                    )

            YukiData.player.pause = False
            YukiData.player.play(parse_specifiers_now_url(arg_override_play))
            if YukiData.event_handler:
                try:
                    YukiData.event_handler.on_metadata()
                except Exception:
                    pass

        def mpv_override_stop(ignore=False):
            YukiData.player.command("stop")
            if not ignore:
                logger.info("Disabling deinterlace for main.png")
                YukiData.player.deinterlace = False
            YukiData.player.play(str(Path("yuki_iptv", ICONS_FOLDER, "main.png")))
            YukiData.player.pause = True
            if YukiData.event_handler:
                try:
                    YukiData.event_handler.on_metadata()
                except Exception:
                    pass

        YukiData.firstVolRun = True

        def mpv_override_volume(volume_val):
            YukiData.player.volume = volume_val
            YukiData.volume = volume_val
            if YukiData.event_handler:
                try:
                    YukiData.event_handler.on_volume()
                except Exception:
                    pass

        def mpv_override_mute(mute_val):
            YukiData.player.mute = mute_val
            if YukiData.event_handler:
                try:
                    YukiData.event_handler.on_volume()
                except Exception:
                    pass

        def stopPlayer(ignore=False):
            try:
                mpv_override_stop(ignore)
            except Exception:
                YukiData.player.loop = True
                mpv_override_play(str(Path("yuki_iptv", ICONS_FOLDER, "main.png")))
                YukiData.player.pause = True

        def setVideoAspect(va):
            if va == 0:
                va = -1
            try:
                YukiData.player.video_aspect_override = va
            except Exception:
                YukiData.player.video_aspect = va

        def setZoom(zm):
            YukiData.player.video_zoom = zm

        def setPanscan(ps):
            YukiData.player.panscan = ps

        def getVideoAspect():
            try:
                va1 = YukiData.player.video_aspect_override
            except Exception:
                va1 = YukiData.player.video_aspect
            return va1

        def doPlay(play_url1, ua_ch=def_user_agent, channel_name_0=""):
            comm_instance.do_play_args = (play_url1, ua_ch, channel_name_0)
            logger.info("")
            logger.info(f"Playing '{channel_name_0}' ('{format_url_clean(play_url1)}')")
            # Loading
            loading.setText(_("Loading..."))
            loading.setStyleSheet("color: #778a30")
            showLoading()
            # Optimizations
            if play_url1.startswith("udp://") or play_url1.startswith("rtp://"):
                if YukiData.settings["multicastoptimization"]:
                    try:
                        # For low latency on multicast
                        logger.info("Using multicast optimized settings")
                        YukiData.player.cache = "no"
                        YukiData.player.untimed = True
                        YukiData.player["cache-pause"] = False
                        YukiData.player["audio-buffer"] = 0
                        YukiData.player["vd-lavc-threads"] = 1
                        YukiData.player["demuxer-lavf-probe-info"] = "nostreams"
                        YukiData.player["demuxer-lavf-analyzeduration"] = 0.1
                        YukiData.player["video-sync"] = "audio"
                        YukiData.player["interpolation"] = False
                        YukiData.player["video-latency-hacks"] = True
                    except Exception:
                        logger.warning("Failed to set multicast optimized settings!")
            try:
                if YukiData.settings["autoreconnection"]:
                    YukiData.player.stream_lavf_o = (
                        "-reconnect=1 -reconnect_at_eof=1 "
                        "-reconnect_streamed=1 -reconnect_delay_max=2"
                    )
            except Exception:
                pass
            YukiData.player.loop = False
            # Playing
            mpv_override_play(play_url1, channel_name_0)
            # Set channel (video) settings
            setPlayerSettings(channel_name_0)
            # Monitor playback (for stream information)
            monitor_playback()

        def channel_settings_save():
            channel_3 = YukiGUI.title.text()
            if YukiData.settings["m3u"] not in YukiData.channel_sets:
                YukiData.channel_sets[YukiData.settings["m3u"]] = {}
            YukiData.channel_sets[YukiData.settings["m3u"]][channel_3] = {
                "deinterlace": YukiGUI.deinterlace_chk.isChecked(),
                "ua": YukiGUI.useragent_choose.text(),
                "ref": YukiGUI.referer_choose_custom.text(),
                "group": YukiGUI.group_text.text(),
                "hidden": YukiGUI.hidden_chk.isChecked(),
                "contrast": YukiGUI.contrast_choose.value(),
                "brightness": YukiGUI.brightness_choose.value(),
                "hue": YukiGUI.hue_choose.value(),
                "saturation": YukiGUI.saturation_choose.value(),
                "gamma": YukiGUI.gamma_choose.value(),
                "videoaspect": YukiGUI.videoaspect_choose.currentIndex(),
                "zoom": YukiGUI.zoom_choose.currentIndex(),
                "panscan": YukiGUI.panscan_choose.value(),
                "epgname": (
                    YukiGUI.epgname_lbl.text()
                    if YukiGUI.epgname_lbl.text() != _("Default")
                    else ""
                ),
            }
            save_channel_sets()
            if YukiData.playing_channel == channel_3:
                YukiData.player.deinterlace = YukiGUI.deinterlace_chk.isChecked()
                YukiData.player.contrast = YukiGUI.contrast_choose.value()
                YukiData.player.brightness = YukiGUI.brightness_choose.value()
                YukiData.player.hue = YukiGUI.hue_choose.value()
                YukiData.player.saturation = YukiGUI.saturation_choose.value()
                YukiData.player.gamma = YukiGUI.gamma_choose.value()
                YukiData.player.video_zoom = YukiGUI.zoom_vars[
                    list(YukiGUI.zoom_vars)[YukiGUI.zoom_choose.currentIndex()]
                ]
                YukiData.player.panscan = YukiGUI.panscan_choose.value()
                setVideoAspect(
                    YukiGUI.videoaspect_vars[
                        list(YukiGUI.videoaspect_vars)[
                            YukiGUI.videoaspect_choose.currentIndex()
                        ]
                    ]
                )
            btn_update_click()
            YukiGUI.channels_win.close()

        YukiGUI.save_btn.clicked.connect(channel_settings_save)

        YukiGUI.channels_win.setCentralWidget(YukiGUI.wid)

        YukiData.do_save_settings = False

        # Settings window
        def save_settings():
            settings_old = YukiData.settings.copy()

            if YukiData.settings["epgoffset"] != YukiGUI.soffset.value():
                if os.path.isfile(str(Path(LOCAL_DIR, "epg.cache"))):
                    os.remove(str(Path(LOCAL_DIR, "epg.cache")))

            if YukiData.settings["epgdays"] != YukiGUI.epgdays.value():
                logger.info("EPG days option changed, removing cache")
                if os.path.exists(str(Path(LOCAL_DIR, "epg.cache"))):
                    os.remove(str(Path(LOCAL_DIR, "epg.cache")))

            settings_arr = YukiGUI.get_settings(
                YukiData.settings["uuid"] if "uuid" in YukiData.settings else False,
                SAVE_FOLDER_DEFAULT,
            )

            if YukiGUI.catchupenable_flag.isChecked() != settings_old["catchupenable"]:
                if os.path.exists(str(Path(LOCAL_DIR, "epg.cache"))):
                    os.remove(str(Path(LOCAL_DIR, "epg.cache")))
            settings_file1 = open(
                str(Path(LOCAL_DIR, "settings.json")), "w", encoding="utf8"
            )
            settings_file1.write(json.dumps(settings_arr))
            settings_file1.close()
            YukiGUI.settings_win.hide()
            YukiData.do_save_settings = True
            app.quit()

        def reset_channel_settings():
            if os.path.isfile(str(Path(LOCAL_DIR, "channelsettings.json"))):
                os.remove(str(Path(LOCAL_DIR, "channelsettings.json")))
            if os.path.isfile(str(Path(LOCAL_DIR, "favouritechannels.json"))):
                os.remove(str(Path(LOCAL_DIR, "favouritechannels.json")))
            if os.path.isfile(str(Path(LOCAL_DIR, "sortchannels.json"))):
                os.remove(str(Path(LOCAL_DIR, "sortchannels.json")))
            save_settings()

        def do_clear_logo_cache():
            logger.info("Clearing channel logos cache...")
            if os.path.isdir(Path(LOCAL_DIR, "logo_cache")):
                channel_logos = os.listdir(Path(LOCAL_DIR, "logo_cache"))
                for channel_logo in channel_logos:
                    if os.path.isfile(Path(LOCAL_DIR, "logo_cache", channel_logo)):
                        os.remove(Path(LOCAL_DIR, "logo_cache", channel_logo))
            logger.info("Channel logos cache cleared!")

        def close_settings():
            YukiGUI.settings_win.hide()
            if not win.isVisible():
                if not YukiGUI.playlists_win.isVisible():
                    myExitHandler_before()
                    sys.exit(0)

        def xtream_select():
            m3u_edit_1_text = YukiGUI.m3u_edit_1.text()
            if m3u_edit_1_text.startswith("XTREAM::::::::::::::"):
                m3u_edit_1_text_sp = m3u_edit_1_text.split("::::::::::::::")
                YukiGUI.xtr_username_input_2.setText(m3u_edit_1_text_sp[1])
                YukiGUI.xtr_password_input_2.setText(m3u_edit_1_text_sp[2])
                YukiGUI.xtr_url_input_2.setText(m3u_edit_1_text_sp[3])
            moveWindowToCenter(YukiGUI.xtream_win)
            YukiGUI.xtream_win.show()

        YukiGUI.ssave.clicked.connect(save_settings)
        YukiGUI.sreset.clicked.connect(reset_channel_settings)
        YukiGUI.clear_logo_cache.clicked.connect(do_clear_logo_cache)
        YukiGUI.sclose.clicked.connect(close_settings)
        YukiGUI.sfolder.clicked.connect(save_folder_select)

        YukiGUI.m3u = YukiData.settings["m3u"]
        YukiGUI.epg = (
            YukiData.settings["epg"]
            if not YukiData.settings["epg"].startswith("^^::MULTIPLE::^^")
            else ""
        )
        YukiGUI.sudp.setText(YukiData.settings["udp_proxy"])
        YukiGUI.sdei.setChecked(YukiData.settings["deinterlace"])
        YukiGUI.shwaccel.setChecked(YukiData.settings["hwaccel"])
        YukiGUI.sfld.setText(YukiData.settings["save_folder"])
        YukiGUI.soffset.setValue(YukiData.settings["epgoffset"])
        YukiGUI.scache1.setValue(YukiData.settings["cache_secs"])
        YukiGUI.epgdays.setValue(YukiData.settings["epgdays"])
        YukiGUI.referer_choose.setText(YukiData.settings["referer"])
        YukiGUI.useragent_choose_2.setText(YukiData.settings["ua"])
        YukiGUI.mpv_options.setText(YukiData.settings["mpv_options"])
        YukiGUI.donot_flag.setChecked(YukiData.settings["donotupdateepg"])
        YukiGUI.openprevchannel_flag.setChecked(YukiData.settings["openprevchannel"])
        YukiGUI.hidempv_flag.setChecked(YukiData.settings["hidempv"])
        YukiGUI.hideepgpercentage_flag.setChecked(
            YukiData.settings["hideepgpercentage"]
        )
        YukiGUI.hideepgfromplaylist_flag.setChecked(
            YukiData.settings["hideepgfromplaylist"]
        )
        YukiGUI.multicastoptimization_flag.setChecked(
            YukiData.settings["multicastoptimization"]
        )
        YukiGUI.hidebitrateinfo_flag.setChecked(YukiData.settings["hidebitrateinfo"])
        YukiGUI.styleredefoff_flag.setChecked(YukiData.settings["styleredefoff"])
        YukiGUI.volumechangestep_choose.setValue(YukiData.settings["volumechangestep"])
        YukiGUI.flpopacity_input.setValue(YukiData.settings["flpopacity"])
        YukiGUI.panelposition_choose.setCurrentIndex(YukiData.settings["panelposition"])
        YukiGUI.mouseswitchchannels_flag.setChecked(
            YukiData.settings["mouseswitchchannels"]
        )
        YukiGUI.autoreconnection_flag.setChecked(YukiData.settings["autoreconnection"])
        YukiGUI.showplaylistmouse_flag.setChecked(
            YukiData.settings["showplaylistmouse"]
        )
        YukiGUI.showcontrolsmouse_flag.setChecked(
            YukiData.settings["showcontrolsmouse"]
        )
        YukiGUI.channellogos_select.setCurrentIndex(YukiData.settings["channellogos"])
        YukiGUI.nocacheepg_flag.setChecked(YukiData.settings["nocacheepg"])
        YukiGUI.scrrecnosubfolders_flag.setChecked(
            YukiData.settings["scrrecnosubfolders"]
        )
        YukiGUI.sort_widget.setCurrentIndex(YukiData.settings["sort"])

        for videoaspect_var_1 in YukiGUI.videoaspect_vars:
            YukiGUI.videoaspect_def_choose.addItem(videoaspect_var_1)

        for zoom_var_1 in YukiGUI.zoom_vars:
            YukiGUI.zoom_def_choose.addItem(zoom_var_1)

        YukiGUI.videoaspect_def_choose.setCurrentIndex(YukiData.settings["videoaspect"])
        YukiGUI.zoom_def_choose.setCurrentIndex(YukiData.settings["zoom"])
        YukiGUI.panscan_def_choose.setValue(YukiData.settings["panscan"])
        YukiGUI.catchupenable_flag.setChecked(YukiData.settings["catchupenable"])
        YukiGUI.rewindenable_flag.setChecked(YukiData.settings["rewindenable"])
        YukiGUI.hidechannellogos_flag.setChecked(YukiData.settings["hidechannellogos"])
        YukiGUI.hideplaylistbyleftmouseclick_flag.setChecked(
            YukiData.settings["hideplaylistbyleftmouseclick"]
        )

        YukiGUI.settings_win.scroll.setWidget(YukiGUI.wid2)

        def xtream_save_btn_action_2():
            if (
                YukiGUI.xtr_username_input_2.text()
                and YukiGUI.xtr_password_input_2.text()
                and YukiGUI.xtr_url_input_2.text()
            ):
                xtream_gen_url_2 = "XTREAM::::::::::::::" + "::::::::::::::".join(
                    [
                        YukiGUI.xtr_username_input_2.text(),
                        YukiGUI.xtr_password_input_2.text(),
                        YukiGUI.xtr_url_input_2.text(),
                    ]
                )
                YukiGUI.m3u_edit_1.setText(xtream_gen_url_2)
            YukiGUI.xtream_win.hide()

        YukiGUI.save_btn_xtream_2.clicked.connect(xtream_save_btn_action_2)
        YukiGUI.xtream_win.setCentralWidget(YukiGUI.wid4)

        @idle_function
        def setUrlText(unused=None):
            YukiGUI.url_text.setText(YukiData.playing_url)
            YukiGUI.url_text.setCursorPosition(0)
            if YukiGUI.streaminfo_win.isVisible():
                YukiGUI.streaminfo_win.hide()

        YukiGUI.streaminfo_win.setCentralWidget(YukiGUI.wid5)

        def show_license():
            if not YukiGUI.license_win.isVisible():
                moveWindowToCenter(YukiGUI.license_win)
                YukiGUI.license_win.show()
            else:
                YukiGUI.license_win.hide()

        YukiGUI.licensebox_close_btn.clicked.connect(YukiGUI.license_win.close)
        YukiGUI.license_win.setCentralWidget(YukiGUI.licensewin_widget)

        class Communicate(QtCore.QObject):
            do_play_args = ()
            comboboxIndex = -1
            mainThread = Signal(type(lambda x: None))
            mainThread_partial = Signal(type(partial(int, 2)))

        def exInMainThread_partial(function_exec):
            try:
                comm_instance.mainThread_partial.emit(function_exec)
            except Exception:
                logger.warning("exInMainThread_partial failed")

        def comm_instance_main_thread(function_exec1):
            function_exec1()

        comm_instance = Communicate()
        comm_instance.mainThread.connect(comm_instance_main_thread)
        comm_instance.mainThread_partial.connect(comm_instance_main_thread)

        def aboutqt_show():
            QtWidgets.QMessageBox.aboutQt(QtWidgets.QWidget(), MAIN_WINDOW_TITLE)
            YukiGUI.help_win.raise_()
            YukiGUI.help_win.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
            YukiGUI.help_win.activateWindow()

        YukiGUI.license_btn.clicked.connect(show_license)
        YukiGUI.aboutqt_btn.clicked.connect(aboutqt_show)
        YukiGUI.close_btn.clicked.connect(YukiGUI.help_win.close)

        YukiGUI.help_win.setCentralWidget(YukiGUI.helpwin_widget)

        def shortcuts_table_clicked(row1, column1):
            if column1 == 1:  # keybind
                sc1_text = YukiGUI.shortcuts_table.item(row1, column1).text()
                keyseq.setKeySequence(sc1_text)
                YukiData.selected_shortcut_row = row1
                keyseq.setFocus()
                moveWindowToCenter(YukiGUI.shortcuts_win_2)
                YukiGUI.shortcuts_win_2.show()

        YukiGUI.shortcuts_table.cellDoubleClicked.connect(shortcuts_table_clicked)

        def get_widget_item(widget_str):
            twi = QtWidgets.QTableWidgetItem(widget_str)
            twi.setFlags(twi.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            return twi

        def show_shortcuts():
            if not YukiGUI.shortcuts_win.isVisible():
                # start
                YukiGUI.shortcuts_table.setRowCount(len(YukiData.main_keybinds))
                keybind_i = -1
                for keybind in YukiData.main_keybinds:
                    keybind_i += 1
                    YukiGUI.shortcuts_table.setItem(
                        keybind_i,
                        0,
                        get_widget_item(main_keybinds_translations[keybind]),
                    )
                    if isinstance(YukiData.main_keybinds[keybind], str):
                        keybind_str = YukiData.main_keybinds[keybind]
                    else:
                        keybind_str = QtGui.QKeySequence(
                            YukiData.main_keybinds[keybind]
                        ).toString()
                    kbd_widget = get_widget_item(keybind_str)
                    kbd_widget.setToolTip(_("Double click to change"))
                    YukiGUI.shortcuts_table.setItem(keybind_i, 1, kbd_widget)
                YukiGUI.shortcuts_table.resizeColumnsToContents()
                # end
                moveWindowToCenter(YukiGUI.shortcuts_win)
                YukiGUI.shortcuts_win.show()
            else:
                YukiGUI.shortcuts_win.hide()

        def show_settings():
            if not YukiGUI.settings_win.isVisible():
                moveWindowToCenter(YukiGUI.settings_win)
                YukiGUI.settings_win.show()
            else:
                YukiGUI.settings_win.hide()

        def show_help():
            if not YukiGUI.help_win.isVisible():
                moveWindowToCenter(YukiGUI.help_win)
                YukiGUI.help_win.show()
            else:
                YukiGUI.help_win.hide()

        def show_sort():
            if not YukiGUI.sort_win.isVisible():
                YukiGUI.sort_list.clear()
                for sort_label_ch in (
                    array_sorted if not YukiData.channel_sort else YukiData.channel_sort
                ):
                    YukiGUI.sort_list.addItem(sort_label_ch)

                moveWindowToCenter(YukiGUI.sort_win)
                YukiGUI.sort_win.show()
            else:
                YukiGUI.sort_win.hide()

        def populate_playlists():
            YukiGUI.playlists_list.clear()
            playlists_data.playlists_used = playlists_saved
            for item2 in playlists_data.playlists_used:
                YukiGUI.playlists_list.addItem(create_playlist_item_widget(item2))

        def show_playlists():
            if not YukiGUI.playlists_win.isVisible():
                populate_playlists()
                moveWindowToCenter(YukiGUI.playlists_win)
                YukiGUI.playlists_win.show()
                show_xtream_playlists_expiration()
            else:
                YukiGUI.playlists_win.hide()

        def reload_playlist():
            logger.info("Reloading playlist...")
            save_settings()

        def playlists_selected():
            try:
                prov_data = playlists_data.playlists_used[
                    YukiGUI.playlists_list.currentItem().data(
                        QtCore.Qt.ItemDataRole.UserRole
                    )
                ]
                prov_m3u = prov_data["m3u"]
                prov_epg = ""
                prov_offset = 0
                if "epg" in prov_data:
                    prov_epg = prov_data["epg"]
                if "epgoffset" in prov_data:
                    prov_offset = prov_data["epgoffset"]
                YukiGUI.m3u = prov_m3u
                YukiGUI.epg = (
                    prov_epg if not prov_epg.startswith("^^::MULTIPLE::^^") else ""
                )
                YukiGUI.soffset.setValue(prov_offset)
                playlists_save_json()
                YukiGUI.playlists_win.hide()
                YukiGUI.playlists_win_edit.hide()
                save_settings()
            except Exception:
                pass

        def playlists_save_json():
            playlists_json_save(playlists_data.playlists_used)

        def playlists_edit_do(ignore0=False):
            try:
                currentItem_text = YukiGUI.playlists_list.currentItem().data(
                    QtCore.Qt.ItemDataRole.UserRole
                )
            except Exception:
                currentItem_text = ""
            if ignore0:
                YukiGUI.name_edit_1.setText("")
                YukiGUI.m3u_edit_1.setText("")
                YukiGUI.epg_edit_1.setText("")
                YukiGUI.soffset_1.setValue(0)
                playlists_data.oldName = ""
                moveWindowToCenter(YukiGUI.playlists_win_edit)
                YukiGUI.playlists_win_edit.show()
            else:
                if currentItem_text:
                    item_m3u = playlists_data.playlists_used[currentItem_text]["m3u"]
                    try:
                        item_epg = playlists_data.playlists_used[currentItem_text][
                            "epg"
                        ]
                    except Exception:
                        item_epg = ""
                    try:
                        item_offset = playlists_data.playlists_used[currentItem_text][
                            "epgoffset"
                        ]
                    except Exception:
                        item_offset = 0
                    YukiGUI.name_edit_1.setText(currentItem_text)
                    YukiGUI.m3u_edit_1.setText(item_m3u)
                    YukiGUI.epg_edit_1.setText(item_epg)
                    YukiGUI.soffset_1.setValue(item_offset)
                    playlists_data.oldName = currentItem_text
                    moveWindowToCenter(YukiGUI.playlists_win_edit)
                    YukiGUI.playlists_win_edit.show()

        def playlists_delete_do():
            resettodefaults_btn_clicked_msg_1 = QtWidgets.QMessageBox.question(
                None,
                MAIN_WINDOW_TITLE,
                _("Delete playlist permanently?"),
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.Yes,
            )
            if (
                resettodefaults_btn_clicked_msg_1
                == QtWidgets.QMessageBox.StandardButton.Yes
            ):
                try:
                    currentItem_text = YukiGUI.playlists_list.currentItem().data(
                        QtCore.Qt.ItemDataRole.UserRole
                    )
                except Exception:
                    currentItem_text = ""
                if currentItem_text:
                    YukiGUI.playlists_list.takeItem(YukiGUI.playlists_list.currentRow())
                    playlists_data.playlists_used.pop(currentItem_text)
                    playlists_save_json()
            show_xtream_playlists_expiration()

        def playlists_add_do():
            playlists_edit_do(True)

        YukiGUI.playlists_list.itemDoubleClicked.connect(playlists_selected)
        YukiGUI.playlists_select.clicked.connect(playlists_selected)
        YukiGUI.playlists_add.clicked.connect(playlists_add_do)
        YukiGUI.playlists_edit.clicked.connect(playlists_edit_do)
        YukiGUI.playlists_delete.clicked.connect(playlists_delete_do)
        YukiGUI.playlists_settings.clicked.connect(show_settings)

        YukiData.fullscreen = False

        def set_mpv_osc(osc_value):
            if YukiData.mpv_osc_enabled:
                if osc_value != YukiData.osc:
                    YukiData.osc = osc_value
                    YukiData.player.osc = osc_value

        YukiData.mpv_osc_enabled = True

        def init_mpv_player():
            mpv_loglevel = "info" if loglevel.lower() != "debug" else "debug"
            YukiData.mpv_osc_enabled = True
            if "osc" in options:
                # To prevent 'multiple values for keyword argument'!
                YukiData.mpv_osc_enabled = options.pop("osc") != "no"
            if enable_libmpv_render_context:
                options["vo"] = "null"
            else:
                options["wid"] = str(int(win.container.winId()))
            try:
                YukiData.player = mpv.MPV(
                    **options,
                    osc=YukiData.mpv_osc_enabled,
                    script_opts="osc-layout=box,osc-seekbarstyle=bar,"
                    "osc-deadzonesize=0,osc-minmousemove=3",
                    ytdl=True,
                    log_handler=my_log,
                    loglevel=mpv_loglevel,
                )
            except Exception:
                logger.warning("mpv init with ytdl failed")
                try:
                    YukiData.player = mpv.MPV(
                        **options,
                        osc=YukiData.mpv_osc_enabled,
                        script_opts="osc-layout=box,osc-seekbarstyle=bar,"
                        "osc-deadzonesize=0,osc-minmousemove=3",
                        log_handler=my_log,
                        loglevel=mpv_loglevel,
                    )
                except Exception:
                    logger.warning("mpv init with osc failed")
                    YukiData.player = mpv.MPV(
                        **options,
                        log_handler=my_log,
                        loglevel=mpv_loglevel,
                    )
            if YukiData.settings["hidempv"]:
                try:
                    set_mpv_osc(False)
                except Exception:
                    logger.warning("player.osc set failed")

            if enable_libmpv_render_context:
                container_layout = QtWidgets.QVBoxLayout()
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(0)
                mpv_opengl_widget = MPVOpenGLWidget(
                    app,
                    YukiData.player,
                    mpv.MpvRenderContext,
                    mpv.MpvGlGetProcAddressFn,
                )
                container_layout.addWidget(mpv_opengl_widget)
                win.container.setLayout(container_layout)

            try:
                YukiData.player["force-seekable"] = True
            except Exception:
                pass
            if not YukiData.settings["hwaccel"]:
                try:
                    YukiData.player["x11-bypass-compositor"] = "yes"
                except Exception:
                    pass
            try:
                YukiData.player["network-timeout"] = 25
            except Exception:
                pass

            try:
                YukiData.player.title = MAIN_WINDOW_TITLE
            except Exception:
                pass

            try:
                YukiData.player["audio-client-name"] = "yuki-iptv"
            except Exception:
                logger.warning("mpv audio-client-name set failed")

            try:
                mpv_version = YukiData.player.mpv_version
                if not mpv_version.startswith("mpv "):
                    mpv_version = "mpv " + mpv_version
            except Exception:
                mpv_version = "unknown mpv version"

            logger.info(f"Using {mpv_version}")

            YukiGUI.textbox.setText(get_about_text())

            if YukiData.settings["cache_secs"] != 0:
                try:
                    YukiData.player["demuxer-readahead-secs"] = YukiData.settings[
                        "cache_secs"
                    ]
                    logger.info(
                        f'Demuxer cache set to {YukiData.settings["cache_secs"]}s'
                    )
                except Exception:
                    pass
                try:
                    YukiData.player["cache-secs"] = YukiData.settings["cache_secs"]
                    logger.info(f'Cache set to {YukiData.settings["cache_secs"]}s')
                except Exception:
                    pass
            else:
                logger.info("Using default cache settings")
            YukiData.player.user_agent = def_user_agent
            if YukiData.settings["referer"]:
                YukiData.player.http_header_fields = (
                    f"Referer: {YukiData.settings['referer']}"
                )
                logger.info(f"HTTP referer: '{YukiData.settings['referer']}'")
            else:
                logger.info("No HTTP referer set up")
            mpv_override_volume(100)
            YukiData.player.loop = True

            aot_action1 = None
            try:
                aot_action1 = populate_menubar(
                    0,
                    win.menu_bar_qt,
                    win,
                    YukiData.player.track_list,
                    YukiData.playing_channel,
                    get_keybind,
                )
                populate_menubar(
                    1,
                    YukiData.right_click_menu,
                    win,
                    YukiData.player.track_list,
                    YukiData.playing_channel,
                    get_keybind,
                )
            except Exception:
                logger.warning("populate_menubar failed")
                show_exception(traceback.format_exc(), "populate_menubar failed")
            logger.info("redraw_menubar triggered by init")
            redraw_menubar()

            @YukiData.player.property_observer("duration")
            def duration_observer(_name, value):
                try:
                    if YukiData.old_playing_url != YukiData.playing_url:
                        YukiData.old_playing_url = YukiData.playing_url
                        YukiData.event_handler.on_metadata()
                except Exception:
                    pass

            @idle_function
            def seek_event_callback(unused=None):
                if YukiData.mpris_ready and YukiData.mpris_running:
                    (
                        playback_status,
                        mpris_trackid,
                        artUrl,
                        player_position,
                    ) = get_mpris_metadata()
                    mpris_seeked(player_position)

            @YukiData.player.event_callback("seek")
            def seek_event(event):
                seek_event_callback()

            @YukiData.player.event_callback("file-loaded")
            def file_loaded_2(event):
                file_loaded_callback()

            @YukiData.player.event_callback("end_file")
            def ready_handler_2(event):
                if event["event"]["error"] != 0:
                    end_file_error_callback()
                else:
                    end_file_callback()

            if needs_player_keybinds:

                @YukiData.player.on_key_press("MBTN_RIGHT")
                def my_mouse_right():
                    my_mouse_right_callback()

                @YukiData.player.on_key_press("MBTN_LEFT")
                def my_mouse_left():
                    my_mouse_left_callback()

                @YukiData.player.on_key_press("MBTN_LEFT_DBL")
                def my_leftdbl_binding():
                    mpv_fullscreen()

                @YukiData.player.on_key_press("MBTN_FORWARD")
                def my_forward_binding():
                    next_channel()

                @YukiData.player.on_key_press("MBTN_BACK")
                def my_back_binding():
                    prev_channel()

                @YukiData.player.on_key_press("WHEEL_UP")
                def my_up_binding():
                    my_up_binding_execute()

                @YukiData.player.on_key_press("WHEEL_DOWN")
                def my_down_binding():
                    my_down_binding_execute()

            @idle_function
            def pause_handler(unused=None, unused2=None, unused3=None):
                try:
                    if not YukiData.player.pause:
                        YukiGUI.btn_playpause.setIcon(
                            QtGui.QIcon(
                                str(Path("yuki_iptv", ICONS_FOLDER, "pause.png"))
                            )
                        )
                        YukiGUI.btn_playpause.setToolTip(_("Pause"))
                    else:
                        YukiGUI.btn_playpause.setIcon(
                            QtGui.QIcon(
                                str(Path("yuki_iptv", ICONS_FOLDER, "play.png"))
                            )
                        )
                        YukiGUI.btn_playpause.setToolTip(_("Play"))
                    if YukiData.event_handler:
                        try:
                            YukiData.event_handler.on_playpause()
                        except Exception:
                            pass
                except Exception:
                    pass

            YukiData.player.observe_property("pause", pause_handler)

            def yuki_track_set(track, type1):
                logger.info(f"Set {type1} track to {track}")
                if YukiData.playing_channel not in YukiData.player_tracks:
                    YukiData.player_tracks[YukiData.playing_channel] = {}
                if type1 == "vid":
                    YukiData.player.vid = track
                    YukiData.player_tracks[YukiData.playing_channel]["vid"] = track
                elif type1 == "aid":
                    YukiData.player.aid = track
                    YukiData.player_tracks[YukiData.playing_channel]["aid"] = track
                elif type1 == "sid":
                    YukiData.player.sid = track
                    YukiData.player_tracks[YukiData.playing_channel]["sid"] = track

            init_menubar_player(
                YukiData.player,
                mpv_play,
                mpv_stop,
                prev_channel,
                next_channel,
                mpv_fullscreen,
                showhideeverything,
                main_channel_settings,
                show_settings,
                show_help,
                do_screenshot,
                mpv_mute,
                showhideplaylist,
                lowpanel_ch_1,
                open_stream_info,
                app.quit,
                redraw_menubar,
                QtGui.QIcon(
                    QtGui.QIcon(
                        str(Path("yuki_iptv", ICONS_FOLDER, "circle.png"))
                    ).pixmap(8, 8)
                ),
                my_up_binding_execute,
                my_down_binding_execute,
                show_playlist_editor,
                show_playlists,
                show_sort,
                show_exception,
                get_curwindow_pos,
                force_update_epg_act,
                get_keybind,
                show_tvguide_2,
                enable_always_on_top,
                disable_always_on_top,
                reload_playlist,
                show_shortcuts,
                str(Path(LOCAL_DIR, "alwaysontop.json")),
                yuki_track_set,
                mpv_frame_step,
                mpv_frame_back_step,
            )

            volume_option1 = read_option("volume")
            if volume_option1 is not None:
                logger.info(f"Set volume to {vol_remembered}")
                YukiGUI.volume_slider.setValue(vol_remembered)
                mpv_volume_set()
            else:
                YukiGUI.volume_slider.setValue(100)
                mpv_volume_set()

            return aot_action1

        def move_label(label, x, y):
            label.move(x, y)

        def set_label_width(label, width):
            if width > 0:
                label.setFixedWidth(width)

        def get_global_cursor_position():
            return QtGui.QCursor.pos()

        if enable_libmpv_render_context:
            needs_player_keybinds = False
        else:
            needs_player_keybinds = True

        class MainWindow(QtWidgets.QMainWindow):
            oldpos = None
            oldpos1 = None

            def __init__(self, parent=None):
                super().__init__(parent)
                self.windowWidth = self.width()
                self.windowHeight = self.height()
                self.container = None
                self.listWidget = None
                self.moviesWidget = None
                self.seriesWidget = None
                self.latestWidth = 0
                self.latestHeight = 0
                self.createMenuBar_mw()

                #
                # == mpv init ==
                #

                class Container(QtWidgets.QWidget):
                    def mousePressEvent(self, event3):
                        if event3.button() == QtCore.Qt.MouseButton.LeftButton:
                            my_mouse_left_callback()
                        elif event3.button() == QtCore.Qt.MouseButton.RightButton:
                            my_mouse_right_callback()
                        elif event3.button() in [
                            QtCore.Qt.MouseButton.BackButton,
                            QtCore.Qt.MouseButton.XButton1,
                            QtCore.Qt.MouseButton.ExtraButton1,
                        ]:
                            prev_channel()
                        elif event3.button() in [
                            QtCore.Qt.MouseButton.ForwardButton,
                            QtCore.Qt.MouseButton.XButton2,
                            QtCore.Qt.MouseButton.ExtraButton2,
                        ]:
                            next_channel()
                        else:
                            super().mousePressEvent(event3)

                    def mouseDoubleClickEvent(self, event3):
                        if event3.button() == QtCore.Qt.MouseButton.LeftButton:
                            mpv_fullscreen()

                    def wheelEvent(self, event3):
                        if event3.angleDelta().y() > 0:
                            # up
                            my_up_binding_execute()
                        else:
                            # down
                            my_down_binding_execute()
                        event3.accept()

                if not needs_player_keybinds:
                    self.container = Container(self)
                else:
                    self.container = QtWidgets.QWidget(self)
                self.setCentralWidget(self.container)
                self.container.setAttribute(
                    QtCore.Qt.WidgetAttribute.WA_DontCreateNativeAncestors
                )
                if not enable_libmpv_render_context:
                    self.container.setAttribute(
                        QtCore.Qt.WidgetAttribute.WA_NativeWindow
                    )
                self.container.setFocus()
                self.container.setStyleSheet(
                    """
                    background-color: #C0C6CA;
                """
                )

            def updateWindowSize(self):
                if (
                    self.width() != self.latestWidth
                    or self.height() != self.latestHeight
                ):
                    self.latestWidth = self.width()
                    self.latestHeight = self.height()

            def resize_rewind(self):
                rewind_normal_offset = 150
                rewind_fullscreen_offset = 180
                if YukiData.settings["panelposition"] == 2:
                    dockWidget_playlist_cur_width = 0
                else:
                    dockWidget_playlist_cur_width = dockWidget_playlist.width()

                if not YukiData.fullscreen:
                    if not dockWidget_controlPanel.isVisible():
                        set_label_width(
                            YukiGUI.rewind,
                            self.windowWidth - dockWidget_playlist_cur_width + 58,
                        )
                        move_label(
                            YukiGUI.rewind,
                            int(
                                ((self.windowWidth - YukiGUI.rewind.width()) / 2)
                                - (dockWidget_playlist_cur_width / 1.7)
                            ),
                            int(
                                (self.windowHeight - YukiGUI.rewind.height())
                                - rewind_fullscreen_offset
                            ),
                        )
                    else:
                        set_label_width(
                            YukiGUI.rewind,
                            self.windowWidth - dockWidget_playlist_cur_width + 58,
                        )
                        move_label(
                            YukiGUI.rewind,
                            int(
                                ((self.windowWidth - YukiGUI.rewind.width()) / 2)
                                - (dockWidget_playlist_cur_width / 1.7)
                            ),
                            int(
                                (self.windowHeight - YukiGUI.rewind.height())
                                - dockWidget_controlPanel.height()
                                - rewind_normal_offset
                            ),
                        )
                else:
                    set_label_width(YukiGUI.rewind, YukiGUI.controlpanel_widget.width())
                    rewind_position_x = (
                        YukiGUI.controlpanel_widget.pos().x() - win.pos().x()
                    )
                    if rewind_position_x < 0:
                        rewind_position_x = 0
                    move_label(
                        YukiGUI.rewind,
                        rewind_position_x,
                        int(
                            (self.windowHeight - YukiGUI.rewind.height())
                            - rewind_fullscreen_offset
                        ),
                    )

            def update(self):
                if YukiData.settings["panelposition"] == 2:
                    dockWidget_playlist_cur_width2 = 0
                else:
                    dockWidget_playlist_cur_width2 = dockWidget_playlist.width()

                self.windowWidth = self.width()
                self.windowHeight = self.height()
                self.updateWindowSize()
                if YukiData.settings["panelposition"] in (0, 2):
                    move_label(YukiData.tvguide_lbl, 2, YukiGUI.tvguide_lbl_offset)
                else:
                    move_label(
                        YukiData.tvguide_lbl,
                        win.width() - YukiData.tvguide_lbl.width(),
                        YukiGUI.tvguide_lbl_offset,
                    )
                self.resize_rewind()
                if not YukiData.fullscreen:
                    if not dockWidget_controlPanel.isVisible():
                        set_label_width(
                            YukiData.state,
                            self.windowWidth - dockWidget_playlist_cur_width2 + 58,
                        )
                        move_label(
                            YukiData.state,
                            int(
                                ((self.windowWidth - YukiData.state.width()) / 2)
                                - (dockWidget_playlist_cur_width2 / 1.7)
                            ),
                            int((self.windowHeight - YukiData.state.height()) - 20),
                        )
                        h = 0
                        h2 = 10
                    else:
                        set_label_width(
                            YukiData.state,
                            self.windowWidth - dockWidget_playlist_cur_width2 + 58,
                        )
                        move_label(
                            YukiData.state,
                            int(
                                ((self.windowWidth - YukiData.state.width()) / 2)
                                - (dockWidget_playlist_cur_width2 / 1.7)
                            ),
                            int(
                                (self.windowHeight - YukiData.state.height())
                                - dockWidget_controlPanel.height()
                                - 10
                            ),
                        )
                        h = dockWidget_controlPanel.height()
                        h2 = 20
                else:
                    set_label_width(YukiData.state, self.windowWidth)
                    move_label(
                        YukiData.state,
                        int((self.windowWidth - YukiData.state.width()) / 2),
                        int((self.windowHeight - YukiData.state.height()) - 20),
                    )
                    h = 0
                    h2 = 10
                if dockWidget_playlist.isVisible():
                    if YukiData.settings["panelposition"] in (0, 2):
                        move_label(YukiGUI.lbl2, 0, YukiGUI.lbl2_offset)
                    else:
                        move_label(
                            YukiGUI.lbl2,
                            YukiData.tvguide_lbl.width() + YukiGUI.lbl2.width(),
                            YukiGUI.lbl2_offset,
                        )
                else:
                    move_label(YukiGUI.lbl2, 0, YukiGUI.lbl2_offset)
                if YukiData.state.isVisible():
                    state_h = YukiData.state.height()
                else:
                    state_h = 15
                YukiData.tvguide_lbl.setFixedHeight(
                    (self.windowHeight - state_h - h) - 40 - state_h + h2
                )

            def resizeEvent(self, event):
                try:
                    self.update()
                except Exception:
                    pass
                QtWidgets.QMainWindow.resizeEvent(self, event)

            def closeEvent(self, event1):
                logger.info("Main window closed")
                try:
                    YukiData.player.vo = "null"
                except Exception:
                    pass
                if YukiGUI.streaminfo_win.isVisible():
                    YukiGUI.streaminfo_win.hide()

            def createMenuBar_mw(self):
                self.menu_bar_qt = self.menuBar()
                init_yuki_iptv_menubar(self, app, self.menu_bar_qt)

        def centerwidget(wdg3, offset1=0):
            fg1 = win.container.frameGeometry()
            xg1 = (fg1.width() - wdg3.width()) / 2
            yg1 = (fg1.height() - wdg3.height()) / 2
            wdg3.move(int(xg1), int(yg1) + int(offset1))

        win = MainWindow()
        win.setMinimumSize(1, 1)
        win.setWindowTitle(MAIN_WINDOW_TITLE)
        win.setWindowIcon(YukiGUI.main_icon)

        YukiGUI.create3(win, centerwidget, ICONS_FOLDER)

        window_data = read_option("window")
        if window_data:
            win.setGeometry(
                window_data["x"], window_data["y"], window_data["w"], window_data["h"]
            )
        else:
            YukiData.needs_resize = True
            win.resize(WINDOW_SIZE[0], WINDOW_SIZE[1])
            qr = win.frameGeometry()
            qr.moveCenter(
                QtGui.QScreen.availableGeometry(
                    QtWidgets.QApplication.primaryScreen()
                ).center()
            )
            win.move(qr.topLeft())

        def get_curwindow_pos():
            try:
                win_geometry = win.screen().availableGeometry()
            except Exception:
                win_geometry = QtWidgets.QDesktopWidget().screenGeometry(win)
            win_width = win_geometry.width()
            win_height = win_geometry.height()
            logger.info(f"Screen size: {win_width}x{win_height}")
            return (
                win_width,
                win_height,
            )

        def get_curwindow_pos_actual():
            try:
                win_geometry_1 = win.screen().availableGeometry()
            except Exception:
                win_geometry_1 = QtWidgets.QDesktopWidget().screenGeometry(win)
            return win_geometry_1

        def showLoading2():
            if not YukiGUI.loading2.isVisible():
                centerwidget(YukiGUI.loading2, 50)
                YukiGUI.loading_movie2.stop()
                YukiGUI.loading_movie2.start()
                YukiGUI.loading2.show()

        def hideLoading2():
            if YukiGUI.loading2.isVisible():
                YukiGUI.loading2.hide()
                YukiGUI.loading_movie2.stop()

        YukiData.playing = False
        YukiData.playing_channel = ""
        YukiData.playing_group = -1

        def show_progress(prog):
            YukiGUI.progress.hide()
            YukiGUI.start_label.setText("")
            YukiGUI.start_label.hide()
            YukiGUI.stop_label.setText("")
            YukiGUI.stop_label.hide()

        YukiData.playing_url = ""

        @idle_function
        def set_mpv_title(unused=None):
            try:
                YukiData.player.title = win.windowTitle()
            except Exception:
                pass

        def setChannelText(channelText, do_channel_set=False):
            chTextStrip = channelText.strip()
            if chTextStrip:
                win.setWindowTitle(chTextStrip + " - " + MAIN_WINDOW_TITLE)
            else:
                win.setWindowTitle(MAIN_WINDOW_TITLE)
            set_mpv_title()
            if not do_channel_set:
                YukiGUI.channel.setText(channelText)
            if YukiData.fullscreen and chTextStrip:
                YukiData.state.show()
                YukiData.state.setTextYuki(chTextStrip)
                YukiData.time_stop = time.time() + 1

        YukiData.playing_archive = False

        @idle_function
        def idle_on_metadata(unused=None):
            try:
                YukiData.event_handler.on_metadata()
            except Exception:
                pass

        @async_gui_blocking_function
        def setPlayerSettings(j):
            try:
                logger.info("setPlayerSettings waiting for channel load...")
                try:
                    YukiData.player.wait_until_playing()
                except Exception:
                    pass
                if j == YukiData.playing_channel:
                    logger.info(f"setPlayerSettings '{j}'")
                    idle_on_metadata()
                    if (
                        YukiData.settings["m3u"] in YukiData.channel_sets
                        and j in YukiData.channel_sets[YukiData.settings["m3u"]]
                    ):
                        d = YukiData.channel_sets[YukiData.settings["m3u"]][j]
                        YukiData.player.deinterlace = d["deinterlace"]
                        if "ua" not in d:
                            d["ua"] = ""
                        if "ref" not in d:
                            d["ref"] = ""
                        if "contrast" in d:
                            YukiData.player.contrast = d["contrast"]
                        else:
                            YukiData.player.contrast = 0
                        if "brightness" in d:
                            YukiData.player.brightness = d["brightness"]
                        else:
                            YukiData.player.brightness = 0
                        if "hue" in d:
                            YukiData.player.hue = d["hue"]
                        else:
                            YukiData.player.hue = 0
                        if "saturation" in d:
                            YukiData.player.saturation = d["saturation"]
                        else:
                            YukiData.player.saturation = 0
                        if "gamma" in d:
                            YukiData.player.gamma = d["gamma"]
                        else:
                            YukiData.player.gamma = 0
                        if "videoaspect" in d:
                            setVideoAspect(
                                YukiGUI.videoaspect_vars[
                                    list(YukiGUI.videoaspect_vars)[d["videoaspect"]]
                                ]
                            )
                        else:
                            setVideoAspect(
                                YukiGUI.videoaspect_vars[
                                    YukiGUI.videoaspect_def_choose.itemText(
                                        YukiData.settings["videoaspect"]
                                    )
                                ]
                            )
                        if "zoom" in d:
                            setZoom(
                                YukiGUI.zoom_vars[list(YukiGUI.zoom_vars)[d["zoom"]]]
                            )
                        else:
                            setZoom(
                                YukiGUI.zoom_vars[
                                    YukiGUI.zoom_def_choose.itemText(
                                        YukiData.settings["zoom"]
                                    )
                                ]
                            )
                        if "panscan" in d:
                            setPanscan(d["panscan"])
                        else:
                            setPanscan(YukiData.settings["panscan"])
                    else:
                        YukiData.player.deinterlace = YukiData.settings["deinterlace"]
                        setVideoAspect(
                            YukiGUI.videoaspect_vars[
                                YukiGUI.videoaspect_def_choose.itemText(
                                    YukiData.settings["videoaspect"]
                                )
                            ]
                        )
                        setZoom(
                            YukiGUI.zoom_vars[
                                YukiGUI.zoom_def_choose.itemText(
                                    YukiData.settings["zoom"]
                                )
                            ]
                        )
                        setPanscan(YukiData.settings["panscan"])
                        YukiData.player.gamma = 0
                        YukiData.player.saturation = 0
                        YukiData.player.hue = 0
                        YukiData.player.brightness = 0
                        YukiData.player.contrast = 0
                    # Print settings
                    if YukiData.player.deinterlace:
                        logger.info("Deinterlace: enabled")
                    else:
                        logger.info("Deinterlace: disabled")
                    logger.info(f"Contrast: {YukiData.player.contrast}")
                    logger.info(f"Brightness: {YukiData.player.brightness}")
                    logger.info(f"Hue: {YukiData.player.hue}")
                    logger.info(f"Saturation: {YukiData.player.saturation}")
                    logger.info(f"Gamma: {YukiData.player.gamma}")
                    logger.info(f"Video aspect: {getVideoAspect()}")
                    logger.info(f"Zoom: {YukiData.player.video_zoom}")
                    logger.info(f"Panscan: {YukiData.player.panscan}")
                    try:
                        YukiData.player["cursor-autohide"] = 1000
                        YukiData.player["force-window"] = True
                    except Exception:
                        pass
                    # Restore video / audio / subtitle tracks for channel
                    if YukiData.playing_channel in YukiData.player_tracks:
                        last_track = YukiData.player_tracks[YukiData.playing_channel]
                        if "vid" in last_track:
                            logger.info(
                                f"Restoring last video track: '{last_track['vid']}'"
                            )
                            YukiData.player.vid = last_track["vid"]
                        else:
                            YukiData.player.vid = "auto"
                        if "aid" in last_track:
                            logger.info(
                                f"Restoring last audio track: '{last_track['aid']}'"
                            )
                            YukiData.player.aid = last_track["aid"]
                        else:
                            YukiData.player.aid = "auto"
                        if "sid" in last_track:
                            logger.info(
                                f"Restoring last sub track: '{last_track['sid']}'"
                            )
                            YukiData.player.sid = last_track["sid"]
                        else:
                            YukiData.player.sid = "auto"
                    else:
                        YukiData.player.vid = "auto"
                        YukiData.player.aid = "auto"
                        YukiData.player.sid = "auto"
                    file_loaded_callback()
            except Exception:
                pass

        def itemClicked_event(item, custom_url="", archived=False, is_rewind=False):
            is_ic_ok = True
            try:
                is_ic_ok = item.text() != _("Nothing found")
            except Exception:
                pass
            if is_ic_ok:
                YukiData.playing_archive = archived
                if not archived:
                    YukiData.archive_epg = None
                    YukiGUI.rewind_slider.setValue(100)
                    YukiData.rewind_value = YukiGUI.rewind_slider.value()
                else:
                    if not is_rewind:
                        YukiGUI.rewind_slider.setValue(0)
                        YukiData.rewind_value = YukiGUI.rewind_slider.value()
                try:
                    j = item.data(QtCore.Qt.ItemDataRole.UserRole)
                except Exception:
                    j = item
                if not j:
                    return
                YukiData.playing_channel = j
                YukiData.playing_group = playmode_selector.currentIndex()
                YukiData.item_selected = j
                try:
                    play_url = getArrayItem(j)["url"]
                except Exception:
                    play_url = custom_url
                if archived:
                    play_url = custom_url
                MAX_CHAN_SIZE = 35
                channel_name = j
                if len(channel_name) > MAX_CHAN_SIZE:
                    channel_name = channel_name[: MAX_CHAN_SIZE - 3] + "..."
                setChannelText("  " + channel_name)
                current_prog = None
                jlower = j.lower()
                try:
                    jlower = YukiData.prog_match_arr[jlower]
                except Exception:
                    pass
                if YukiData.settings["epg"] and exists_in_epg(
                    jlower, YukiData.programmes
                ):
                    for pr in get_epg(YukiData.programmes, jlower):
                        if time.time() > pr["start"] and time.time() < pr["stop"]:
                            current_prog = pr
                            break
                YukiData.current_prog1 = current_prog
                show_progress(current_prog)
                if YukiGUI.start_label.isVisible():
                    dockWidget_controlPanel.setFixedHeight(
                        DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH
                    )
                else:
                    dockWidget_controlPanel.setFixedHeight(
                        DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW
                    )
                YukiData.playing = True
                win.update()
                YukiData.playing_url = play_url
                setUrlText()
                ua_choose = def_user_agent
                if (
                    YukiData.settings["m3u"] in YukiData.channel_sets
                    and j in YukiData.channel_sets[YukiData.settings["m3u"]]
                ):
                    ua_choose = YukiData.channel_sets[YukiData.settings["m3u"]][j]["ua"]
                if not custom_url:
                    doPlay(play_url, ua_choose, j)
                else:
                    doPlay(custom_url, ua_choose, j)
                btn_update_click()

        YukiData.item_selected = ""

        def itemSelected_event(item):
            try:
                n_1 = item.data(QtCore.Qt.ItemDataRole.UserRole)
                YukiData.item_selected = n_1
                update_tvguide(n_1)
            except Exception:
                pass

        def mpv_play():
            YukiData.player.pause = not YukiData.player.pause

        def mpv_stop():
            YukiData.playing_channel = ""
            YukiData.playing_group = -1
            YukiData.playing_url = ""
            setUrlText()
            hideLoading()
            setChannelText("")
            YukiData.playing = False
            stopPlayer()
            YukiData.player.loop = True
            YukiData.player.deinterlace = False
            mpv_override_play(str(Path("yuki_iptv", ICONS_FOLDER, "main.png")))
            YukiData.player.pause = True
            YukiGUI.channel.setText(_("No channel selected"))
            YukiGUI.progress.hide()
            YukiGUI.start_label.hide()
            YukiGUI.stop_label.hide()
            YukiGUI.start_label.setText("")
            YukiGUI.stop_label.setText("")
            dockWidget_controlPanel.setFixedHeight(DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW)
            win.update()
            btn_update_click()
            logger.info("redraw_menubar triggered by mpv_stop")
            redraw_menubar()

        def esc_handler():
            if YukiData.fullscreen:
                mpv_fullscreen()

        def get_always_on_top():
            return YukiData.cur_always_on_top_state

        def set_always_on_top(always_on_top_state):
            YukiData.cur_always_on_top_state = always_on_top_state
            logger.debug(f"set_always_on_top: {always_on_top_state}")
            whint1 = QtCore.Qt.WindowType.WindowStaysOnTopHint
            if (always_on_top_state and (win.windowFlags() & whint1)) or (
                not always_on_top_state and (not win.windowFlags() & whint1)
            ):
                logger.debug("set_always_on_top: nothing to do")
                return
            winIsVisible = win.isVisible()
            winPos1 = win.pos()
            if always_on_top_state:
                win.setWindowFlags(
                    win.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint
                )
            else:
                win.setWindowFlags(
                    win.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint
                )
            win.move(winPos1)
            if winIsVisible:
                win.show()
                win.raise_()
                win.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
                win.activateWindow()

        @idle_function
        def enable_always_on_top(unused=None):
            set_always_on_top(True)

        @idle_function
        def disable_always_on_top(unused=None):
            set_always_on_top(False)

        # Always on top
        is_aot = False
        if os.path.isfile(str(Path(LOCAL_DIR, "alwaysontop.json"))):
            try:
                aot_f1 = open(
                    str(Path(LOCAL_DIR, "alwaysontop.json")), encoding="utf-8"
                )
                aot_f1_data = json.loads(aot_f1.read())["alwaysontop"]
                aot_f1.close()
                is_aot = aot_f1_data
            except Exception:
                pass
        if is_aot:
            logger.info("Always on top enabled")
            enable_always_on_top()
        else:
            logger.info("Always on top disabled")

        YukiData.cur_always_on_top_state = is_aot

        YukiData.currentWidthHeight = [
            win.geometry().x(),
            win.geometry().y(),
            win.width(),
            win.height(),
        ]
        YukiData.currentMaximized = win.isMaximized()

        YukiData.isPlaylistVisible = False
        YukiData.isControlPanelVisible = False

        @idle_function
        def mpv_fullscreen(unused=None):
            if not YukiData.fullscreen:
                # Entering fullscreen
                if not YukiData.fullscreen_locked:
                    YukiData.fullscreen_locked = True
                    logger.info("Entering fullscreen started")
                    time01 = time.time()
                    rewind_layout_offset = 10
                    YukiGUI.rewind_layout.setContentsMargins(
                        rewind_layout_offset, 0, rewind_layout_offset - 50, 0
                    )
                    YukiData.isControlPanelVisible = dockWidget_controlPanel.isVisible()
                    YukiData.isPlaylistVisible = dockWidget_playlist.isVisible()
                    setShortcutState(True)
                    YukiData.currentWidthHeight = [
                        win.geometry().x(),
                        win.geometry().y(),
                        win.width(),
                        win.height(),
                    ]
                    YukiData.currentMaximized = win.isMaximized()
                    YukiGUI.channelfilter.usePopup = False
                    win.menu_bar_qt.hide()
                    YukiData.fullscreen = True
                    dockWidget_playlist.hide()
                    YukiGUI.channel.hide()
                    YukiGUI.label_video_data.hide()
                    YukiGUI.label_avsync.hide()
                    for lbl3 in YukiGUI.controlpanel_btns:
                        if lbl3 not in YukiGUI.show_lbls_fullscreen:
                            lbl3.hide()
                    YukiGUI.progress.hide()
                    YukiGUI.start_label.hide()
                    YukiGUI.stop_label.hide()
                    dockWidget_controlPanel.hide()
                    dockWidget_controlPanel.setFixedHeight(
                        DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW
                    )
                    win.update()
                    win.showFullScreen()
                    if YukiData.settings["panelposition"] == 1:
                        tvguide_close_lbl.move(
                            get_curwindow_pos()[0] - YukiData.tvguide_lbl.width() - 40,
                            YukiGUI.tvguide_lbl_offset,
                        )
                    centerwidget(YukiGUI.loading1)
                    centerwidget(YukiGUI.loading2, 50)
                    time02 = time.time() - time01
                    logger.info(
                        f"Entering fullscreen ended, took {round(time02, 2)} seconds"
                    )
                    YukiData.fullscreen_locked = False
            else:
                # Leaving fullscreen
                if not YukiData.fullscreen_locked:
                    YukiData.fullscreen_locked = True
                    logger.info("Leaving fullscreen started")
                    time03 = time.time()
                    YukiGUI.rewind_layout.setContentsMargins(100, 0, 50, 0)
                    setShortcutState(False)
                    if YukiData.state.isVisible() and YukiData.state.text().startswith(
                        _("Volume")
                    ):
                        YukiData.state.hide()
                    win.menu_bar_qt.show()
                    hide_playlist_fullscreen()
                    hide_controlpanel_fullscreen()
                    dockWidget_playlist.setWindowOpacity(1)
                    dockWidget_playlist.hide()
                    dockWidget_controlPanel.setWindowOpacity(1)
                    dockWidget_controlPanel.hide()
                    YukiData.fullscreen = False
                    if YukiData.state.text().endswith(
                        "{} F".format(_("To exit fullscreen mode press"))
                    ):
                        YukiData.state.setTextYuki("")
                        if not YukiData.gl_is_static:
                            YukiData.state.hide()
                            win.update()
                    if (
                        not YukiData.player.pause
                        and YukiData.playing
                        and YukiGUI.start_label.text()
                    ):
                        YukiGUI.progress.show()
                        YukiGUI.start_label.show()
                        YukiGUI.stop_label.show()
                        dockWidget_controlPanel.setFixedHeight(
                            DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH
                        )
                    YukiGUI.label_video_data.show()
                    YukiGUI.label_avsync.show()
                    for lbl3 in YukiGUI.controlpanel_btns:
                        if lbl3 not in YukiGUI.show_lbls_fullscreen:
                            lbl3.show()
                    dockWidget_controlPanel.show()
                    dockWidget_playlist.show()
                    YukiGUI.channel.show()
                    win.update()
                    if not YukiData.currentMaximized:
                        win.showNormal()
                    else:
                        win.showMaximized()
                    win.setGeometry(
                        YukiData.currentWidthHeight[0],
                        YukiData.currentWidthHeight[1],
                        YukiData.currentWidthHeight[2],
                        YukiData.currentWidthHeight[3],
                    )
                    if not YukiData.isPlaylistVisible:
                        show_hide_playlist()
                    if YukiData.settings["panelposition"] == 1:
                        tvguide_close_lbl.move(
                            win.width() - YukiData.tvguide_lbl.width() - 40,
                            YukiGUI.tvguide_lbl_offset,
                        )
                    centerwidget(YukiGUI.loading1)
                    centerwidget(YukiGUI.loading2, 50)
                    if YukiData.isControlPanelVisible:
                        dockWidget_controlPanel.show()
                    else:
                        dockWidget_controlPanel.hide()
                    if YukiData.compact_mode:
                        win.menu_bar_qt.hide()
                        setShortcutState(True)
                    dockWidget_playlist.lower()
                    time04 = time.time() - time03
                    logger.info(
                        f"Leaving fullscreen ended, took {round(time04, 2)} seconds"
                    )
                    YukiData.fullscreen_locked = False
            try:
                YukiData.event_handler.on_fullscreen()
            except Exception:
                pass

        YukiData.old_value = 100

        def is_show_volume():
            showdata = YukiData.fullscreen
            if not YukiData.fullscreen and win.isVisible():
                showdata = not dockWidget_controlPanel.isVisible()
            return showdata and not YukiGUI.controlpanel_widget.isVisible()

        def show_volume(v1):
            if is_show_volume():
                YukiData.state.show()
                if isinstance(v1, str):
                    YukiData.state.setTextYuki(v1)
                else:
                    YukiData.state.setTextYuki("{}: {}%".format(_("Volume"), int(v1)))

        def mpv_mute():
            YukiData.time_stop = time.time() + 3
            if YukiData.player.mute:
                if YukiData.old_value > 50:
                    YukiGUI.btn_volume.setIcon(
                        QtGui.QIcon(str(Path("yuki_iptv", ICONS_FOLDER, "volume.png")))
                    )
                else:
                    YukiGUI.btn_volume.setIcon(
                        QtGui.QIcon(
                            str(Path("yuki_iptv", ICONS_FOLDER, "volume-low.png"))
                        )
                    )
                mpv_override_mute(False)
                YukiGUI.volume_slider.setValue(YukiData.old_value)
                show_volume(YukiData.old_value)
            else:
                YukiGUI.btn_volume.setIcon(
                    QtGui.QIcon(str(Path("yuki_iptv", ICONS_FOLDER, "mute.png")))
                )
                mpv_override_mute(True)
                YukiData.old_value = YukiGUI.volume_slider.value()
                YukiGUI.volume_slider.setValue(0)
                show_volume(_("Volume off"))

        def mpv_volume_set():
            YukiData.time_stop = time.time() + 3
            vol = int(YukiGUI.volume_slider.value())
            try:
                if vol == 0:
                    show_volume(_("Volume off"))
                else:
                    show_volume(vol)
            except NameError:
                pass
            mpv_override_volume(vol)
            if vol == 0:
                mpv_override_mute(True)
                YukiGUI.btn_volume.setIcon(
                    QtGui.QIcon(str(Path("yuki_iptv", ICONS_FOLDER, "mute.png")))
                )
            else:
                mpv_override_mute(False)
                if vol > 50:
                    YukiGUI.btn_volume.setIcon(
                        QtGui.QIcon(str(Path("yuki_iptv", ICONS_FOLDER, "volume.png")))
                    )
                else:
                    YukiGUI.btn_volume.setIcon(
                        QtGui.QIcon(
                            str(Path("yuki_iptv", ICONS_FOLDER, "volume-low.png"))
                        )
                    )

        class PlaylistDockWidget(QtWidgets.QDockWidget):
            def enterEvent(self, event4):
                YukiData.check_playlist_visible = True

            def leaveEvent(self, event4):
                YukiData.check_playlist_visible = False

        dockWidget_playlist = PlaylistDockWidget(win)

        win.listWidget = QtWidgets.QListWidget()
        win.moviesWidget = QtWidgets.QListWidget()
        win.seriesWidget = QtWidgets.QListWidget()

        def tvguide_close_lbl_func(arg):
            hide_tvguide()

        YukiData.tvguide_lbl = YukiGUI.ScrollableLabel(win)
        YukiData.tvguide_lbl.move(0, YukiGUI.tvguide_lbl_offset)
        YukiData.tvguide_lbl.setFixedWidth(TVGUIDE_WIDTH)
        YukiData.tvguide_lbl.hide()

        class ClickableLabel(QtWidgets.QLabel):
            def __init__(self, whenClicked, win, parent=None):
                QtWidgets.QLabel.__init__(self, win)
                self._whenClicked = whenClicked

            def mouseReleaseEvent(self, event):
                self._whenClicked(event)

        tvguide_close_lbl = ClickableLabel(tvguide_close_lbl_func, win)
        tvguide_close_lbl.setPixmap(
            QtGui.QIcon(str(Path("yuki_iptv", ICONS_FOLDER, "close.png"))).pixmap(
                32, 32
            )
        )
        tvguide_close_lbl.setStyleSheet(
            "background-color: {};".format(
                "black" if YukiData.use_dark_icon_theme else "white"
            )
        )
        tvguide_close_lbl.resize(32, 32)
        if YukiData.settings["panelposition"] in (0, 2):
            tvguide_close_lbl.move(
                YukiData.tvguide_lbl.width() + 5, YukiGUI.tvguide_lbl_offset
            )
        else:
            tvguide_close_lbl.move(
                win.width() - YukiData.tvguide_lbl.width() - 40,
                YukiGUI.tvguide_lbl_offset,
            )
            YukiGUI.lbl2.move(
                YukiData.tvguide_lbl.width() + YukiGUI.lbl2.width(), YukiGUI.lbl2_offset
            )
        tvguide_close_lbl.hide()

        YukiData.current_group = _("All channels")

        Path(LOCAL_DIR, "logo_cache").mkdir(parents=True, exist_ok=True)

        def get_of_txt(of_num):
            # try:
            #     of_txt = gettext.ngettext("of %d", "", of_num) % of_num
            # except Exception:
            #     of_txt = f"of {of_num}"
            # return of_txt
            return _("of") + " " + str(of_num)

        YukiData.prog_match_arr = {}

        YukiData.channel_logos_request_old = {}
        YukiData.channel_logos_process = None
        YukiData.mp_manager_dict["logos_inprogress"] = False
        YukiData.mp_manager_dict["logos_completed"] = False
        YukiData.mp_manager_dict["logosmovie_inprogress"] = False
        YukiData.mp_manager_dict["logosmovie_completed"] = False
        logos_cache = {}

        def get_pixmap_from_filename(pixmap_filename):
            if pixmap_filename in logos_cache:
                return logos_cache[pixmap_filename]
            else:
                try:
                    if os.path.isfile(pixmap_filename):
                        icon_pixmap = QtGui.QIcon(pixmap_filename)
                        logos_cache[pixmap_filename] = icon_pixmap
                        icon_pixmap = None
                        return logos_cache[pixmap_filename]
                    else:
                        return None
                except Exception:
                    return None

        YukiData.timer_logos_update_lock = False

        def timer_logos_update():
            try:
                if not YukiData.timer_logos_update_lock:
                    YukiData.timer_logos_update_lock = True
                    if YukiData.mp_manager_dict["logos_completed"]:
                        YukiData.mp_manager_dict["logos_completed"] = False
                        btn_update_click()
                    if YukiData.mp_manager_dict["logosmovie_completed"]:
                        YukiData.mp_manager_dict["logosmovie_completed"] = False
                        update_movie_icons()
                    YukiData.timer_logos_update_lock = False
            except Exception:
                pass

        custom_logos_enabled = os.path.isdir(Path(LOCAL_DIR, "logos")) or os.path.isdir(
            Path("..", "..", "share", "yuki-iptv", "channel_logos")
        )

        all_channels_lang = _("All channels")
        favourites_lang = _("Favourites")

        def get_page_count(array_len):
            return max(1, math.ceil(array_len / 100))

        def generate_channels():
            channel_logos_request = {}

            try:
                idx = (YukiGUI.page_box.value() - 1) * 100
            except Exception:
                idx = 0
            try:
                filter_txt = YukiGUI.channelfilter.text()
            except Exception:
                filter_txt = ""

            # Group and favourites filter
            array_filtered = []
            for j1 in array_sorted:
                group1 = YukiData.array[j1]["tvg-group"]
                if YukiData.current_group != all_channels_lang:
                    if YukiData.current_group == favourites_lang:
                        if j1 not in YukiData.favourite_sets:
                            continue
                    else:
                        if group1 != YukiData.current_group:
                            continue
                array_filtered.append(j1)

            ch_array = [
                x13
                for x13 in array_filtered
                if unidecode(filter_txt).lower().strip()
                in unidecode(x13).lower().strip()
            ]
            ch_array = ch_array[idx : idx + 100]
            try:
                if filter_txt:
                    YukiGUI.page_box.setMaximum(get_page_count(len(ch_array)))
                    YukiGUI.of_lbl.setText(get_of_txt(get_page_count(len(ch_array))))
                else:
                    YukiGUI.page_box.setMaximum(get_page_count(len(array_filtered)))
                    YukiGUI.of_lbl.setText(
                        get_of_txt(get_page_count(len(array_filtered)))
                    )
            except Exception:
                pass
            res = {}
            k0 = -1
            k = 0
            for i in ch_array:
                k0 += 1
                k += 1
                prog = ""
                prog_desc = ""
                is_epgname_found = False

                # First, match EPG name from settings
                if (
                    YukiData.settings["m3u"] in YukiData.channel_sets
                    and i in YukiData.channel_sets[YukiData.settings["m3u"]]
                ):
                    if "epgname" in YukiData.channel_sets[YukiData.settings["m3u"]][i]:
                        if YukiData.channel_sets[YukiData.settings["m3u"]][i][
                            "epgname"
                        ]:
                            epg_name = YukiData.channel_sets[YukiData.settings["m3u"]][
                                i
                            ]["epgname"]
                            if exists_in_epg(
                                str(epg_name).lower(), YukiData.programmes
                            ):
                                prog_search = str(epg_name).lower()
                                is_epgname_found = True

                # Second, match from tvg-id
                if not is_epgname_found:
                    if YukiData.array[i]["tvg-ID"]:
                        if str(YukiData.array[i]["tvg-ID"]) in YukiData.prog_ids:
                            prog_search_lst = YukiData.prog_ids[
                                str(YukiData.array[i]["tvg-ID"])
                            ]
                            if prog_search_lst:
                                prog_search = prog_search_lst[0].lower()
                                is_epgname_found = True

                # Third, match from tvg-name
                if not is_epgname_found:
                    if YukiData.array[i]["tvg-name"]:
                        if exists_in_epg(
                            str(YukiData.array[i]["tvg-name"]).lower(),
                            YukiData.programmes,
                        ):
                            prog_search = str(YukiData.array[i]["tvg-name"]).lower()
                            is_epgname_found = True
                        else:
                            spaces_replaced_name = YukiData.array[i][
                                "tvg-name"
                            ].replace(" ", "_")
                            if exists_in_epg(
                                str(spaces_replaced_name).lower(), YukiData.programmes
                            ):
                                prog_search = str(spaces_replaced_name).lower()
                                is_epgname_found = True

                # Last, match from channel name
                if not is_epgname_found:
                    prog_search = i.lower()
                    is_epgname_found = True

                YukiData.prog_match_arr[i.lower()] = prog_search
                if exists_in_epg(prog_search, YukiData.programmes):
                    current_prog = {"start": 0, "stop": 0, "title": "", "desc": ""}
                    for pr in get_epg(YukiData.programmes, prog_search):
                        if time.time() > pr["start"] and time.time() < pr["stop"]:
                            current_prog = pr
                            break
                    if current_prog["start"] != 0:
                        start_time = datetime.datetime.fromtimestamp(
                            current_prog["start"]
                        ).strftime("%H:%M")
                        stop_time = datetime.datetime.fromtimestamp(
                            current_prog["stop"]
                        ).strftime("%H:%M")
                        t_t = time.time()
                        percentage = round(
                            (t_t - current_prog["start"])
                            / (current_prog["stop"] - current_prog["start"])
                            * 100
                        )
                        if YukiData.settings["hideepgpercentage"]:
                            prog = current_prog["title"]
                        else:
                            prog = str(percentage) + "% " + current_prog["title"]
                        try:
                            if current_prog["desc"]:
                                prog_desc = "\n\n" + textwrap.fill(
                                    current_prog["desc"], 100
                                )
                            else:
                                prog_desc = ""
                        except Exception:
                            prog_desc = ""
                    else:
                        start_time = ""
                        stop_time = ""
                        t_t = time.time()
                        percentage = 0
                        prog = ""
                        prog_desc = ""
                MyPlaylistWidget = YukiGUI.PlaylistWidget(
                    YukiGUI, YukiData.settings["hidechannellogos"]
                )
                CHANNEL_TITLE_MAX_SIZE = 43
                channel_name = i

                original_channel_name = channel_name

                if YukiData.settings["channellogos"] != 3:
                    try:
                        channel_logo1 = ""
                        if "tvg-logo" in YukiData.array[i]:
                            channel_logo1 = YukiData.array[i]["tvg-logo"]

                        if (
                            custom_logos_enabled
                            and not channel_logo1
                            and "channel-logo-file-checked" not in YukiData.array[i]
                        ):
                            YukiData.array[i]["channel-logo-file-checked"] = True
                            custom_channel_logo = get_custom_channel_logo(i)
                            if custom_channel_logo:
                                channel_logo1 = custom_channel_logo
                                YukiData.array[i]["tvg-logo"] = custom_channel_logo

                        epg_logo1 = ""
                        if prog_search in YukiData.epg_icons:
                            epg_logo1 = YukiData.epg_icons[prog_search]

                        req_data_ua, req_data_ref = get_ua_ref_for_channel(
                            original_channel_name
                        )
                        channel_logos_request[YukiData.array[i]["title"]] = [
                            channel_logo1,
                            epg_logo1,
                            req_data_ua,
                            req_data_ref,
                        ]
                    except Exception:
                        logger.warning(f"Exception in channel logos (channel '{i}')")
                        logger.warning(traceback.format_exc())

                if len(channel_name) > CHANNEL_TITLE_MAX_SIZE:
                    channel_name = channel_name[0:CHANNEL_TITLE_MAX_SIZE] + "..."
                unicode_play_symbol = chr(9654) + " "
                append_symbol = ""
                if YukiData.playing_channel == channel_name:
                    append_symbol = unicode_play_symbol
                MyPlaylistWidget.name_label.setText(
                    append_symbol + str(k) + ". " + channel_name
                )
                MAX_SIZE = 28
                orig_prog = prog
                try:
                    tooltip_group = "{}: {}".format(
                        _("Group"), YukiData.array[i]["tvg-group"]
                    )
                except Exception:
                    tooltip_group = "{}: {}".format(_("Group"), _("All channels"))
                if len(prog) > MAX_SIZE:
                    prog = prog[0:MAX_SIZE] + "..."
                if (
                    exists_in_epg(prog_search, YukiData.programmes)
                    and orig_prog
                    and not YukiData.settings["hideepgfromplaylist"]
                ):
                    MyPlaylistWidget.setDescription(
                        prog,
                        (
                            f"<b>{i}</b>" + f"<br>{tooltip_group}<br><br>"
                            "<i>" + orig_prog + "</i>" + prog_desc
                        ).replace("\n", "<br>"),
                    )
                    MyPlaylistWidget.showDescription()
                    try:
                        if start_time:
                            MyPlaylistWidget.setProgress(int(percentage))
                        else:
                            MyPlaylistWidget.hideProgress()
                    except Exception:
                        logger.warning("Async EPG load problem, ignoring")
                else:
                    MyPlaylistWidget.setDescription(
                        "", f"<b>{i}</b><br>{tooltip_group}"
                    )
                    MyPlaylistWidget.hideProgress()
                    MyPlaylistWidget.hideDescription()

                MyPlaylistWidget.setIcon(YukiGUI.tv_icon)

                if YukiData.settings["channellogos"] != 3:  # Do not load any logos
                    try:
                        if (
                            f"LOGO:::{original_channel_name}"
                            in YukiData.mp_manager_dict
                        ):
                            if YukiData.settings["channellogos"] == 0:  # Prefer M3U
                                first_loaded = False
                                if YukiData.mp_manager_dict[
                                    f"LOGO:::{original_channel_name}"
                                ][0]:
                                    channel_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGO:::{original_channel_name}"
                                        ][0]
                                    )
                                    if channel_logo:
                                        first_loaded = True
                                        MyPlaylistWidget.setIcon(channel_logo)
                                if not first_loaded:
                                    channel_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGO:::{original_channel_name}"
                                        ][1]
                                    )
                                    if channel_logo:
                                        MyPlaylistWidget.setIcon(channel_logo)
                            elif YukiData.settings["channellogos"] == 1:  # Prefer EPG
                                first_loaded = False
                                if YukiData.mp_manager_dict[
                                    f"LOGO:::{original_channel_name}"
                                ][1]:
                                    channel_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGO:::{original_channel_name}"
                                        ][1]
                                    )
                                    if channel_logo:
                                        first_loaded = True
                                        MyPlaylistWidget.setIcon(channel_logo)
                                if not first_loaded:
                                    channel_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGO:::{original_channel_name}"
                                        ][0]
                                    )
                                    if channel_logo:
                                        MyPlaylistWidget.setIcon(channel_logo)
                            elif (
                                YukiData.settings["channellogos"] == 2
                            ):  # Do not load from EPG (only M3U)
                                if YukiData.mp_manager_dict[
                                    f"LOGO:::{original_channel_name}"
                                ][0]:
                                    channel_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGO:::{original_channel_name}"
                                        ][0]
                                    )
                                    if channel_logo:
                                        MyPlaylistWidget.setIcon(channel_logo)
                    except Exception:
                        logger.warning("Set channel logos failed with exception")
                        logger.warning(traceback.format_exc())

                # Create QListWidgetItem
                myQListWidgetItem = QtWidgets.QListWidgetItem()
                myQListWidgetItem.setData(QtCore.Qt.ItemDataRole.UserRole, i)
                # Set size hint
                myQListWidgetItem.setSizeHint(MyPlaylistWidget.sizeHint())
                res[k0] = [myQListWidgetItem, MyPlaylistWidget, k0, i]
            j1 = YukiData.playing_channel.lower()
            try:
                j1 = YukiData.prog_match_arr[j1]
            except Exception:
                pass
            if j1:
                current_channel = None
                try:
                    cur = get_epg(YukiData.programmes, j1)
                    for pr in cur:
                        if time.time() > pr["start"] and time.time() < pr["stop"]:
                            current_channel = pr
                            break
                except Exception:
                    pass
                show_progress(current_channel)

            # Fetch channel logos
            try:
                if YukiData.settings["channellogos"] != 3:
                    if channel_logos_request != YukiData.channel_logos_request_old:
                        YukiData.channel_logos_request_old = channel_logos_request
                        logger.debug("Channel logos request")
                        if (
                            YukiData.channel_logos_process
                            and YukiData.channel_logos_process.is_alive()
                        ):
                            # logger.debug(
                            #     "Old channel logos request found, stopping it"
                            # )
                            YukiData.channel_logos_process.kill()
                        YukiData.channel_logos_process = get_context("spawn").Process(
                            name="[yuki-iptv] channel_logos_worker",
                            target=channel_logos_worker,
                            daemon=True,
                            args=(
                                loglevel,
                                channel_logos_request,
                                YukiData.mp_manager_dict,
                            ),
                        )
                        YukiData.channel_logos_process.start()
            except Exception:
                logger.warning("Fetch channel logos failed with exception:")
                logger.warning(traceback.format_exc())

            return res

        YukiData.row0 = -1

        def redraw_channels():
            channels_1 = generate_channels()
            update_tvguide()
            YukiData.row0 = win.listWidget.currentRow()
            val0 = win.listWidget.verticalScrollBar().value()
            win.listWidget.clear()
            if channels_1:
                for channel_1 in channels_1.values():
                    channel_3 = channel_1
                    win.listWidget.addItem(channel_3[0])
                    win.listWidget.setItemWidget(channel_3[0], channel_3[1])
            else:
                win.listWidget.addItem(_("Nothing found"))
            win.listWidget.setCurrentRow(YukiData.row0)
            win.listWidget.verticalScrollBar().setValue(val0)

        YukiData.first_change = False

        def group_change(self):
            comm_instance.comboboxIndex = YukiData.combobox.currentIndex()
            YukiData.current_group = groups[self]
            if not YukiData.first_change:
                YukiData.first_change = True
            else:
                btn_update_click()

        YukiGUI.btn_update.clicked.connect(redraw_channels)

        YukiData.first_playmode_change = False

        def playmode_change(self=False):
            YukiData.playmodeIndex = playmode_selector.currentIndex()
            if not YukiData.first_playmode_change:
                YukiData.first_playmode_change = True
            else:
                tv_widgets = [YukiData.combobox, win.listWidget, YukiGUI.widget4]
                movies_widgets = [movies_combobox, win.moviesWidget]
                series_widgets = [win.seriesWidget]
                # Clear search text when play mode is changed
                # (TV channels, movies, series)
                try:
                    YukiGUI.channelfilter.setText("")
                    YukiGUI.channelfiltersearch.click()
                except Exception:
                    pass
                if playmode_selector.currentIndex() == 0:
                    # TV channels
                    for lbl5 in movies_widgets:
                        lbl5.hide()
                    for lbl6 in series_widgets:
                        lbl6.hide()
                    for lbl4 in tv_widgets:
                        lbl4.show()
                    try:
                        YukiGUI.channelfilter.setPlaceholderText(_("Search channel"))
                    except Exception:
                        pass
                if playmode_selector.currentIndex() == 1:
                    # Movies
                    for lbl4 in tv_widgets:
                        lbl4.hide()
                    for lbl6 in series_widgets:
                        lbl6.hide()
                    for lbl5 in movies_widgets:
                        lbl5.show()
                    try:
                        YukiGUI.channelfilter.setPlaceholderText(_("Search movie"))
                    except Exception:
                        pass
                if playmode_selector.currentIndex() == 2:
                    # Series
                    for lbl4 in tv_widgets:
                        lbl4.hide()
                    for lbl5 in movies_widgets:
                        lbl5.hide()
                    for lbl6 in series_widgets:
                        lbl6.show()
                    try:
                        YukiGUI.channelfilter.setPlaceholderText(_("Search series"))
                    except Exception:
                        pass

        channels = generate_channels()
        for channel in channels:
            # Add QListWidgetItem into QListWidget
            win.listWidget.addItem(channels[channel][0])
            win.listWidget.setItemWidget(channels[channel][0], channels[channel][1])

        def sort_upbtn_clicked():
            curIndex = YukiGUI.sort_list.currentRow()
            if curIndex != -1 and curIndex > 0:
                curItem = YukiGUI.sort_list.takeItem(curIndex)
                YukiGUI.sort_list.insertItem(curIndex - 1, curItem)
                YukiGUI.sort_list.setCurrentRow(curIndex - 1)

        def sort_downbtn_clicked():
            curIndex1 = YukiGUI.sort_list.currentRow()
            if curIndex1 != -1 and curIndex1 < YukiGUI.sort_list.count() - 1:
                curItem1 = YukiGUI.sort_list.takeItem(curIndex1)
                YukiGUI.sort_list.insertItem(curIndex1 + 1, curItem1)
                YukiGUI.sort_list.setCurrentRow(curIndex1 + 1)

        YukiGUI.create_sort_widgets2(ICONS_FOLDER)

        YukiGUI.sort_upbtn.clicked.connect(sort_upbtn_clicked)
        YukiGUI.sort_downbtn.clicked.connect(sort_downbtn_clicked)

        def tvguide_context_menu():
            update_tvguide()
            YukiData.tvguide_lbl.show()
            tvguide_close_lbl.show()

        def settings_context_menu():
            if YukiGUI.channels_win.isVisible():
                YukiGUI.channels_win.close()
            YukiGUI.title.setText(str(YukiData.item_selected))
            if (
                YukiData.settings["m3u"] in YukiData.channel_sets
                and YukiData.item_selected
                in YukiData.channel_sets[YukiData.settings["m3u"]]
            ):
                YukiGUI.deinterlace_chk.setChecked(
                    YukiData.channel_sets[YukiData.settings["m3u"]][
                        YukiData.item_selected
                    ]["deinterlace"]
                )
                try:
                    YukiGUI.useragent_choose.setText(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["ua"]
                    )
                except Exception:
                    YukiGUI.useragent_choose.setText("")
                try:
                    YukiGUI.referer_choose_custom.setText(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["ref"]
                    )
                except Exception:
                    YukiGUI.referer_choose_custom.setText("")
                try:
                    YukiGUI.group_text.setText(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["group"]
                    )
                except Exception:
                    YukiGUI.group_text.setText("")
                try:
                    YukiGUI.hidden_chk.setChecked(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["hidden"]
                    )
                except Exception:
                    YukiGUI.hidden_chk.setChecked(False)
                try:
                    YukiGUI.contrast_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["contrast"]
                    )
                except Exception:
                    YukiGUI.contrast_choose.setValue(0)
                try:
                    YukiGUI.brightness_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["brightness"]
                    )
                except Exception:
                    YukiGUI.brightness_choose.setValue(0)
                try:
                    YukiGUI.hue_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["hue"]
                    )
                except Exception:
                    YukiGUI.hue_choose.setValue(0)
                try:
                    YukiGUI.saturation_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["saturation"]
                    )
                except Exception:
                    YukiGUI.saturation_choose.setValue(0)
                try:
                    YukiGUI.gamma_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["gamma"]
                    )
                except Exception:
                    YukiGUI.gamma_choose.setValue(0)
                try:
                    YukiGUI.videoaspect_choose.setCurrentIndex(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["videoaspect"]
                    )
                except Exception:
                    YukiGUI.videoaspect_choose.setCurrentIndex(0)
                try:
                    YukiGUI.zoom_choose.setCurrentIndex(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["zoom"]
                    )
                except Exception:
                    YukiGUI.zoom_choose.setCurrentIndex(0)
                try:
                    YukiGUI.panscan_choose.setValue(
                        YukiData.channel_sets[YukiData.settings["m3u"]][
                            YukiData.item_selected
                        ]["panscan"]
                    )
                except Exception:
                    YukiGUI.panscan_choose.setValue(0)
                try:
                    epgname_saved = YukiData.channel_sets[YukiData.settings["m3u"]][
                        YukiData.item_selected
                    ]["epgname"]
                    if not epgname_saved:
                        epgname_saved = _("Default")
                    YukiGUI.epgname_lbl.setText(epgname_saved)
                except Exception:
                    YukiGUI.epgname_lbl.setText(_("Default"))
            else:
                YukiGUI.deinterlace_chk.setChecked(YukiData.settings["deinterlace"])
                YukiGUI.hidden_chk.setChecked(False)
                YukiGUI.contrast_choose.setValue(0)
                YukiGUI.brightness_choose.setValue(0)
                YukiGUI.hue_choose.setValue(0)
                YukiGUI.saturation_choose.setValue(0)
                YukiGUI.gamma_choose.setValue(0)
                YukiGUI.videoaspect_choose.setCurrentIndex(0)
                YukiGUI.zoom_choose.setCurrentIndex(0)
                YukiGUI.panscan_choose.setValue(0)
                YukiGUI.useragent_choose.setText("")
                YukiGUI.referer_choose_custom.setText("")
                YukiGUI.group_text.setText("")
                YukiGUI.epgname_lbl.setText(_("Default"))
            moveWindowToCenter(YukiGUI.channels_win)
            YukiGUI.channels_win.show()

        def tvguide_favourites_add():
            if YukiData.item_selected in YukiData.favourite_sets:
                isdelete_fav_msg = QtWidgets.QMessageBox.question(
                    None,
                    MAIN_WINDOW_TITLE,
                    str(_("Delete from favourites")) + "?",
                    QtWidgets.QMessageBox.StandardButton.Yes
                    | QtWidgets.QMessageBox.StandardButton.No,
                    QtWidgets.QMessageBox.StandardButton.Yes,
                )
                if isdelete_fav_msg == QtWidgets.QMessageBox.StandardButton.Yes:
                    YukiData.favourite_sets.remove(YukiData.item_selected)
            else:
                YukiData.favourite_sets.append(YukiData.item_selected)
            save_favourite_sets()
            btn_update_click()

        def open_external_player():
            moveWindowToCenter(YukiGUI.ext_win)
            YukiGUI.ext_win.show()

        def tvguide_hide():
            YukiData.tvguide_lbl.setText("")
            YukiData.tvguide_lbl.hide()
            tvguide_close_lbl.hide()

        def favoritesplaylistsep_add():
            ps_data = getArrayItem(YukiData.item_selected)
            str1 = "#EXTINF:-1"
            if ps_data["tvg-name"]:
                str1 += f" tvg-name=\"{ps_data['tvg-name']}\""
            if ps_data["tvg-ID"]:
                str1 += f" tvg-id=\"{ps_data['tvg-ID']}\""
            if ps_data["tvg-logo"]:
                str1 += f" tvg-logo=\"{ps_data['tvg-logo']}\""
            if ps_data["tvg-group"]:
                str1 += f" tvg-group=\"{ps_data['tvg-group']}\""
            if ps_data["tvg-url"]:
                str1 += f" tvg-url=\"{ps_data['tvg-url']}\""
            else:
                str1 += f" tvg-url=\"{YukiData.settings['epg']}\""
            if ps_data["catchup"]:
                str1 += f" catchup=\"{ps_data['catchup']}\""
            if ps_data["catchup-source"]:
                str1 += f" catchup-source=\"{ps_data['catchup-source']}\""
            if ps_data["catchup-days"]:
                str1 += f" catchup-days=\"{ps_data['catchup-days']}\""

            str_append = ""
            if ps_data["useragent"]:
                str_append += f"#EXTVLCOPT:http-user-agent={ps_data['useragent']}\n"
            if ps_data["referer"]:
                str_append += f"#EXTVLCOPT:http-referrer={ps_data['referer']}\n"

            str1 += f",{YukiData.item_selected}\n{str_append}{ps_data['url']}\n"
            file03 = open(str(Path(LOCAL_DIR, "favplaylist.m3u")), encoding="utf8")
            file03_contents = file03.read()
            file03.close()
            if file03_contents == "#EXTM3U\n#EXTINF:-1,-\nhttp://255.255.255.255\n":
                file04 = open(
                    str(Path(LOCAL_DIR, "favplaylist.m3u")), "w", encoding="utf8"
                )
                file04.write("#EXTM3U\n" + str1)
                file04.close()
            else:
                if str1 in file03_contents:
                    playlistsep_del_msg = QtWidgets.QMessageBox.question(
                        None,
                        MAIN_WINDOW_TITLE,
                        _("Remove channel from Favourites+?"),
                        QtWidgets.QMessageBox.StandardButton.Yes
                        | QtWidgets.QMessageBox.StandardButton.No,
                        QtWidgets.QMessageBox.StandardButton.Yes,
                    )
                    if playlistsep_del_msg == QtWidgets.QMessageBox.StandardButton.Yes:
                        new_data = file03_contents.replace(str1, "")
                        if new_data == "#EXTM3U\n":
                            new_data = "#EXTM3U\n#EXTINF:-1,-\nhttp://255.255.255.255\n"
                        file05 = open(
                            str(Path(LOCAL_DIR, "favplaylist.m3u")),
                            "w",
                            encoding="utf8",
                        )
                        file05.write(new_data)
                        file05.close()
                else:
                    file02 = open(
                        str(Path(LOCAL_DIR, "favplaylist.m3u")), "w", encoding="utf8"
                    )
                    file02.write(file03_contents + str1)
                    file02.close()

        def show_context_menu(pos):
            is_continue = True
            try:
                is_continue = win.listWidget.selectedItems()[0].text() != _(
                    "Nothing found"
                )
            except Exception:
                pass
            try:
                if is_continue:
                    self = win.listWidget
                    itemSelected_event(self.selectedItems()[0])
                    menu = QtWidgets.QMenu()
                    menu.addAction(_("TV guide"), tvguide_context_menu)
                    menu.addAction(_("Hide TV guide"), tvguide_hide)
                    menu.addAction(_("Favourites"), tvguide_favourites_add)
                    menu.addAction(
                        _("Favourites+ (separate playlist)"), favoritesplaylistsep_add
                    )
                    menu.addAction(_("Open in external player"), open_external_player)
                    menu.addAction(_("Video settings"), settings_context_menu)
                    _exec(menu, self.mapToGlobal(pos))
            except Exception:
                pass

        win.listWidget.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        win.listWidget.customContextMenuRequested.connect(show_context_menu)
        win.listWidget.currentItemChanged.connect(itemSelected_event)
        win.listWidget.itemClicked.connect(itemSelected_event)
        win.listWidget.itemDoubleClicked.connect(itemClicked_event)

        def enterPressed():
            currentItem1 = win.listWidget.currentItem()
            if currentItem1:
                itemClicked_event(currentItem1)

        shortcuts = {}
        shortcuts_return = QShortcut(
            QtGui.QKeySequence(QtCore.Qt.Key.Key_Return),
            win.listWidget,
            activated=enterPressed,
        )

        def get_movie_text(movie_1):
            movie_1_txt = ""
            try:
                movie_1_txt = movie_1.text()
            except Exception:
                pass
            try:
                movie_1_txt = movie_1.data(QtCore.Qt.ItemDataRole.UserRole)
            except Exception:
                pass
            if not movie_1_txt:
                movie_1_txt = ""
            return movie_1_txt

        def channelfilter_do():
            try:
                filter_txt1 = YukiGUI.channelfilter.text()
            except Exception:
                filter_txt1 = ""
            if YukiData.playmodeIndex == 0:  # TV channels
                btn_update_click()
            elif YukiData.playmodeIndex == 1:  # Movies
                for item3 in range(win.moviesWidget.count()):
                    if (
                        unidecode(filter_txt1).lower().strip()
                        in unidecode(get_movie_text(win.moviesWidget.item(item3)))
                        .lower()
                        .strip()
                    ):
                        win.moviesWidget.item(item3).setHidden(False)
                    else:
                        win.moviesWidget.item(item3).setHidden(True)
            elif YukiData.playmodeIndex == 2:  # Series
                try:
                    redraw_series()
                except Exception:
                    logger.warning("redraw_series FAILED")
                for item4 in range(win.seriesWidget.count()):
                    if (
                        unidecode(filter_txt1).lower().strip()
                        in unidecode(win.seriesWidget.item(item4).text())
                        .lower()
                        .strip()
                    ):
                        win.seriesWidget.item(item4).setHidden(False)
                    else:
                        win.seriesWidget.item(item4).setHidden(True)

        loading = QtWidgets.QLabel(_("Loading..."))
        loading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        loading.setStyleSheet("color: #778a30")
        hideLoading()

        loading.setFont(YukiGUI.font_12_bold)
        YukiData.combobox = QtWidgets.QComboBox()
        YukiData.combobox.currentIndexChanged.connect(group_change)
        for group in groups:
            YukiData.combobox.addItem(group)

        YukiData.currentMoviesGroup = {}

        YukiData.movie_logos_request_old = {}
        YukiData.movie_logos_process = None

        def update_movie_icons():
            if YukiData.settings["channellogos"] != 3:  # Do not load any logos
                try:
                    for item4 in range(win.moviesWidget.count()):
                        movie_name = get_movie_text(win.moviesWidget.item(item4))
                        if movie_name:
                            if f"LOGOmovie:::{movie_name}" in YukiData.mp_manager_dict:
                                if YukiData.mp_manager_dict[
                                    f"LOGOmovie:::{movie_name}"
                                ][0]:
                                    movie_logo = get_pixmap_from_filename(
                                        YukiData.mp_manager_dict[
                                            f"LOGOmovie:::{movie_name}"
                                        ][0]
                                    )
                                    if movie_logo:
                                        win.moviesWidget.itemWidget(
                                            win.moviesWidget.item(item4)
                                        ).setIcon(movie_logo)
                except Exception:
                    logger.warning("Set movie logos failed with exception")
                    logger.warning(traceback.format_exc())

        def movies_group_change():
            if YukiData.movies:
                current_movies_group = movies_combobox.currentText()
                if current_movies_group:
                    win.moviesWidget.clear()
                    YukiData.currentMoviesGroup = {}
                    movie_logos_request = {}
                    for movies1 in YukiData.movies:
                        if "tvg-group" in YukiData.movies[movies1]:
                            if (
                                YukiData.movies[movies1]["tvg-group"]
                                == current_movies_group
                            ):
                                MovieWidget = YukiGUI.PlaylistWidget(
                                    YukiGUI, YukiData.settings["hidechannellogos"]
                                )
                                MovieWidget.name_label.setText(
                                    YukiData.movies[movies1]["title"]
                                )
                                MovieWidget.hideProgress()
                                MovieWidget.hideDescription()
                                MovieWidget.setIcon(YukiGUI.movie_icon)
                                # Create QListWidgetItem
                                myMovieQListWidgetItem = QtWidgets.QListWidgetItem()
                                myMovieQListWidgetItem.setData(
                                    QtCore.Qt.ItemDataRole.UserRole,
                                    YukiData.movies[movies1]["title"],
                                )
                                # Set size hint
                                myMovieQListWidgetItem.setSizeHint(
                                    MovieWidget.sizeHint()
                                )
                                win.moviesWidget.addItem(myMovieQListWidgetItem)
                                win.moviesWidget.setItemWidget(
                                    myMovieQListWidgetItem, MovieWidget
                                )
                                YukiData.currentMoviesGroup[
                                    YukiData.movies[movies1]["title"]
                                ] = YukiData.movies[movies1]
                                req_data_ua1, req_data_ref1 = get_ua_ref_for_channel(
                                    YukiData.movies[movies1]["title"]
                                )
                                movie_logo1 = ""
                                if "tvg-logo" in YukiData.movies[movies1]:
                                    movie_logo1 = YukiData.movies[movies1]["tvg-logo"]
                                movie_logos_request[
                                    YukiData.movies[movies1]["title"]
                                ] = [
                                    movie_logo1,
                                    "",
                                    req_data_ua1,
                                    req_data_ref1,
                                ]
                    # Fetch movie logos
                    try:
                        if YukiData.settings["channellogos"] != 3:
                            if movie_logos_request != YukiData.movie_logos_request_old:
                                YukiData.movie_logos_request_old = movie_logos_request
                                logger.debug("Movie logos request")
                                if (
                                    YukiData.movie_logos_process
                                    and YukiData.movie_logos_process.is_alive()
                                ):
                                    # logger.debug(
                                    #     "Old movie logos request found, stopping it"
                                    # )
                                    YukiData.movie_logos_process.kill()
                                YukiData.movie_logos_process = get_context(
                                    "spawn"
                                ).Process(
                                    name="[yuki-iptv] channel_logos_worker_for_movie",
                                    target=channel_logos_worker,
                                    daemon=True,
                                    args=(
                                        loglevel,
                                        movie_logos_request,
                                        YukiData.mp_manager_dict,
                                        "movie",
                                    ),
                                )
                                YukiData.movie_logos_process.start()
                    except Exception:
                        logger.warning("Fetch movie logos failed with exception:")
                        logger.warning(traceback.format_exc())
                    update_movie_icons()
            else:
                win.moviesWidget.clear()
                win.moviesWidget.addItem(_("Nothing found"))

        def movies_play(mov_item):
            if get_movie_text(mov_item) in YukiData.currentMoviesGroup:
                itemClicked_event(
                    get_movie_text(mov_item),
                    YukiData.currentMoviesGroup[get_movie_text(mov_item)]["url"],
                )

        win.moviesWidget.itemDoubleClicked.connect(movies_play)

        movies_groups = []
        movies_combobox = QtWidgets.QComboBox()
        for movie_combobox in YukiData.movies:
            if "tvg-group" in YukiData.movies[movie_combobox]:
                if YukiData.movies[movie_combobox]["tvg-group"] not in movies_groups:
                    movies_groups.append(YukiData.movies[movie_combobox]["tvg-group"])
        for movie_group in movies_groups:
            movies_combobox.addItem(movie_group)
        movies_combobox.currentIndexChanged.connect(movies_group_change)
        movies_group_change()

        def redraw_series():
            YukiData.serie_selected = False
            win.seriesWidget.clear()
            if YukiData.series:
                for serie2 in YukiData.series:
                    win.seriesWidget.addItem(serie2)
            else:
                win.seriesWidget.addItem(_("Nothing found"))

        def series_change(series_item):
            sel_serie = series_item.text()
            if sel_serie == "< " + _("Back"):
                redraw_series()
            else:
                if YukiData.serie_selected:
                    try:
                        serie_data = series_item.data(QtCore.Qt.ItemDataRole.UserRole)
                        if serie_data:
                            series_name = serie_data.split(":::::::::::::::::::")[2]
                            season_name = serie_data.split(":::::::::::::::::::")[1]
                            serie_url = serie_data.split(":::::::::::::::::::")[0]
                            itemClicked_event(
                                sel_serie
                                + " ::: "
                                + season_name
                                + " ::: "
                                + series_name,
                                serie_url,
                            )
                    except Exception:
                        pass
                else:
                    logger.info(f"Fetching data for serie '{sel_serie}'")
                    win.seriesWidget.clear()
                    win.seriesWidget.addItem("< " + _("Back"))
                    win.seriesWidget.item(0).setForeground(QtCore.Qt.GlobalColor.blue)
                    try:
                        if not YukiData.series[sel_serie].seasons:
                            xt.get_series_info_by_id(YukiData.series[sel_serie])
                        for season_name in YukiData.series[sel_serie].seasons.keys():
                            season = YukiData.series[sel_serie].seasons[season_name]
                            season_item = QtWidgets.QListWidgetItem()
                            season_item.setText("== " + season.name + " ==")
                            season_item.setFont(YukiGUI.font_bold)
                            win.seriesWidget.addItem(season_item)
                            for episode_name in season.episodes.keys():
                                episode = season.episodes[episode_name]
                                episode_item = QtWidgets.QListWidgetItem()
                                episode_item.setText(episode.title)
                                episode_item.setData(
                                    QtCore.Qt.ItemDataRole.UserRole,
                                    episode.url
                                    + ":::::::::::::::::::"
                                    + season.name
                                    + ":::::::::::::::::::"
                                    + sel_serie,
                                )
                                win.seriesWidget.addItem(episode_item)
                        YukiData.serie_selected = True
                        logger.info(f"Fetching data for serie '{sel_serie}' completed")
                    except Exception:
                        logger.warning(f"Fetching data for serie '{sel_serie}' FAILED")

        win.seriesWidget.itemDoubleClicked.connect(series_change)

        redraw_series()

        playmode_selector = QtWidgets.QComboBox()
        playmode_selector.currentIndexChanged.connect(playmode_change)
        for playmode in [_("TV channels"), _("Movies"), _("Series")]:
            playmode_selector.addItem(playmode)

        def focusOutEvent_after(
            playlist_widget_visible,
            controlpanel_widget_visible,
            channelfiltersearch_has_focus,
        ):
            YukiGUI.channelfilter.usePopup = False
            YukiGUI.playlist_widget.setWindowFlags(
                QtCore.Qt.WindowType.CustomizeWindowHint
                | QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.X11BypassWindowManagerHint
            )
            YukiGUI.controlpanel_widget.setWindowFlags(
                QtCore.Qt.WindowType.CustomizeWindowHint
                | QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.X11BypassWindowManagerHint
            )
            if playlist_widget_visible:
                YukiGUI.playlist_widget.show()
            if controlpanel_widget_visible:
                YukiGUI.controlpanel_widget.show()
            if channelfiltersearch_has_focus:
                YukiGUI.channelfiltersearch.click()

        @async_gui_blocking_function
        def mainthread_timer_2(t2):
            time.sleep(0.05)
            exInMainThread_partial(t2)

        def mainthread_timer(t1):
            mainthread_timer_2(t1)

        class MyLineEdit(QtWidgets.QLineEdit):
            usePopup = False
            click_event = Signal()

            def mousePressEvent(self, event1):
                if event1.button() == QtCore.Qt.MouseButton.LeftButton:
                    self.click_event.emit()
                else:
                    super().mousePressEvent(event1)

            def focusOutEvent(self, event2):
                super().focusOutEvent(event2)
                if YukiData.fullscreen:
                    playlist_widget_visible1 = YukiGUI.playlist_widget.isVisible()
                    controlpanel_widget_visible1 = (
                        YukiGUI.controlpanel_widget.isVisible()
                    )
                    channelfiltersearch_has_focus1 = (
                        YukiGUI.channelfiltersearch.hasFocus()
                    )
                    focusOutEvent_after_partial = partial(
                        focusOutEvent_after,
                        playlist_widget_visible1,
                        controlpanel_widget_visible1,
                        channelfiltersearch_has_focus1,
                    )
                    mainthread_timer_1 = partial(
                        mainthread_timer, focusOutEvent_after_partial
                    )
                    exInMainThread_partial(mainthread_timer_1)

        def channelfilter_clicked():
            if YukiData.fullscreen:
                playlist_widget_visible1 = YukiGUI.playlist_widget.isVisible()
                controlpanel_widget_visible1 = YukiGUI.controlpanel_widget.isVisible()
                YukiGUI.channelfilter.usePopup = True
                YukiGUI.playlist_widget.setWindowFlags(
                    QtCore.Qt.WindowType.CustomizeWindowHint
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.X11BypassWindowManagerHint
                    | QtCore.Qt.WindowType.Popup
                )
                YukiGUI.controlpanel_widget.setWindowFlags(
                    QtCore.Qt.WindowType.CustomizeWindowHint
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.X11BypassWindowManagerHint
                    | QtCore.Qt.WindowType.Popup
                )
                if playlist_widget_visible1:
                    YukiGUI.playlist_widget.show()
                if controlpanel_widget_visible1:
                    YukiGUI.controlpanel_widget.show()

        def page_change():
            win.listWidget.verticalScrollBar().setValue(0)
            redraw_channels()
            try:
                YukiGUI.page_box.clearFocus()
            except Exception:
                pass

        def tvguide_many_clicked():
            tvguide_many_channels = []
            tvguide_many_channels_names = []
            tvguide_many_i = -1
            for tvguide_m_channel in [x6[0] for x6 in sorted(YukiData.array.items())]:
                epg_search = tvguide_m_channel.lower()
                if epg_search in YukiData.prog_match_arr:
                    epg_search = YukiData.prog_match_arr[epg_search.lower()]
                if exists_in_epg(epg_search, YukiData.programmes):
                    tvguide_many_i += 1
                    tvguide_many_channels.append(epg_search)
                    tvguide_many_channels_names.append(tvguide_m_channel)
            YukiGUI.tvguide_many_table.setRowCount(len(tvguide_many_channels))
            YukiGUI.tvguide_many_table.setVerticalHeaderLabels(
                tvguide_many_channels_names
            )
            logger.info(YukiGUI.tvguide_many_table.horizontalHeader())
            a_1_len_array = []
            a_1_array = {}
            for channel_6 in tvguide_many_channels:
                a_1 = [
                    a_2
                    for a_2 in get_epg(YukiData.programmes, channel_6)
                    if a_2["stop"] > time.time() - 1
                ]
                a_1_array[channel_6] = a_1
                a_1_len_array.append(len(a_1))
            YukiGUI.tvguide_many_table.setColumnCount(max(a_1_len_array))
            tvguide_many_i2 = -1
            for channel_7 in tvguide_many_channels:
                tvguide_many_i2 += 1
                a_3_i = -1
                for a_3 in a_1_array[channel_7]:
                    a_3_i += 1
                    start_3_many = (
                        datetime.datetime.fromtimestamp(a_3["start"]).strftime("%H:%M")
                        + " - "
                    )
                    stop_3_many = (
                        datetime.datetime.fromtimestamp(a_3["stop"]).strftime("%H:%M")
                        + "\n"
                    )
                    try:
                        title_3_many = a_3["title"] if "title" in a_3 else ""
                    except Exception:
                        title_3_many = ""
                    try:
                        desc_3_many = (
                            ("\n" + a_3["desc"] + "\n") if "desc" in a_3 else ""
                        )
                    except Exception:
                        desc_3_many = ""
                    a_3_text = start_3_many + stop_3_many + title_3_many + desc_3_many
                    YukiGUI.tvguide_many_table.setItem(
                        tvguide_many_i2, a_3_i, QtWidgets.QTableWidgetItem(a_3_text)
                    )
            YukiGUI.tvguide_many_table.setHorizontalHeaderLabels(
                [
                    time.strftime("%H:%M", time.localtime()),
                    time.strftime("%H:%M", time.localtime()),
                ]
            )
            if not YukiGUI.tvguide_many_win.isVisible():
                moveWindowToCenter(YukiGUI.tvguide_many_win)
                YukiGUI.tvguide_many_win.show()
                moveWindowToCenter(YukiGUI.tvguide_many_win)
            else:
                YukiGUI.tvguide_many_win.hide()

        YukiGUI.create2(
            win,
            get_page_count(len(YukiData.array)),
            channelfilter_clicked,
            channelfilter_do,
            get_of_txt,
            page_change,
            tvguide_many_clicked,
            MyLineEdit,
            ICONS_FOLDER,
            playmode_selector,
            YukiData.combobox,
            movies_combobox,
            loading,
        )

        if YukiData.settings["panelposition"] == 2:
            dockWidget_playlist.resize(
                DOCKWIDGET_PLAYLIST_WIDTH, dockWidget_playlist.height()
            )
            playlist_label = QtWidgets.QLabel(_("Playlist"))
            playlist_label.setFont(YukiGUI.font_12_bold)
            dockWidget_playlist.setTitleBarWidget(playlist_label)
        else:
            dockWidget_playlist.setFixedWidth(DOCKWIDGET_PLAYLIST_WIDTH)
            dockWidget_playlist.setTitleBarWidget(QtWidgets.QWidget())
        if YukiData.settings["panelposition"] == 2:
            gripWidget = QtWidgets.QWidget()
            gripLayout = QtWidgets.QVBoxLayout()
            gripLayout.setContentsMargins(0, 0, 0, 0)
            gripLayout.setSpacing(0)
            gripLayout.addWidget(YukiGUI.widget)
            gripLayout.addWidget(
                QtWidgets.QSizeGrip(YukiGUI.widget),
                0,
                QtCore.Qt.AlignmentFlag.AlignBottom
                | QtCore.Qt.AlignmentFlag.AlignRight,
            )
            gripWidget.setLayout(gripLayout)
            dockWidget_playlist.setWidget(gripWidget)
        else:
            dockWidget_playlist.setWidget(YukiGUI.widget)
        dockWidget_playlist.setFloating(YukiData.settings["panelposition"] == 2)
        dockWidget_playlist.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        )
        if YukiData.settings["panelposition"] == 0:
            win.addDockWidget(
                QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dockWidget_playlist
            )
        elif YukiData.settings["panelposition"] == 1:
            win.addDockWidget(
                QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, dockWidget_playlist
            )
        elif YukiData.settings["panelposition"] == 2:
            separate_playlist_data = read_option("separate_playlist")
            if separate_playlist_data:
                dockWidget_playlist.setGeometry(
                    separate_playlist_data["x"],
                    separate_playlist_data["y"],
                    separate_playlist_data["w"],
                    separate_playlist_data["h"],
                )
            else:
                dockWidget_playlist.resize(dockWidget_playlist.width(), win.height())
                dockWidget_playlist.move(
                    win.pos().x() + win.width() - dockWidget_playlist.width() + 25,
                    win.pos().y(),
                )

        FORBIDDEN_CHARS = ('"', "*", ":", "<", ">", "?", "\\", "/", "|", "[", "]")

        def do_screenshot():
            if YukiData.playing_channel:
                YukiData.state.show()
                YukiData.state.setTextYuki(_("Doing screenshot..."))
                ch = YukiData.playing_channel.replace(" ", "_")
                for char in FORBIDDEN_CHARS:
                    ch = ch.replace(char, "")
                cur_time = datetime.datetime.now().strftime("%d%m%Y_%H%M%S")
                file_name = "screenshot_-_" + cur_time + "_-_" + ch + ".png"
                if not YukiData.settings["scrrecnosubfolders"]:
                    file_path = str(Path(save_folder, "screenshots", file_name))
                else:
                    file_path = str(Path(save_folder, file_name))
                try:
                    YukiData.player.screenshot_to_file(file_path, includes="subtitles")
                    YukiData.state.show()
                    YukiData.state.setTextYuki(_("Screenshot saved!"))
                except Exception:
                    YukiData.state.show()
                    YukiData.state.setTextYuki(_("Screenshot saving error!"))
                YukiData.time_stop = time.time() + 1
            else:
                YukiData.state.show()
                YukiData.state.setTextYuki("{}!".format(_("No channel selected")))
                YukiData.time_stop = time.time() + 1

        def update_tvguide(
            channel_1="",
            do_return=False,
            show_all_guides=False,
            mark_integers=False,
            date_selected=None,
        ):
            if YukiData.array:
                if not channel_1:
                    if YukiData.item_selected:
                        channel_2 = YukiData.item_selected
                    else:
                        channel_2 = sorted(YukiData.array.items())[0][0]
                else:
                    channel_2 = channel_1
                try:
                    channel_1_item = getArrayItem(channel_1)
                except Exception:
                    channel_1_item = None
                txt = _("No TV guide for channel")
                channel_2 = channel_2.lower()
                newline_symbol = "\n"
                if do_return:
                    newline_symbol = "!@#$%^^&*("
                try:
                    channel_3 = YukiData.prog_match_arr[channel_2]
                except Exception:
                    channel_3 = channel_2
                if exists_in_epg(channel_3, YukiData.programmes):
                    txt = newline_symbol
                    prog = get_epg(YukiData.programmes, channel_3)
                    for pr in prog:
                        override_this = False
                        if show_all_guides:
                            override_this = pr["start"] < time.time() + 1
                        else:
                            override_this = pr["stop"] > time.time() - 1
                        archive_btn = ""
                        if date_selected is not None:
                            override_this = pr["start"] > date_selected - 1 and pr[
                                "start"
                            ] < (date_selected + 86401)
                        if override_this:
                            def_placeholder = "%d.%m.%y %H:%M"
                            if mark_integers:
                                def_placeholder = "%d.%m.%Y %H:%M:%S"
                            start_2 = (
                                datetime.datetime.fromtimestamp(pr["start"]).strftime(
                                    def_placeholder
                                )
                                + " - "
                            )
                            stop_2 = (
                                datetime.datetime.fromtimestamp(pr["stop"]).strftime(
                                    def_placeholder
                                )
                                + "\n"
                            )
                            try:
                                title_2 = pr["title"] if "title" in pr else ""
                            except Exception:
                                title_2 = ""
                            try:
                                desc_2 = (
                                    ("\n" + pr["desc"] + "\n") if "desc" in pr else ""
                                )
                            except Exception:
                                desc_2 = ""
                            attach_1 = ""
                            if mark_integers:
                                try:
                                    marked_integer = prog.index(pr)
                                except Exception:
                                    marked_integer = -1
                                attach_1 = f" ({marked_integer})"
                            if (
                                date_selected is not None
                                and YukiData.settings["catchupenable"]
                                and YukiGUI.showonlychplaylist_chk.isChecked()
                            ):
                                try:
                                    catchup_days2 = int(channel_1_item["catchup-days"])
                                except Exception:
                                    catchup_days2 = 7
                                # support for seconds
                                if catchup_days2 < 1000:
                                    catchup_days2 = catchup_days2 * 86400
                                if (
                                    pr["start"] < time.time() + 1
                                    and not (
                                        time.time() > pr["start"]
                                        and time.time() < pr["stop"]
                                    )
                                    and pr["stop"] > time.time() - catchup_days2
                                ):
                                    archive_link = urllib.parse.quote_plus(
                                        json.dumps(
                                            [
                                                channel_1,
                                                datetime.datetime.fromtimestamp(
                                                    pr["start"]
                                                ).strftime("%d.%m.%Y %H:%M:%S"),
                                                datetime.datetime.fromtimestamp(
                                                    pr["stop"]
                                                ).strftime("%d.%m.%Y %H:%M:%S"),
                                                prog.index(pr),
                                            ]
                                        )
                                    )
                                    archive_btn = (
                                        '\n<a href="#__archive__'
                                        + archive_link
                                        + '">'
                                        + _("Open archive")
                                        + "</a>"
                                    )
                            start_symbl = ""
                            stop_symbl = ""
                            if YukiData.use_dark_icon_theme:
                                start_symbl = '<span style="color: white;">'
                                stop_symbl = "</span>"
                            use_epg_color = "green"
                            if time.time() > pr["start"] and time.time() < pr["stop"]:
                                use_epg_color = "red"
                            txt += (
                                f'<span style="color: {use_epg_color};">'
                                + start_2
                                + stop_2
                                + "</span>"
                                + start_symbl
                                + "<b>"
                                + title_2
                                + "</b>"
                                + archive_btn
                                + desc_2
                                + attach_1
                                + stop_symbl
                                + newline_symbol
                            )
                if do_return:
                    return txt
                txt = txt.replace("\n", "<br>").replace("<br>", "", 1)
                YukiData.tvguide_lbl.setText(txt)
            return ""

        def show_tvguide():
            if YukiData.tvguide_lbl.isVisible():
                YukiData.tvguide_lbl.setText("")
                YukiData.tvguide_lbl.hide()
                tvguide_close_lbl.hide()
            else:
                update_tvguide()
                YukiData.tvguide_lbl.show()
                tvguide_close_lbl.show()

        def hide_tvguide():
            if YukiData.tvguide_lbl.isVisible():
                YukiData.tvguide_lbl.setText("")
                YukiData.tvguide_lbl.hide()
                tvguide_close_lbl.hide()

        def update_tvguide_2():
            YukiGUI.epg_win_checkbox.clear()
            if YukiGUI.showonlychplaylist_chk.isChecked():
                YukiGUI.epg_win_count.setText(
                    "({}: {})".format(_("channels"), len(array_sorted))
                )
                for channel_0 in array_sorted:
                    YukiGUI.epg_win_checkbox.addItem(channel_0)
            else:
                YukiGUI.epg_win_count.setText(
                    "({}: {})".format(_("channels"), len(YukiData.programmes))
                )
                for channel_0 in YukiData.programmes:
                    YukiGUI.epg_win_checkbox.addItem(channel_0)

        def show_tvguide_2():
            if YukiGUI.epg_win.isVisible():
                YukiGUI.epg_win.hide()
            else:
                epg_index = YukiGUI.epg_win_checkbox.currentIndex()
                update_tvguide_2()
                if epg_index != -1:
                    YukiGUI.epg_win_checkbox.setCurrentIndex(epg_index)
                YukiGUI.epg_win.show()

        def show_archive():
            if not YukiGUI.epg_win.isVisible():
                show_tvguide_2()
                find_channel = YukiData.item_selected
                if not find_channel:
                    find_channel = YukiData.playing_channel
                if find_channel:
                    try:
                        find_channel_index = YukiGUI.epg_win_checkbox.findText(
                            find_channel, QtCore.Qt.MatchFlag.MatchExactly
                        )
                    except Exception:
                        find_channel_index = -1
                    if find_channel_index != -1:
                        YukiGUI.epg_win_checkbox.setCurrentIndex(find_channel_index)
                epg_date_changed(YukiGUI.epg_select_date.selectedDate())
            else:
                YukiGUI.epg_win.hide()

        YukiData.is_recording = False
        YukiData.recording_time = 0
        YukiData.record_file = None

        def start_record(ch1, url3):
            orig_channel_name = ch1
            if not YukiData.is_recording:
                YukiData.is_recording = True
                YukiGUI.lbl2.show()
                YukiGUI.lbl2.setText(_("Preparing record"))
                ch = ch1.replace(" ", "_")
                for char in FORBIDDEN_CHARS:
                    ch = ch.replace(char, "")
                cur_time = datetime.datetime.now().strftime("%d%m%Y_%H%M%S")
                record_format = ".ts"
                if is_youtube_url(url3):
                    record_format = ".mkv"
                if not YukiData.settings["scrrecnosubfolders"]:
                    out_file = str(
                        Path(
                            save_folder,
                            "recordings",
                            "recording_-_" + cur_time + "_-_" + ch + record_format,
                        )
                    )
                else:
                    out_file = str(
                        Path(
                            save_folder,
                            "recording_-_" + cur_time + "_-_" + ch + record_format,
                        )
                    )
                YukiData.record_file = out_file
                record(
                    url3,
                    out_file,
                    orig_channel_name,
                    f"Referer: {YukiData.settings['referer']}",
                    get_ua_ref_for_channel,
                )
            else:
                YukiData.is_recording = False
                YukiData.recording_time = 0
                stop_record()
                YukiGUI.lbl2.setText("")
                YukiGUI.lbl2.hide()

        def do_record():
            if YukiData.playing_channel:
                start_record(YukiData.playing_channel, YukiData.playing_url)
            else:
                YukiData.time_stop = time.time() + 1
                YukiData.state.show()
                YukiData.state.setTextYuki(_("No channel selected for record"))

        def my_log(mpv_loglevel1, component, message):
            mpv_log_str = f"[{mpv_loglevel1}] {component}: {message}"
            if "Invalid video timestamp: " not in str(mpv_log_str):
                if "[debug] " in str(mpv_log_str) or "[trace] " in str(mpv_log_str):
                    mpv_logger.debug(str(mpv_log_str))
                elif "[warn] " in str(mpv_log_str):
                    mpv_logger.warning(str(mpv_log_str))
                elif "[error] " in str(mpv_log_str):
                    mpv_logger.error(str(mpv_log_str))
                elif "[fatal] " in str(mpv_log_str):
                    mpv_logger.critical(str(mpv_log_str))
                else:
                    mpv_logger.info(str(mpv_log_str))

        def playLastChannel():
            isPlayingLast = False
            if (
                os.path.isfile(str(Path(LOCAL_DIR, "lastchannels.json")))
                and YukiData.settings["openprevchannel"]
            ):
                try:
                    lastfile_1 = open(
                        str(Path(LOCAL_DIR, "lastchannels.json")), encoding="utf8"
                    )
                    lastfile_1_dat = json.loads(lastfile_1.read())
                    lastfile_1.close()
                    if lastfile_1_dat[0] in array_sorted:
                        isPlayingLast = True
                        YukiData.player.user_agent = lastfile_1_dat[2]
                        setChannelText("  " + lastfile_1_dat[0])
                        itemClicked_event(lastfile_1_dat[0])
                        setChannelText("  " + lastfile_1_dat[0])
                        try:
                            if lastfile_1_dat[3] < YukiData.combobox.count():
                                YukiData.combobox.setCurrentIndex(lastfile_1_dat[3])
                        except Exception:
                            pass
                        try:
                            win.listWidget.setCurrentRow(lastfile_1_dat[4])
                        except Exception:
                            pass
                except Exception:
                    if os.path.isfile(str(Path(LOCAL_DIR, "lastchannels.json"))):
                        os.remove(str(Path(LOCAL_DIR, "lastchannels.json")))
            return isPlayingLast

        VIDEO_OUTPUT = "gpu,x11"
        HWACCEL = "auto-safe" if YukiData.settings["hwaccel"] else "no"

        # Wayland fix
        is_apply_wayland_fix = False

        if "WAYLAND_DISPLAY" in os.environ:
            if os.environ["WAYLAND_DISPLAY"]:
                logger.info("Found environ WAYLAND_DISPLAY")
                is_apply_wayland_fix = True
        if "XDG_SESSION_TYPE" in os.environ:
            if os.environ["XDG_SESSION_TYPE"] == "wayland":
                logger.info("Environ XDG_SESSION_TYPE == wayland")
                is_apply_wayland_fix = True

        if is_apply_wayland_fix:
            logger.info("Set libmpv video output to x11 because Wayland is used")
            VIDEO_OUTPUT = "x11"

        options = {
            "vo": VIDEO_OUTPUT,
            "hwdec": HWACCEL,
            "cursor-autohide": 1000,
            "force-window": True,
        }
        options_orig = options.copy()
        options_2 = {}
        try:
            mpv_options_1 = YukiData.settings["mpv_options"]
            if "=" in mpv_options_1:
                pairs = mpv_options_1.split()
                for pair in pairs:
                    key, value = pair.split("=")
                    options[key.replace("--", "")] = value
                    options_2[key.replace("--", "")] = value
        except Exception:
            logger.warning("Could not parse MPV options!")
            logger.warning(traceback.format_exc())
        logger.info("Testing custom mpv options...")
        logger.info(options_2)
        try:
            test_options = mpv.MPV(**options_2)
            logger.info("mpv options OK")
        except Exception:
            logger.warning("mpv options test failed, ignoring them")
            msg_wrongmpvoptions = QtWidgets.QMessageBox(
                qt_icon_warning,
                MAIN_WINDOW_TITLE,
                _("Custom MPV options invalid, ignoring them")
                + "\n\n"
                + str(json.dumps(options_2)),
                QtWidgets.QMessageBox.StandardButton.Ok,
            )
            msg_wrongmpvoptions.exec()
            options = options_orig

        logger.info(f"Using mpv options: {json.dumps(options)}")

        YukiData.player = None

        def get_about_text():
            about_txt = f"<b>yuki-iptv {APP_VERSION}</b>"
            about_txt += "<br><br>" + _("IPTV player with EPG support")
            about_txt += "<br><br>" + _("Developer: {}").format("Liya Astrova")
            about_txt += (
                "<br><br>"
                + _("Using Qt {} ({})").format(QtCore.qVersion(), qt_library)
                + QT_PLATFORM
            )
            mpv_version = YukiData.player.mpv_version
            if " " in mpv_version:
                mpv_version = mpv_version.split(" ", 1)[1]
            if not mpv_version:
                mpv_version = "UNKNOWN"
            about_txt += "<br>" + _("Using libmpv {}").format(mpv_version)
            return about_txt

        def main_channel_settings():
            if YukiData.playing_channel:
                YukiData.item_selected = YukiData.playing_channel
                settings_context_menu()
            else:
                msg = QtWidgets.QMessageBox(
                    qt_icon_warning,
                    MAIN_WINDOW_TITLE,
                    _("No channel selected"),
                    QtWidgets.QMessageBox.StandardButton.Ok,
                )
                msg.exec()

        @idle_function
        def showhideplaylist(unused=None):
            if not YukiData.fullscreen:
                try:
                    show_hide_playlist()
                except Exception:
                    pass

        @idle_function
        def lowpanel_ch_1(unused=None):
            if not YukiData.fullscreen:
                try:
                    lowpanel_ch()
                except Exception:
                    pass

        def showhideeverything():
            if not YukiData.fullscreen:
                if dockWidget_playlist.isVisible():
                    YukiData.compact_mode = True
                    dockWidget_playlist.hide()
                    dockWidget_controlPanel.hide()
                    win.menu_bar_qt.hide()
                else:
                    YukiData.compact_mode = False
                    dockWidget_playlist.show()
                    dockWidget_controlPanel.show()
                    win.menu_bar_qt.show()

        stream_info.data = {}

        def process_stream_info(
            stream_info_count,
            stream_info_name,
            stream_properties,
            stream_information_name,
        ):
            if stream_information_name:
                stream_information_label1 = QtWidgets.QLabel()
                stream_information_label1.setStyleSheet("color:green")
                stream_information_label1.setFont(YukiGUI.font_bold)
                stream_information_label1.setText("\n" + stream_information_name + "\n")
                YukiGUI.stream_information_layout.addWidget(
                    stream_information_label1, stream_info_count, 0
                )
                stream_info_count += 1

            stream_information_label2 = QtWidgets.QLabel()
            stream_information_label2.setFont(YukiGUI.font_bold)
            stream_information_label2.setText(stream_info_name)
            YukiGUI.stream_information_layout.addWidget(
                stream_information_label2, stream_info_count, 0
            )

            for stream_information_data in stream_properties:
                stream_info_count += 1
                stream_info_widget1 = QtWidgets.QLabel()
                stream_info_widget2 = QtWidgets.QLabel()
                stream_info_widget1.setText(str(stream_information_data))
                stream_info_widget2.setText(
                    str(stream_properties[stream_information_data])
                )

                if (
                    str(stream_information_data) == _("Average Bitrate")
                    and stream_properties == stream_info.video_properties[_("General")]
                ):
                    stream_info.data["video"] = [stream_info_widget2, stream_properties]

                if (
                    str(stream_information_data) == _("Average Bitrate")
                    and stream_properties == stream_info.audio_properties[_("General")]
                ):
                    stream_info.data["audio"] = [stream_info_widget2, stream_properties]

                YukiGUI.stream_information_layout.addWidget(
                    stream_info_widget1, stream_info_count, 0
                )
                YukiGUI.stream_information_layout.addWidget(
                    stream_info_widget2, stream_info_count, 1
                )
            return stream_info_count + 1

        def timer_bitrate():
            try:
                if YukiGUI.streaminfo_win.isVisible():
                    if "video" in stream_info.data:
                        stream_info.data["video"][0].setText(
                            stream_info.data["video"][1][_("Average Bitrate")]
                        )
                    if "audio" in stream_info.data:
                        stream_info.data["audio"][0].setText(
                            stream_info.data["audio"][1][_("Average Bitrate")]
                        )
            except Exception:
                pass

        def open_stream_info():
            if YukiData.playing_channel:
                for stream_info_i in reversed(
                    range(YukiGUI.stream_information_layout.count())
                ):
                    YukiGUI.stream_information_layout.itemAt(
                        stream_info_i
                    ).widget().setParent(None)

                stream_props = [
                    stream_info.video_properties[_("General")],
                    stream_info.video_properties[_("Color")],
                    stream_info.audio_properties[_("General")],
                    stream_info.audio_properties[_("Layout")],
                ]

                stream_info_count = 1
                stream_info_video_lbl = QtWidgets.QLabel(_("Video") + "\n")
                stream_info_video_lbl.setStyleSheet("color:green")
                stream_info_video_lbl.setFont(YukiGUI.font_bold)
                YukiGUI.stream_information_layout.addWidget(stream_info_video_lbl, 0, 0)
                stream_info_count = process_stream_info(
                    stream_info_count, _("General"), stream_props[0], ""
                )
                stream_info_count = process_stream_info(
                    stream_info_count, _("Color"), stream_props[1], ""
                )
                stream_info_count = process_stream_info(
                    stream_info_count, _("General"), stream_props[2], _("Audio")
                )
                stream_info_count = process_stream_info(
                    stream_info_count, _("Layout"), stream_props[3], ""
                )

                if not YukiGUI.streaminfo_win.isVisible():
                    YukiGUI.streaminfo_win.show()
                    moveWindowToCenter(YukiGUI.streaminfo_win)
                else:
                    YukiGUI.streaminfo_win.hide()
            else:
                YukiData.state.show()
                YukiData.state.setTextYuki("{}!".format(_("No channel selected")))
                YukiData.time_stop = time.time() + 1

        YukiGUI.streaminfo_win.setWindowTitle(_("Stream Information"))

        def is_recording_func():
            ret_code_rec = False
            if YukiData.ffmpeg_processes:
                ret_code_array = []
                for ffmpeg_process_1 in YukiData.ffmpeg_processes:
                    if ffmpeg_process_1[0].processId() == 0:
                        ret_code_array.append(True)
                        YukiData.ffmpeg_processes.remove(ffmpeg_process_1)
                    else:
                        ret_code_array.append(False)
                ret_code_rec = False not in ret_code_array
            else:
                ret_code_rec = True
            return ret_code_rec

        win.oldpos = None

        YukiData.force_turnoff_osc = False

        def redraw_menubar():
            try:
                update_menubar(
                    YukiData.player.track_list,
                    YukiData.playing_channel,
                    YukiData.settings["m3u"],
                    str(Path(LOCAL_DIR, "alwaysontop.json")),
                )
            except Exception:
                logger.warning("redraw_menubar failed")
                show_exception(traceback.format_exc(), "redraw_menubar failed")

        YukiData.right_click_menu = QtWidgets.QMenu()

        @idle_function
        def do_reconnect1(unused=None):
            if YukiData.playing_channel:
                logger.info("Reconnecting to stream")
                try:
                    doPlay(*comm_instance.do_play_args)
                except Exception:
                    logger.warning("Failed reconnecting to stream - no known URL")

        @async_gui_blocking_function
        def do_reconnect1_async():
            time.sleep(1)
            do_reconnect1()

        @idle_function
        def end_file_error_callback(unused=None):
            logger.warning("Playing error!")
            if YukiData.settings["autoreconnection"] and YukiData.playing_group == 0:
                logger.warning("Connection to stream lost, waiting 1 sec...")
                do_reconnect1_async()
            elif not YukiData.is_loading:
                mpv_stop()
            else:
                YukiData.resume_playback = not YukiData.player.pause
                mpv_stop()
                    
            YukiGUI.channel.setText("")
            loading.setText(_("Playing error"))
            loading.setStyleSheet("color: red")
            showLoading()
            YukiGUI.loading1.hide()
            YukiGUI.loading_movie.stop()

        @idle_function
        def end_file_callback(unused=None):
            if win.isVisible():
                if YukiData.playing_channel and YukiData.player.path is None:
                    if (
                        YukiData.settings["autoreconnection"]
                        and YukiData.playing_group == 0
                    ):
                        logger.warning("Connection to stream lost, waiting 1 sec...")
                        do_reconnect1_async()
                    elif not YukiData.is_loading:
                        mpv_stop()

        @idle_function
        def file_loaded_callback(unused=None):
            if YukiData.playing_channel:
                logger.info("redraw_menubar triggered by file_loaded_callback")
                redraw_menubar()

        @idle_function
        def my_mouse_right_callback(unused=None):
            _exec(YukiData.right_click_menu, QtGui.QCursor.pos())

        @idle_function
        def my_mouse_left_callback(unused=None):
            if YukiData.right_click_menu.isVisible():
                YukiData.right_click_menu.hide()
            elif YukiData.settings["hideplaylistbyleftmouseclick"]:
                show_hide_playlist()

        @idle_function
        def my_up_binding_execute(unused=None):
            if YukiData.settings["mouseswitchchannels"]:
                next_channel()
            else:
                volume = int(
                    YukiData.player.volume + YukiData.settings["volumechangestep"]
                )
                volume = min(volume, 200)
                YukiGUI.volume_slider.setValue(volume)
                mpv_volume_set()

        @idle_function
        def my_down_binding_execute(unused=None):
            if YukiData.settings["mouseswitchchannels"]:
                prev_channel()
            else:
                volume = int(
                    YukiData.player.volume - YukiData.settings["volumechangestep"]
                )
                volume = max(volume, 0)
                YukiData.time_stop = time.time() + 3
                show_volume(volume)
                YukiGUI.volume_slider.setValue(volume)
                mpv_volume_set()

        class ControlPanelDockWidget(QtWidgets.QDockWidget):
            def enterEvent(self, event4):
                YukiData.check_controlpanel_visible = True

            def leaveEvent(self, event4):
                YukiData.check_controlpanel_visible = False

        dockWidget_controlPanel = ControlPanelDockWidget(win)

        dockWidget_playlist.setObjectName("dockWidget_playlist")
        dockWidget_controlPanel.setObjectName("dockWidget_controlPanel")

        def open_recording_folder():
            absolute_path = Path(save_folder).absolute()
            xdg_open = subprocess.Popen(["xdg-open", str(absolute_path)])
            xdg_open.wait()

        def go_channel(i1):
            pause_state = YukiData.player.pause
            if YukiData.resume_playback:
                YukiData.resume_playback = False
                pause_state = False
            row = win.listWidget.currentRow()
            if row == -1:
                row = YukiData.row0
            next_row = row + i1
            if next_row < 0:
                # Previous page
                if YukiGUI.page_box.value() - 1 == 0:
                    next_row = 0
                else:
                    YukiGUI.page_box.setValue(YukiGUI.page_box.value() - 1)
                    next_row = win.listWidget.count()
            elif next_row > win.listWidget.count() - 1:
                # Next page
                if YukiGUI.page_box.value() + 1 > YukiGUI.page_box.maximum():
                    next_row = row
                else:
                    YukiGUI.page_box.setValue(YukiGUI.page_box.value() + 1)
                    next_row = 0
            next_row = max(next_row, 0)
            next_row = min(next_row, win.listWidget.count() - 1)
            chk_pass = True
            try:
                chk_pass = win.listWidget.item(next_row).text() != _("Nothing found")
            except Exception:
                pass
            if chk_pass:
                win.listWidget.setCurrentRow(next_row)
                itemClicked_event(win.listWidget.currentItem())
            YukiData.player.pause = pause_state

        @idle_function
        def prev_channel(unused=None):
            go_channel(-1)

        @idle_function
        def next_channel(unused=None):
            go_channel(1)

        if qt_library == "PyQt6":
            qaction_prio = QtGui.QAction.Priority.HighPriority
        else:
            qaction_prio = QtWidgets.QAction.Priority.HighPriority

        def get_keybind(func1):
            return YukiData.main_keybinds[func1]

        def win_raise():
            win.show()
            win.raise_()
            win.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
            win.activateWindow()

        def mpris_set_volume(val):
            YukiGUI.volume_slider.setValue(int(val * 100))
            mpv_volume_set()

        def mpris_seek(val):
            if YukiData.playing_channel:
                YukiData.player.command("seek", val)

        def mpris_set_position(track_id, val):
            if YukiData.mpris_ready and YukiData.mpris_running:
                (
                    playback_status,
                    mpris_trackid,
                    artUrl,
                    player_position,
                ) = get_mpris_metadata()
                if track_id == mpris_trackid:
                    YukiData.player.time_pos = val

        YukiData.stopped = False

        def get_playlist_hash(playlist):
            return hashlib.sha512(playlist["m3u"].encode("utf-8")).hexdigest()

        def get_playlists():
            prefix = "/page/codeberg/ame_chan_angel/yuki_iptv/Playlist/"
            current_playlist = (f"{prefix}Unknown", _("Unknown"), "")
            current_playlist_name = _("Unknown")
            for playlist in playlists_saved:
                if playlists_saved[playlist]["m3u"] == YukiData.settings["m3u"]:
                    current_playlist_name = playlist
                    current_playlist = (
                        f"{prefix}{get_playlist_hash(playlists_saved[playlist])}",
                        playlist,
                        "",
                    )
                    break
            return (
                current_playlist_name,
                current_playlist,
                [
                    (
                        f"{prefix}{get_playlist_hash(playlists_saved[x])}",
                        x,
                        "",
                    )
                    for x in playlists_saved
                ],
            )

        @idle_function
        def mpris_select_playlist(unused=None):
            (
                _current_playlist_name,
                _current_playlist,
                playlists,
            ) = get_playlists()
            for playlist in playlists:
                if playlist[0] == YukiData.mpris_select_playlist:
                    populate_playlists()
                    YukiGUI.playlists_list.setCurrentItem(
                        YukiGUI.playlists_list.findItems(
                            playlist[1], QtCore.Qt.MatchFlag.MatchExactly
                        )[0]
                    )
                    playlists_selected()
                    break

        # MPRIS
        try:

            def mpris_callback(mpris_data):
                if (
                    mpris_data[0] == "org.mpris.MediaPlayer2"
                    and mpris_data[1] == "Raise"
                ):
                    exInMainThread_partial(partial(win_raise))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2"
                    and mpris_data[1] == "Quit"
                ):
                    QtCore.QTimer.singleShot(
                        100, lambda: exInMainThread_partial(partial(key_quit))
                    )
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Next"
                ):
                    exInMainThread_partial(partial(next_channel))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Previous"
                ):
                    exInMainThread_partial(partial(prev_channel))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Pause"
                ):
                    if not YukiData.player.pause:
                        exInMainThread_partial(partial(mpv_play))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "PlayPause"
                ):
                    exInMainThread_partial(partial(mpv_play))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Stop"
                ):
                    exInMainThread_partial(partial(mpv_stop))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Play"
                ):
                    if YukiData.player.pause:
                        exInMainThread_partial(partial(mpv_play))
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "Seek"
                ):
                    # microseconds to seconds
                    exInMainThread_partial(
                        partial(mpris_seek, mpris_data[2][0] / 1000000)
                    )
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "SetPosition"
                ):
                    track_id = mpris_data[2][0]
                    position = mpris_data[2][1] / 1000000  # microseconds to seconds
                    if (
                        track_id
                        != "/page/codeberg/ame_chan_angel/yuki_iptv/Track/NoTrack"
                    ):
                        exInMainThread_partial(
                            partial(mpris_set_position, track_id, position)
                        )
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Player"
                    and mpris_data[1] == "OpenUri"
                ):
                    mpris_play_url = mpris_data[2].unpack()[0]
                    exInMainThread_partial(
                        partial(itemClicked_event, mpris_play_url, mpris_play_url)
                    )
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Playlists"
                    and mpris_data[1] == "ActivatePlaylist"
                ):
                    YukiData.mpris_select_playlist = mpris_data[2].unpack()[0]
                    mpris_select_playlist()
                elif (
                    mpris_data[0] == "org.mpris.MediaPlayer2.Playlists"
                    and mpris_data[1] == "GetPlaylists"
                ):
                    (
                        _current_playlist_name,
                        _current_playlist,
                        playlists,
                    ) = get_playlists()
                    return GLib.Variant.new_tuple(GLib.Variant("a(oss)", playlists))
                elif (
                    mpris_data[0] == "org.freedesktop.DBus.Properties"
                    and mpris_data[1] == "Set"
                ):
                    mpris_data_params = mpris_data[2].unpack()
                    if (
                        mpris_data_params[0] == "org.mpris.MediaPlayer2"
                        and mpris_data_params[1] == "Fullscreen"
                    ):
                        if mpris_data_params[2]:
                            # Enable fullscreen
                            if not YukiData.fullscreen:
                                exInMainThread_partial(partial(mpv_fullscreen))
                        else:
                            # Disable fullscreen
                            if YukiData.fullscreen:
                                exInMainThread_partial(partial(mpv_fullscreen))
                    elif (
                        mpris_data_params[0] == "org.mpris.MediaPlayer2.Player"
                        and mpris_data_params[1] == "LoopStatus"
                    ):
                        # Not implemented
                        pass
                    elif (
                        mpris_data_params[0] == "org.mpris.MediaPlayer2.Player"
                        and mpris_data_params[1] == "Rate"
                    ):
                        exInMainThread_partial(
                            partial(set_playback_speed, mpris_data_params[2])
                        )
                    elif (
                        mpris_data_params[0] == "org.mpris.MediaPlayer2.Player"
                        and mpris_data_params[1] == "Shuffle"
                    ):
                        # Not implemented
                        pass
                    elif (
                        mpris_data_params[0] == "org.mpris.MediaPlayer2.Player"
                        and mpris_data_params[1] == "Volume"
                    ):
                        exInMainThread_partial(
                            partial(mpris_set_volume, mpris_data_params[2])
                        )
                # Always responding None, even if unknown command called
                # to prevent freezing
                return None

            def get_mpris_metadata():
                # Playback status
                if YukiData.playing_channel:
                    if YukiData.player.pause or YukiData.is_loading:
                        playback_status = "Paused"
                    else:
                        playback_status = "Playing"
                else:
                    playback_status = "Stopped"
                # Metadata
                playing_url_hash = hashlib.sha512(
                    YukiData.playing_url.encode("utf-8")
                ).hexdigest()
                mpris_trackid = (
                    f"/page/codeberg/ame_chan_angel/yuki_iptv/Track/{playing_url_hash}"
                    if YukiData.playing_url
                    else "/page/codeberg/ame_chan_angel/yuki_iptv/Track/NoTrack"
                )
                # Logo
                artUrl = ""
                if YukiData.playing_channel in YukiData.array:
                    if "tvg-logo" in YukiData.array[YukiData.playing_channel]:
                        if YukiData.array[YukiData.playing_channel]["tvg-logo"]:
                            artUrl = YukiData.array[YukiData.playing_channel][
                                "tvg-logo"
                            ]
                # Position in microseconds
                player_position = (
                    YukiData.player.duration * 1000000
                    if YukiData.player.duration
                    else 0
                )
                return playback_status, mpris_trackid, artUrl, player_position

            def get_mpris_options():
                if YukiData.mpris_ready and YukiData.mpris_running:
                    (
                        playback_status,
                        mpris_trackid,
                        artUrl,
                        player_position,
                    ) = get_mpris_metadata()
                    current_playlist_name, current_playlist, playlists = get_playlists()
                    return {
                        "org.mpris.MediaPlayer2": {
                            "CanQuit": GLib.Variant("b", True),
                            "Fullscreen": GLib.Variant("b", YukiData.fullscreen),
                            "CanSetFullscreen": GLib.Variant("b", True),
                            "CanRaise": GLib.Variant("b", True),
                            "HasTrackList": GLib.Variant("b", False),
                            "Identity": GLib.Variant("s", "yuki-iptv"),
                            "DesktopEntry": GLib.Variant("s", "yuki-iptv"),
                            "SupportedUriSchemes": GLib.Variant(
                                "as",
                                ("file", "http", "https", "rtp", "udp"),
                            ),
                            "SupportedMimeTypes": GLib.Variant(
                                "as",
                                (
                                    "audio/mpeg",
                                    "audio/x-mpeg",
                                    "video/mpeg",
                                    "video/x-mpeg",
                                    "video/x-mpeg-system",
                                    "video/mp4",
                                    "audio/mp4",
                                    "video/x-msvideo",
                                    "video/quicktime",
                                    "application/ogg",
                                    "application/x-ogg",
                                    "video/x-ms-asf",
                                    "video/x-ms-asf-plugin",
                                    "application/x-mplayer2",
                                    "video/x-ms-wmv",
                                    "video/x-google-vlc-plugin",
                                    "audio/x-wav",
                                    "audio/3gpp",
                                    "video/3gpp",
                                    "audio/3gpp2",
                                    "video/3gpp2",
                                    "video/x-flv",
                                    "video/x-matroska",
                                    "audio/x-matroska",
                                    "application/xspf+xml",
                                ),
                            ),
                        },
                        "org.mpris.MediaPlayer2.Player": {
                            "PlaybackStatus": GLib.Variant("s", playback_status),
                            "LoopStatus": GLib.Variant("s", "None"),
                            "Rate": GLib.Variant("d", YukiData.player.speed),
                            "Shuffle": GLib.Variant("b", False),
                            "Metadata": GLib.Variant(
                                "a{sv}",
                                {
                                    "mpris:trackid": GLib.Variant("o", mpris_trackid),
                                    "mpris:artUrl": GLib.Variant("s", artUrl),
                                    "mpris:length": GLib.Variant("x", player_position),
                                    "xesam:url": GLib.Variant(
                                        "s", YukiData.playing_url
                                    ),
                                    "xesam:title": GLib.Variant(
                                        "s", YukiData.playing_channel
                                    ),
                                },
                            ),
                            "Volume": GLib.Variant(
                                "d", float(YukiData.player.volume / 100)
                            ),
                            "Position": GLib.Variant(
                                "x",
                                YukiData.player.time_pos * 1000000
                                if YukiData.player.time_pos
                                else 0,
                            ),
                            "MinimumRate": GLib.Variant("d", 0.01),
                            "MaximumRate": GLib.Variant("d", 5.0),
                            "CanGoNext": GLib.Variant("b", True),
                            "CanGoPrevious": GLib.Variant("b", True),
                            "CanPlay": GLib.Variant("b", True),
                            "CanPause": GLib.Variant("b", True),
                            "CanSeek": GLib.Variant("b", True),
                            "CanControl": GLib.Variant("b", True),
                        },
                        "org.mpris.MediaPlayer2.Playlists": {
                            "PlaylistCount": GLib.Variant("u", len(playlists)),
                            "Orderings": GLib.Variant("as", ("UserDefined",)),
                            "ActivePlaylist": GLib.Variant(
                                "(b(oss))",
                                (
                                    True,
                                    GLib.Variant(
                                        "(oss)",
                                        current_playlist,
                                    ),
                                ),
                            ),
                        },
                    }

            def wait_until():
                while True:
                    if win.isVisible() or YukiData.stopped:
                        return True
                    else:
                        time.sleep(0.1)
                return False

            def mpris_loop_start():
                wait_until()
                if not YukiData.stopped:
                    logger.info("Starting MPRIS loop")
                    try:
                        mpris_owner_bus_id = start_mpris(
                            os.getpid(), mpris_callback, get_mpris_options
                        )
                        YukiData.mpris_ready = True
                        YukiData.mpris_running = True
                        YukiData.mpris_loop.run()
                        logger.info("Stopping MPRIS...")
                        Gio.bus_unown_name(mpris_owner_bus_id)
                    except Exception:
                        logger.warning("Failed to start MPRIS loop!")
                        logger.warning(traceback.format_exc())

            YukiData.mpris_loop = GLib.MainLoop()
            mpris_thread = threading.Thread(target=mpris_loop_start)
            mpris_thread.start()

            class MPRISEventHandler:
                def on_metadata(self):
                    if YukiData.mpris_ready and YukiData.mpris_running:
                        (
                            playback_status,
                            mpris_trackid,
                            artUrl,
                            player_position,
                        ) = get_mpris_metadata()
                        exInMainThread_partial(
                            partial(
                                emit_mpris_change,
                                "org.mpris.MediaPlayer2.Player",
                                {
                                    "PlaybackStatus": GLib.Variant(
                                        "s", playback_status
                                    ),
                                    "Rate": GLib.Variant("d", YukiData.player.speed),
                                    "Metadata": GLib.Variant(
                                        "a{sv}",
                                        {
                                            "mpris:trackid": GLib.Variant(
                                                "o", mpris_trackid
                                            ),
                                            "mpris:artUrl": GLib.Variant("s", artUrl),
                                            "mpris:length": GLib.Variant(
                                                "x", player_position
                                            ),
                                            "xesam:url": GLib.Variant(
                                                "s", YukiData.playing_url
                                            ),
                                            "xesam:title": GLib.Variant(
                                                "s", YukiData.playing_channel
                                            ),
                                        },
                                    ),
                                },
                            )
                        )

                def on_playpause(self):
                    if YukiData.mpris_ready and YukiData.mpris_running:
                        (
                            playback_status,
                            mpris_trackid,
                            artUrl,
                            player_position,
                        ) = get_mpris_metadata()
                        exInMainThread_partial(
                            partial(
                                emit_mpris_change,
                                "org.mpris.MediaPlayer2.Player",
                                {"PlaybackStatus": GLib.Variant("s", playback_status)},
                            )
                        )

                def on_volume(self):
                    if YukiData.mpris_ready and YukiData.mpris_running:
                        exInMainThread_partial(
                            partial(
                                emit_mpris_change,
                                "org.mpris.MediaPlayer2.Player",
                                {
                                    "Volume": GLib.Variant(
                                        "d", float(YukiData.player.volume / 100)
                                    )
                                },
                            )
                        )

                def on_fullscreen(self):
                    if YukiData.mpris_ready and YukiData.mpris_running:
                        exInMainThread_partial(
                            partial(
                                emit_mpris_change,
                                "org.mpris.MediaPlayer2",
                                {"Fullscreen": GLib.Variant("b", YukiData.fullscreen)},
                            )
                        )

            YukiData.event_handler = MPRISEventHandler()
        except Exception:
            logger.warning(traceback.format_exc())
            logger.warning("Failed to set up MPRIS!")

        def update_scheduler_programme():
            channel_list_2 = [channel_name for channel_name in array_sorted]
            ch_choosed = YukiGUI.choosechannel_ch.currentText()
            YukiGUI.tvguide_sch.clear()
            if ch_choosed in channel_list_2:
                tvguide_got = re.sub(
                    "<[^<]+?>", "", update_tvguide(ch_choosed, True)
                ).split("!@#$%^^&*(")[2:]
                for tvguide_el in tvguide_got:
                    if tvguide_el:
                        YukiGUI.tvguide_sch.addItem(tvguide_el)

        def show_scheduler():
            if YukiGUI.scheduler_win.isVisible():
                YukiGUI.scheduler_win.hide()
            else:
                YukiGUI.choosechannel_ch.clear()
                channel_list = [channel_name for channel_name in array_sorted]
                for channel1 in channel_list:
                    YukiGUI.choosechannel_ch.addItem(channel1)
                if YukiData.item_selected in channel_list:
                    YukiGUI.choosechannel_ch.setCurrentIndex(
                        channel_list.index(YukiData.item_selected)
                    )
                YukiGUI.choosechannel_ch.currentIndexChanged.connect(
                    update_scheduler_programme
                )
                update_scheduler_programme()
                moveWindowToCenter(YukiGUI.scheduler_win)
                YukiGUI.scheduler_win.show()

        def mpv_volume_set_custom():
            mpv_volume_set()

        YukiGUI.btn_playpause.clicked.connect(mpv_play)
        YukiGUI.btn_stop.clicked.connect(mpv_stop)
        YukiGUI.btn_fullscreen.clicked.connect(mpv_fullscreen)
        YukiGUI.btn_open_recordings_folder.clicked.connect(open_recording_folder)
        YukiGUI.btn_record.clicked.connect(do_record)
        YukiGUI.btn_show_scheduler.clicked.connect(show_scheduler)
        YukiGUI.btn_volume.clicked.connect(mpv_mute)
        YukiGUI.volume_slider.valueChanged.connect(mpv_volume_set_custom)
        YukiGUI.btn_screenshot.clicked.connect(do_screenshot)
        YukiGUI.btn_show_archive.clicked.connect(show_archive)
        if not YukiData.settings["catchupenable"]:
            YukiGUI.btn_show_archive.setVisible(False)
        YukiGUI.btn_show_settings.clicked.connect(show_settings)
        YukiGUI.btn_show_playlists.clicked.connect(show_playlists)
        YukiGUI.btn_tv_guide.clicked.connect(show_tvguide)
        YukiGUI.btn_prev_channel.clicked.connect(prev_channel)
        YukiGUI.btn_next_channel.clicked.connect(next_channel)

        dockWidget_controlPanel.setTitleBarWidget(QtWidgets.QWidget())
        dockWidget_controlPanel.setWidget(YukiGUI.controlpanel_dock_widget)
        dockWidget_controlPanel.setFloating(False)
        dockWidget_controlPanel.setFixedHeight(DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH)
        dockWidget_controlPanel.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
        )
        win.addDockWidget(
            QtCore.Qt.DockWidgetArea.BottomDockWidgetArea, dockWidget_controlPanel
        )

        YukiGUI.progress.hide()
        YukiGUI.start_label.hide()
        YukiGUI.stop_label.hide()
        dockWidget_controlPanel.setFixedHeight(DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW)

        YukiData.state = QtWidgets.QLabel(win)
        YukiData.state.setStyleSheet("background-color: " + BCOLOR)
        YukiData.state.setFont(YukiGUI.font_12_bold)
        YukiData.state.setWordWrap(True)
        YukiData.state.move(50, 50)
        YukiData.state.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        class Slider(QtWidgets.QSlider):
            def getRewindTime(self):
                s_start = None
                s_stop = None
                s_index = None
                if YukiData.archive_epg:
                    s_start = datetime.datetime.strptime(
                        YukiData.archive_epg[1], "%d.%m.%Y %H:%M:%S"
                    ).timestamp()
                    s_stop = datetime.datetime.strptime(
                        YukiData.archive_epg[2], "%d.%m.%Y %H:%M:%S"
                    ).timestamp()
                    s_index = YukiData.archive_epg[3]
                else:
                    if YukiData.settings["epg"] and exists_in_epg(
                        YukiData.playing_channel.lower(), YukiData.programmes
                    ):
                        prog1 = get_epg(
                            YukiData.programmes, YukiData.playing_channel.lower()
                        )
                        for pr in prog1:
                            if time.time() > pr["start"] and time.time() < pr["stop"]:
                                s_start = pr["start"]
                                # s_stop = pr["stop"]
                                s_stop = datetime.datetime.now().timestamp()
                                s_index = prog1.index(pr)
                if not s_start:
                    return None
                return (
                    s_start + (self.value() / 100) * (s_stop - s_start),
                    s_stop,
                    s_index,
                )

            def mouseMoveEvent(self, event1):
                if YukiData.playing_channel:
                    rewind_time = self.getRewindTime()
                    if rewind_time:
                        QtWidgets.QToolTip.showText(
                            self.mapToGlobal(event1.pos()),
                            datetime.datetime.fromtimestamp(rewind_time[0]).strftime(
                                "%H:%M:%S"
                            ),
                        )
                super().mouseMoveEvent(event1)

            def doMouseReleaseEvent(self):
                if YukiData.playing_channel:
                    QtWidgets.QToolTip.hideText()
                    rewind_time = self.getRewindTime()
                    if rewind_time:
                        YukiData.rewind_value = self.value()
                        do_open_archive(
                            "#__rewind__#__archive__"
                            + urllib.parse.quote_plus(
                                json.dumps(
                                    [
                                        YukiData.playing_channel,
                                        datetime.datetime.fromtimestamp(
                                            rewind_time[0]
                                        ).strftime("%d.%m.%Y %H:%M:%S"),
                                        datetime.datetime.fromtimestamp(
                                            rewind_time[1]
                                        ).strftime("%d.%m.%Y %H:%M:%S"),
                                        rewind_time[2],
                                        True,
                                    ]
                                )
                            )
                        )

            def mouseReleaseEvent(self, event1):
                self.doMouseReleaseEvent()
                super().mouseReleaseEvent(event1)

        YukiGUI.create_rewind(win, Slider, BCOLOR)

        YukiData.static_text = ""
        YukiData.gl_is_static = False
        YukiData.previous_text = ""

        def set_text_state(text="", is_previous=False):
            if is_previous:
                text = YukiData.previous_text
            else:
                YukiData.previous_text = text
            if YukiData.gl_is_static:
                br = "    "
                if not text or not YukiData.static_text:
                    br = ""
                text = YukiData.static_text + br + text
            win.update()
            YukiData.state.setText(text)

        def set_text_static(is_static):
            YukiData.static_text = ""
            YukiData.gl_is_static = is_static

        YukiData.state.setTextYuki = set_text_state
        YukiData.state.setStaticYuki = set_text_static
        YukiData.state.hide()

        def getUserAgent():
            try:
                userAgent2 = YukiData.player.user_agent
            except Exception:
                userAgent2 = def_user_agent
            return userAgent2

        def saveLastChannel():
            if YukiData.playing_url and playmode_selector.currentIndex() == 0:
                current_group_0 = 0
                if YukiData.combobox.currentIndex() != 0:
                    try:
                        current_group_0 = groups.index(
                            YukiData.array[YukiData.playing_channel]["tvg-group"]
                        )
                    except Exception:
                        pass
                current_channel_0 = 0
                try:
                    current_channel_0 = win.listWidget.currentRow()
                except Exception:
                    pass
                lastfile = open(
                    str(Path(LOCAL_DIR, "lastchannels.json")), "w", encoding="utf8"
                )
                lastfile.write(
                    json.dumps(
                        [
                            YukiData.playing_channel,
                            YukiData.playing_url,
                            getUserAgent(),
                            current_group_0,
                            current_channel_0,
                        ]
                    )
                )
                lastfile.close()
            else:
                if os.path.isfile(str(Path(LOCAL_DIR, "lastchannels.json"))):
                    os.remove(str(Path(LOCAL_DIR, "lastchannels.json")))

        def cur_win_width():
            w1_width = 0
            for app_scr in app.screens():
                w1_width += app_scr.size().width()
            return w1_width

        def cur_win_height():
            w1_height = 0
            for app_scr in app.screens():
                w1_height += app_scr.size().height()
            return w1_height

        def myExitHandler_before():
            if comm_instance.comboboxIndex != -1:
                write_option(
                    "comboboxindex",
                    {
                        "m3u": YukiData.settings["m3u"],
                        "index": comm_instance.comboboxIndex,
                    },
                )
            try:
                if get_first_run():
                    logger.info("Saving active vf filters...")
                    write_option("vf_filters", get_active_vf_filters())
                    logger.info("Active vf filters saved")
            except Exception:
                pass
            try:
                if not YukiData.first_start:
                    logger.info("Saving main window position / width / height...")
                    write_option(
                        "window",
                        {
                            "x": win.geometry().x(),
                            "y": win.geometry().y(),
                            "w": win.width(),
                            "h": win.height(),
                        },
                    )
                    logger.info("Main window position / width / height saved")
                    if YukiData.settings["panelposition"] == 2:
                        logger.info(
                            "Saving separate playlist window "
                            "position / width / height..."
                        )
                        write_option(
                            "separate_playlist",
                            {
                                "x": dockWidget_playlist.geometry().x(),
                                "y": dockWidget_playlist.geometry().y(),
                                "w": dockWidget_playlist.width(),
                                "h": dockWidget_playlist.height(),
                            },
                        )
                        logger.info(
                            "Separate playlist window position / width / height saved"
                        )
            except Exception:
                pass
            try:
                write_option(
                    "compactstate",
                    {
                        "compact_mode": YukiData.compact_mode,
                        "playlist_hidden": YukiData.playlist_hidden,
                        "controlpanel_hidden": YukiData.controlpanel_hidden,
                    },
                )
            except Exception:
                pass
            try:
                write_option("volume", int(YukiData.volume))
            except Exception:
                pass
            save_player_tracks()
            saveLastChannel()
            stop_record()
            for rec_1 in sch_recordings:
                do_stop_record(rec_1)
            if YukiData.mpris_loop:
                YukiData.mpris_running = False
                YukiData.mpris_loop.quit()
            YukiData.stopped = True
            if YukiData.epg_thread_2:
                try:
                    YukiData.epg_thread_2.kill()
                except Exception:
                    try:
                        YukiData.epg_thread_2.terminate()
                    except Exception:
                        pass
            if multiprocessing_manager:
                multiprocessing_manager.shutdown()
            for process_3 in active_children():
                try:
                    process_3.kill()
                except Exception:
                    try:
                        process_3.terminate()
                    except Exception:
                        pass

        def myExitHandler():
            myExitHandler_before()
            logger.info("Stopped")
            if not YukiData.do_save_settings:
                sys.exit(0)

        YukiData.first_boot_1 = True

        YukiData.waiting_for_epg = False
        YukiData.epg_failed = False

        def get_catchup_days(is_seconds=False):
            try:
                catchup_days1 = min(
                    max(
                        1,
                        max(
                            int(YukiData.array[xc1]["catchup-days"])
                            for xc1 in YukiData.array
                            if "catchup-days" in YukiData.array[xc1]
                        ),
                    ),
                    7,
                )
            except Exception:
                catchup_days1 = 7
            if not YukiData.settings["catchupenable"]:
                catchup_days1 = 7
            if is_seconds:
                catchup_days1 = 86400 * (catchup_days1 + 1)
            return catchup_days1

        logger.info(f"catchup-days = {get_catchup_days()}")

        YukiData.epg_data = None

        def timer_channels_redraw():
            YukiData.ic += 0.1
            # redraw every 15 seconds
            if YukiData.ic > (
                14.9 if not YukiData.mp_manager_dict["logos_inprogress"] else 2.9
            ):
                YukiData.ic = 0
                btn_update_click()
            YukiData.ic3 += 0.1
            # redraw every 15 seconds
            if YukiData.ic3 > (
                14.9 if not YukiData.mp_manager_dict["logosmovie_inprogress"] else 2.9
            ):
                YukiData.ic3 = 0
                update_movie_icons()

        @idle_function
        def thread_tvguide_update_1(unused=None):
            YukiData.state.setStaticYuki(True)
            YukiData.state.show()
            YukiData.static_text = _("Updating TV guide...")
            YukiData.state.setTextYuki("")
            YukiData.time_stop = time.time() + 3

        @idle_function
        def thread_tvguide_update_2(unused=None):
            YukiData.state.setStaticYuki(False)
            YukiData.state.show()
            YukiData.state.setTextYuki(_("TV guide update error!"))
            YukiData.time_stop = time.time() + 3

        @async_gui_blocking_function
        def thread_tvguide_update():
            while not YukiData.stopped:
                if not YukiData.first_boot:
                    YukiData.first_boot = True
                    if YukiData.settings["epg"] and not YukiData.epg_failed:
                        if not YukiData.use_local_tvguide:
                            update_epg = not YukiData.settings["donotupdateepg"]
                            if not YukiData.first_boot_1:
                                update_epg = True
                            if update_epg:
                                if YukiData.epg_update_allowed:
                                    YukiData.epg_updating = True
                                    thread_tvguide_update_1()
                                    try:
                                        YukiData.epg_data = None
                                        YukiData.waiting_for_epg = True
                                        YukiData.epg_data = (
                                            get_context("spawn")
                                            .Pool(1)
                                            .apply(
                                                worker,
                                                (
                                                    YukiData.settings,
                                                    get_catchup_days(),
                                                    YukiData.mp_manager_dict,
                                                ),
                                            )
                                        )
                                    except Exception as e1:
                                        YukiData.epg_failed = True
                                        logger.warning(
                                            "[TV guide, part 1] Caught exception: "
                                            + str(e1)
                                        )
                                        logger.warning(traceback.format_exc())
                                        thread_tvguide_update_2()
                                        YukiData.epg_updating = False
                            else:
                                logger.info("EPG update at boot disabled")
                            YukiData.first_boot_1 = False
                        else:
                            YukiData.programmes = {
                                prog0.lower(): YukiData.tvguide_sets[prog0]
                                for prog0 in YukiData.tvguide_sets
                            }
                            btn_update_click()  # start update in main thread
                time.sleep(0.1)

        def timer_record():
            try:
                YukiData.ic1 += 0.1
                if YukiData.ic1 > 0.9:
                    YukiData.ic1 = 0
                    # executing every second
                    if YukiData.is_recording:
                        if not YukiData.recording_time:
                            YukiData.recording_time = time.time()
                        record_time = format_seconds(
                            time.time() - YukiData.recording_time
                        )
                        if os.path.isfile(YukiData.record_file):
                            record_size = convert_size(
                                os.path.getsize(YukiData.record_file)
                            )
                            YukiGUI.lbl2.setText(
                                "REC " + record_time + " - " + record_size
                            )
                        else:
                            YukiData.recording_time = time.time()
                            YukiGUI.lbl2.setText(_("Waiting for record"))
                win.update()
                if (time.time() > YukiData.time_stop) and YukiData.time_stop != 0:
                    YukiData.time_stop = 0
                    if not YukiData.gl_is_static:
                        YukiData.state.hide()
                        win.update()
                    else:
                        YukiData.state.setTextYuki("")
            except Exception:
                pass

        YukiData.x_conn = None

        def do_reconnect():
            if (YukiData.playing_channel and not YukiData.is_loading) and (
                YukiData.player.cache_buffering_state == 0
            ):
                logger.info("Reconnecting to stream")
                try:
                    doPlay(*comm_instance.do_play_args)
                except Exception:
                    logger.warning("Failed reconnecting to stream - no known URL")
            YukiData.x_conn = None

        YukiData.connprinted = False

        def check_connection():
            if YukiData.settings["autoreconnection"]:
                if YukiData.playing_group == 0:
                    if not YukiData.connprinted:
                        YukiData.connprinted = True
                        logger.info("Connection loss detector enabled")
                    try:
                        if (
                            YukiData.playing_channel and not YukiData.is_loading
                        ) and YukiData.player.cache_buffering_state == 0:
                            if not YukiData.x_conn:
                                logger.warning(
                                    "Connection to stream lost, waiting 5 secs..."
                                )
                                YukiData.x_conn = QtCore.QTimer()
                                YukiData.x_conn.timeout.connect(do_reconnect)
                                YukiData.x_conn.start(25000)
                    except Exception:
                        logger.warning("Failed to set connection loss detector!")
            else:
                if not YukiData.connprinted:
                    YukiData.connprinted = True
                    logger.info("Connection loss detector disabled")

        def timer_check_tvguide_obsolete():
            try:
                if win.isVisible():
                    check_connection()
                    try:
                        if YukiData.player.video_bitrate:
                            bitrate_arr = [
                                _("bps") + " ",
                                _("kbps"),
                                _("Mbps"),
                                _("Gbps"),
                                _("Tbps"),
                            ]
                            video_bitrate = " - " + str(
                                format_bytes(YukiData.player.video_bitrate, bitrate_arr)
                            )
                        else:
                            video_bitrate = ""
                    except Exception:
                        video_bitrate = ""
                    try:
                        audio_codec = YukiData.player.audio_codec.split(" ")[0].strip()
                    except Exception:
                        audio_codec = "no audio"
                    try:
                        codec = YukiData.player.video_codec.split(" ")[0].strip()
                        width = YukiData.player.width
                        height = YukiData.player.height
                    except Exception:
                        codec = "png"
                        width = 800
                        height = 600
                    if YukiData.player.avsync:
                        avsync = str(round(YukiData.player.avsync, 2))
                        deavsync = round(YukiData.player.avsync, 2)
                        if deavsync < 0:
                            deavsync = deavsync * -1
                        if deavsync > 0.999:
                            avsync = f"<span style='color: #B58B00;'>{avsync}</span>"
                    else:
                        avsync = "0.0"
                    if (
                        not (codec.lower() == "png" and width == 800 and height == 600)
                    ) and (width and height):
                        if YukiData.settings["hidebitrateinfo"]:
                            YukiGUI.label_video_data.setText("")
                            YukiGUI.label_avsync.setText("")
                        else:
                            YukiGUI.label_video_data.setText(
                                f"  {width}x{height}"
                                f" - {codec} / {audio_codec}{video_bitrate} -"
                            )
                            YukiGUI.label_avsync.setText(f"A-V {avsync}")
                        if loading.text() == _("Loading..."):
                            hideLoading()
                    else:
                        YukiGUI.label_video_data.setText("")
                        YukiGUI.label_avsync.setText("")
                    YukiData.ic2 += 0.1
                    if YukiData.ic2 > 9.9:
                        YukiData.ic2 = 0
                        if not YukiData.epg_updating:
                            if not is_program_actual(
                                YukiData.programmes, YukiData.epg_ready
                            ):
                                force_update_epg()
            except Exception:
                pass

        @idle_function
        def thread_tvguide_update_pt2_1(unused=None):
            YukiData.state.setStaticYuki(False)
            YukiData.state.show()
            YukiData.state.setTextYuki(_("TV guide update done!"))
            YukiData.time_stop = time.time() + 3

        YukiData.thread_tvguide_update_pt2_e2 = ""

        @idle_function
        def thread_tvguide_update_pt2_3(unused=None):
            YukiData.state.setStaticYuki(False)
            YukiData.state.show()
            if "Programme not actual" in str(YukiData.thread_tvguide_update_pt2_e2):
                YukiData.state.setTextYuki(_("EPG is outdated!"))
            else:
                YukiData.state.setTextYuki(_("TV guide update error!"))
            YukiData.time_stop = time.time() + 3

        @async_gui_blocking_function
        def thread_tvguide_update_pt2_2(unused=None):
            time.sleep(0.5)
            thread_tvguide_update_pt2_3()

        @async_gui_blocking_function
        def thread_tvguide_update_pt2():
            while not YukiData.stopped:
                if (
                    YukiData.waiting_for_epg
                    and YukiData.epg_data
                    and len(YukiData.epg_data) == 7
                ):
                    try:
                        if not YukiData.epg_data[3]:
                            YukiData.thread_tvguide_update_pt2_e2 = YukiData.epg_data[4]
                            thread_tvguide_update_pt2_2()
                            raise YukiData.epg_data[4]
                        YukiData.programmes = {
                            prog0.lower(): YukiData.epg_data[1][prog0]
                            for prog0 in YukiData.epg_data[1]
                        }
                        if not is_program_actual(
                            YukiData.programmes, YukiData.epg_ready
                        ):
                            raise Exception("Programme not actual")
                        thread_tvguide_update_pt2_1()
                        YukiData.prog_ids = YukiData.epg_data[5]
                        YukiData.epg_icons = YukiData.epg_data[6]
                        YukiData.tvguide_sets = YukiData.programmes
                        save_tvguide_sets()
                        btn_update_click()  # start update in main thread
                    except Exception as e2:
                        YukiData.epg_failed = True
                        logger.warning(
                            "[TV guide, part 2] Caught exception: " + str(e2)
                        )
                        logger.warning(traceback.format_exc())
                        YukiData.thread_tvguide_update_pt2_e2 = e2
                        thread_tvguide_update_pt2_2()
                    YukiData.epg_updating = False
                    YukiData.waiting_for_epg = False
                time.sleep(1)

        YukiData.thread_tvguide_progress_lock = False

        def timer_tvguide_progress():
            try:
                if not YukiData.thread_tvguide_progress_lock:
                    YukiData.thread_tvguide_progress_lock = True
                    try:
                        if YukiData.waiting_for_epg:
                            if (
                                "epg_progress" in YukiData.mp_manager_dict
                                and YukiData.mp_manager_dict["epg_progress"]
                            ):
                                YukiData.static_text = YukiData.mp_manager_dict[
                                    "epg_progress"
                                ]
                                YukiData.state.setTextYuki(is_previous=True)
                    except Exception:
                        pass
                    YukiData.thread_tvguide_progress_lock = False
            except Exception:
                pass

        def timer_update_time():
            try:
                YukiGUI.scheduler_clock.setText(get_current_time())
            except Exception:
                pass

        def timer_osc():
            try:
                if win.isVisible():
                    if YukiData.playing_url:
                        if not YukiData.settings["hidempv"]:
                            try:
                                if not YukiData.force_turnoff_osc:
                                    set_mpv_osc(True)
                                else:
                                    set_mpv_osc(False)
                            except Exception:
                                pass
                    else:
                        try:
                            set_mpv_osc(False)
                        except Exception:
                            pass
            except Exception:
                pass

        YukiData.dockWidget_playlistVisible = False
        YukiData.dockWidget_controlPanelVisible = False
        YukiData.rewindWidgetVisible = False

        dockWidget_playlist.installEventFilter(win)

        YukiData.prev_cursor = QtGui.QCursor.pos()
        YukiData.last_cursor_moved = 0
        YukiData.last_cursor_time = 0

        def timer_cursor():
            show_cursor = False
            cursor_offset = (
                QtGui.QCursor.pos().x()
                - YukiData.prev_cursor.x()
                + QtGui.QCursor.pos().y()
                - YukiData.prev_cursor.y()
            )
            if cursor_offset < 0:
                cursor_offset = cursor_offset * -1
            if cursor_offset > 5:
                YukiData.prev_cursor = QtGui.QCursor.pos()
                if (time.time() - YukiData.last_cursor_moved) > 0.3:
                    YukiData.last_cursor_moved = time.time()
                    YukiData.last_cursor_time = time.time() + 1
                    show_cursor = True
            show_cursor_really = True
            if not show_cursor:
                show_cursor_really = time.time() < YukiData.last_cursor_time
            if YukiData.fullscreen:
                try:
                    if show_cursor_really:
                        win.container.unsetCursor()
                    else:
                        win.container.setCursor(QtCore.Qt.CursorShape.BlankCursor)
                except Exception:
                    pass
            else:
                try:
                    win.container.unsetCursor()
                except Exception:
                    pass

        def maptoglobal(x6, y6):
            return win.mapToGlobal(QtCore.QPoint(x6, y6))

        def show_playlist_fullscreen():
            if YukiData.settings["panelposition"] in (0, 2):
                YukiGUI.playlist_widget.move(
                    maptoglobal(win.width() - DOCKWIDGET_PLAYLIST_WIDTH, 0)
                )
            else:
                YukiGUI.playlist_widget.move(maptoglobal(0, 0))
            YukiGUI.playlist_widget.setFixedWidth(DOCKWIDGET_PLAYLIST_WIDTH)
            YukiGUI.playlist_widget_height = win.height() - 50
            YukiGUI.playlist_widget.resize(
                YukiGUI.playlist_widget.width(), YukiGUI.playlist_widget_height
            )
            YukiGUI.playlist_widget.setWindowOpacity(0.55)
            YukiGUI.playlist_widget.setWindowFlags(
                QtCore.Qt.WindowType.CustomizeWindowHint
                | QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.X11BypassWindowManagerHint
            )
            YukiGUI.pl_layout.addWidget(YukiGUI.widget)
            YukiGUI.playlist_widget.show()

        def hide_playlist_fullscreen():
            YukiGUI.pl_layout.removeWidget(YukiGUI.widget)
            dockWidget_playlist.setWidget(YukiGUI.widget)
            YukiGUI.playlist_widget.hide()

        YukiData.VOLUME_SLIDER_WIDTH = False

        def resizeandmove_controlpanel():
            lb2_width = 0
            cur_screen = QtWidgets.QApplication.primaryScreen()
            try:
                cur_screen = win.screen()
            except Exception:
                pass
            cur_width = cur_screen.availableGeometry().width()
            YukiGUI.controlpanel_widget.setFixedWidth(cur_width)
            for lb2_wdg in YukiGUI.show_lbls_fullscreen:
                if (
                    YukiGUI.controlpanel_layout.indexOf(lb2_wdg) != -1
                    and lb2_wdg.isVisible()
                ):
                    lb2_width += lb2_wdg.width() + 10
            YukiGUI.controlpanel_widget.setFixedWidth(lb2_width + 30)
            p_3 = (
                win.container.frameGeometry().center()
                - QtCore.QRect(
                    QtCore.QPoint(), YukiGUI.controlpanel_widget.sizeHint()
                ).center()
            )
            YukiGUI.controlpanel_widget.move(
                maptoglobal(p_3.x() - 100, win.height() - 100)
            )

        def show_controlpanel_fullscreen():
            if not YukiData.VOLUME_SLIDER_WIDTH:
                YukiData.VOLUME_SLIDER_WIDTH = YukiGUI.volume_slider.width()
            YukiGUI.volume_slider.setFixedWidth(YukiData.VOLUME_SLIDER_WIDTH)
            YukiGUI.controlpanel_widget.setWindowOpacity(0.55)
            if YukiGUI.channelfilter.usePopup:
                YukiGUI.controlpanel_widget.setWindowFlags(
                    QtCore.Qt.WindowType.CustomizeWindowHint
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.X11BypassWindowManagerHint
                    | QtCore.Qt.WindowType.Popup
                )
            else:
                YukiGUI.controlpanel_widget.setWindowFlags(
                    QtCore.Qt.WindowType.CustomizeWindowHint
                    | QtCore.Qt.WindowType.FramelessWindowHint
                    | QtCore.Qt.WindowType.X11BypassWindowManagerHint
                )
            YukiGUI.cp_layout.addWidget(YukiGUI.controlpanel_dock_widget)
            resizeandmove_controlpanel()
            YukiGUI.controlpanel_widget.show()
            resizeandmove_controlpanel()

        def hide_controlpanel_fullscreen():
            if YukiData.VOLUME_SLIDER_WIDTH:
                YukiGUI.volume_slider.setFixedWidth(YukiData.VOLUME_SLIDER_WIDTH)
            YukiGUI.cp_layout.removeWidget(YukiGUI.controlpanel_dock_widget)
            dockWidget_controlPanel.setWidget(YukiGUI.controlpanel_dock_widget)
            YukiGUI.controlpanel_widget.hide()
            YukiGUI.rewind.hide()

        def timer_afterrecord():
            try:
                cur_recording = False
                if not YukiGUI.lbl2.isVisible():
                    if "REC / " not in YukiGUI.lbl2.text():
                        cur_recording = is_ffmpeg_recording() is False
                    else:
                        cur_recording = is_recording_func() is not True
                    if cur_recording:
                        showLoading2()
                    else:
                        hideLoading2()
            except Exception:
                pass

        YukiData.menubar_state = False

        def timer_shortcuts():
            try:
                if not YukiData.fullscreen:
                    menubar_new_st = win.menuBar().isVisible()
                    if menubar_new_st != YukiData.menubar_state:
                        YukiData.menubar_state = menubar_new_st
                        if YukiData.menubar_state:
                            setShortcutState(False)
                        else:
                            setShortcutState(True)
            except Exception:
                pass

        def timer_mouse():
            try:
                if win.isVisible():
                    if (
                        YukiData.state.isVisible()
                        and YukiData.state.text().startswith(_("Volume"))
                        and not is_show_volume()
                    ):
                        YukiData.state.hide()
                    YukiGUI.label_volume.setText(f"{int(YukiData.player.volume)}%")
                    if YukiData.settings["panelposition"] != 2:
                        dockWidget_playlist.setFixedWidth(DOCKWIDGET_PLAYLIST_WIDTH)
                    if YukiData.fullscreen:
                        # Check cursor inside window
                        cur_pos = QtGui.QCursor.pos()
                        is_inside_window = (
                            cur_pos.x() > win.pos().x() - 1
                            and cur_pos.x() < (win.pos().x() + win.width())
                        ) and (
                            cur_pos.y() > win.pos().y() - 1
                            and cur_pos.y() < (win.pos().y() + win.height())
                        )
                        # Playlist
                        if YukiData.settings["showplaylistmouse"]:
                            cursor_x = win.container.mapFromGlobal(
                                QtGui.QCursor.pos()
                            ).x()
                            win_width = win.width()
                            if YukiData.settings["panelposition"] in (0, 2):
                                is_cursor_x = cursor_x > win_width - (
                                    DOCKWIDGET_PLAYLIST_WIDTH + 10
                                )
                            else:
                                is_cursor_x = cursor_x < (
                                    DOCKWIDGET_PLAYLIST_WIDTH + 10
                                )
                            if (
                                is_cursor_x
                                and cursor_x < win_width
                                and is_inside_window
                            ):
                                if not YukiData.dockWidget_playlistVisible:
                                    YukiData.dockWidget_playlistVisible = True
                                    show_playlist_fullscreen()
                            else:
                                YukiData.dockWidget_playlistVisible = False
                                hide_playlist_fullscreen()
                        # Control panel
                        if YukiData.settings["showcontrolsmouse"]:
                            cursor_y = win.container.mapFromGlobal(
                                QtGui.QCursor.pos()
                            ).y()
                            win_height = win.height()
                            is_cursor_y = cursor_y > win_height - (
                                dockWidget_controlPanel.height() + 250
                            )
                            if (
                                is_cursor_y
                                and cursor_y < win_height
                                and is_inside_window
                            ):
                                if not YukiData.dockWidget_controlPanelVisible:
                                    YukiData.dockWidget_controlPanelVisible = True
                                    show_controlpanel_fullscreen()
                            else:
                                YukiData.dockWidget_controlPanelVisible = False
                                hide_controlpanel_fullscreen()
                    if YukiData.settings["rewindenable"]:
                        # Check cursor inside window
                        cur_pos = QtGui.QCursor.pos()
                        is_inside_window = (
                            cur_pos.x() > win.pos().x() - 1
                            and cur_pos.x() < (win.pos().x() + win.width())
                        ) and (
                            cur_pos.y() > win.pos().y() - 1
                            and cur_pos.y() < (win.pos().y() + win.height())
                        )
                        # Rewind
                        cursor_y = win.container.mapFromGlobal(QtGui.QCursor.pos()).y()
                        win_height = win.height()
                        is_cursor_y = cursor_y > win_height - (
                            dockWidget_controlPanel.height() + 250
                        )
                        if (
                            is_cursor_y
                            and cursor_y < win_height
                            and is_inside_window
                            and YukiData.playing_channel
                            and YukiData.playing_channel in YukiData.array
                            and YukiData.current_prog1
                            and not YukiData.check_playlist_visible
                            and not YukiData.check_controlpanel_visible
                        ):
                            if not YukiData.rewindWidgetVisible:
                                YukiData.rewindWidgetVisible = True
                                win.resize_rewind()
                                YukiGUI.rewind.show()
                        else:
                            YukiData.rewindWidgetVisible = False
                            if YukiGUI.rewind.isVisible():
                                if YukiData.rewind_value:
                                    if (
                                        YukiData.rewind_value
                                        != YukiGUI.rewind_slider.value()
                                    ):
                                        YukiGUI.rewind_slider.doMouseReleaseEvent()
                                YukiGUI.rewind.hide()
            except Exception:
                pass

        @idle_function
        def show_hide_playlist(unused=None):
            if not YukiData.fullscreen:
                if dockWidget_playlist.isVisible():
                    YukiData.playlist_hidden = True
                    dockWidget_playlist.hide()
                else:
                    YukiData.playlist_hidden = False
                    dockWidget_playlist.show()

        def lowpanel_ch():
            if dockWidget_controlPanel.isVisible():
                YukiData.controlpanel_hidden = True
                dockWidget_controlPanel.hide()
            else:
                YukiData.controlpanel_hidden = False
                dockWidget_controlPanel.show()

        # Key bindings
        def key_quit():
            YukiGUI.settings_win.close()
            YukiGUI.shortcuts_win.close()
            YukiGUI.shortcuts_win_2.close()
            win.close()
            YukiGUI.help_win.close()
            YukiGUI.streaminfo_win.close()
            YukiGUI.license_win.close()
            myExitHandler()
            app.quit()

        def dockwidget_controlpanel_resize_timer():
            try:
                if YukiGUI.start_label.text() and YukiGUI.start_label.isVisible():
                    if (
                        dockWidget_controlPanel.height()
                        != DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH
                    ):
                        dockWidget_controlPanel.setFixedHeight(
                            DOCKWIDGET_CONTROLPANEL_HEIGHT_HIGH
                        )
                else:
                    if (
                        dockWidget_controlPanel.height()
                        != DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW
                    ):
                        dockWidget_controlPanel.setFixedHeight(
                            DOCKWIDGET_CONTROLPANEL_HEIGHT_LOW
                        )
            except Exception:
                pass

        def set_playback_speed(spd):
            try:
                logger.info(f"Set speed to {spd}")
                YukiData.player.speed = spd
                try:
                    YukiData.event_handler.on_metadata()
                except Exception:
                    pass
            except Exception:
                logger.warning("set_playback_speed failed")

        def mpv_seek(secs):
            try:
                if YukiData.playing_channel:
                    logger.info(f"Seeking to {secs} seconds")
                    YukiData.player.command("seek", secs)
            except Exception:
                logger.warning("mpv_seek failed")

        def change_aot_mode():
            if not YukiData.fullscreen:
                if YukiData.aot_action.isChecked():
                    logger.info("change_aot_mode to False")
                    YukiData.aot_action.setChecked(False)
                    disable_always_on_top()
                else:
                    logger.info("change_aot_mode to True")
                    YukiData.aot_action.setChecked(True)
                    enable_always_on_top()

        def mpv_frame_step():
            logger.info("frame-step")
            YukiData.player.command("frame-step")

        def mpv_frame_back_step():
            logger.info("frame-back-step")
            YukiData.player.command("frame-back-step")

        funcs = {
            "show_sort": show_sort,
            "key_t": show_hide_playlist,
            "esc_handler": esc_handler,
            "mpv_fullscreen": mpv_fullscreen,
            "mpv_fullscreen_2": mpv_fullscreen,
            "open_stream_info": open_stream_info,
            "mpv_mute": mpv_mute,
            "key_quit": key_quit,
            "mpv_play": mpv_play,
            "mpv_stop": mpv_stop,
            "do_screenshot": do_screenshot,
            "show_tvguide": show_tvguide,
            "do_record": do_record,
            "prev_channel": prev_channel,
            "next_channel": next_channel,
            "(lambda: my_up_binding())": (lambda: my_up_binding_execute()),
            "(lambda: my_down_binding())": (lambda: my_down_binding_execute()),
            "show_timeshift": show_archive,
            "show_scheduler": show_scheduler,
            "showhideeverything": showhideeverything,
            "show_settings": show_settings,
            "(lambda: set_playback_speed(1.00))": (lambda: set_playback_speed(1.00)),
            "app.quit": app.quit,
            "show_playlists": show_playlists,
            "reload_playlist": reload_playlist,
            "force_update_epg": force_update_epg_act,
            "main_channel_settings": main_channel_settings,
            "show_m3u_editor": show_playlist_editor,
            "my_down_binding_execute": my_down_binding_execute,
            "my_up_binding_execute": my_up_binding_execute,
            "(lambda: mpv_seek(-10))": (lambda: mpv_seek(-10)),
            "(lambda: mpv_seek(10))": (lambda: mpv_seek(10)),
            "(lambda: mpv_seek(-60))": (lambda: mpv_seek(-60)),
            "(lambda: mpv_seek(60))": (lambda: mpv_seek(60)),
            "(lambda: mpv_seek(-600))": (lambda: mpv_seek(-600)),
            "(lambda: mpv_seek(600))": (lambda: mpv_seek(600)),
            "lowpanel_ch_1": lowpanel_ch_1,
            "show_tvguide_2": show_tvguide_2,
            "alwaysontop": change_aot_mode,
            # INTERNAL
            "do_record_1_INTERNAL": do_record,
            "mpv_mute_1_INTERNAL": mpv_mute,
            "mpv_play_1_INTERNAL": mpv_play,
            "mpv_play_2_INTERNAL": mpv_play,
            "mpv_play_3_INTERNAL": mpv_play,
            "mpv_play_4_INTERNAL": mpv_play,
            "mpv_stop_1_INTERNAL": mpv_stop,
            "mpv_stop_2_INTERNAL": mpv_stop,
            "next_channel_1_INTERNAL": next_channel,
            "prev_channel_1_INTERNAL": prev_channel,
            "(lambda: my_up_binding())_INTERNAL": (lambda: my_up_binding_execute()),
            "(lambda: my_down_binding())_INTERNAL": (lambda: my_down_binding_execute()),
            "mpv_frame_step": mpv_frame_step,
            "mpv_frame_back_step": mpv_frame_back_step,
        }

        mki2 = []
        mki2.append(gettext.ngettext("-%d second", "-%d seconds", 10) % 10)
        mki2.append(gettext.ngettext("+%d second", "+%d seconds", 10) % 10)
        mki2.append(gettext.ngettext("-%d minute", "-%d minutes", 1) % 1)
        mki2.append(gettext.ngettext("+%d minute", "+%d minutes", 1) % 1)
        mki2.append(gettext.ngettext("-%d minute", "-%d minutes", 10) % 10)
        mki2.append(gettext.ngettext("+%d minute", "+%d minutes", 10) % 10)

        main_keybinds_translations = {
            "(lambda: mpv_seek(-10))": mki2[0],
            "(lambda: mpv_seek(-60))": mki2[2],
            "(lambda: mpv_seek(-600))": mki2[4],
            "(lambda: mpv_seek(10))": mki2[1],
            "(lambda: mpv_seek(60))": mki2[3],
            "(lambda: mpv_seek(600))": mki2[5],
            "(lambda: my_down_binding())": _("V&olume -").replace("&", ""),
            "(lambda: my_up_binding())": _("Vo&lume +").replace("&", ""),
            "(lambda: set_playback_speed(1.00))": _("&Normal speed").replace("&", ""),
            "app.quit": _("Quit the program") + " (2)",
            "do_record": _("Record"),
            "do_screenshot": _("Screenshot").capitalize(),
            "esc_handler": _("Exit fullscreen"),
            "force_update_epg": _("&Update TV guide").replace("&", ""),
            "key_quit": _("Quit the program"),
            "key_t": _("Show/hide playlist"),
            "lowpanel_ch_1": _("Show/hide controls panel"),
            "main_channel_settings": _("&Video settings").replace("&", ""),
            "mpv_fullscreen": _("&Fullscreen").replace("&", ""),
            "mpv_fullscreen_2": _("&Fullscreen").replace("&", "") + " (2)",
            "mpv_mute": _("&Mute audio").replace("&", ""),
            "mpv_play": _("&Play / Pause").replace("&", ""),
            "mpv_stop": _("&Stop").replace("&", ""),
            "my_down_binding_execute": _("V&olume -").replace("&", ""),
            "my_up_binding_execute": _("Vo&lume +").replace("&", ""),
            "next_channel": _("&Next").replace("&", ""),
            "open_stream_info": _("Stream Information"),
            "prev_channel": _("&Previous").replace("&", ""),
            "show_m3u_editor": _("P&laylist editor").replace("&", ""),
            "show_playlists": _("&Playlists").replace("&", ""),
            "reload_playlist": _("&Update current playlist").replace("&", ""),
            "show_scheduler": _("Scheduler"),
            "show_settings": _("Settings"),
            "show_sort": _("Channel sort"),
            "show_timeshift": _("Archive"),
            "show_tvguide": _("TV guide"),
            "showhideeverything": _("&Compact mode").replace("&", ""),
            "show_tvguide_2": _("TV guide for all channels"),
            "alwaysontop": _("Window always on top"),
            "mpv_frame_step": _("&Frame step").replace("&", ""),
            "mpv_frame_back_step": _("Fra&me back step").replace("&", ""),
        }

        if os.path.isfile(str(Path(LOCAL_DIR, "hotkeys.json"))):
            try:
                with open(
                    str(Path(LOCAL_DIR, "hotkeys.json")), encoding="utf8"
                ) as hotkeys_file_tmp:
                    hotkeys_tmp = json.loads(hotkeys_file_tmp.read())[
                        "current_profile"
                    ]["keys"]
                    YukiData.main_keybinds = hotkeys_tmp
                    logger.info("hotkeys.json found, using it as hotkey settings")
            except Exception:
                logger.warning("failed to read hotkeys.json, using default shortcuts")
                YukiData.main_keybinds = main_keybinds_default.copy()
        else:
            logger.info("No hotkeys.json found, using default hotkeys")
            YukiData.main_keybinds = main_keybinds_default.copy()

        if "show_clock" in YukiData.main_keybinds:
            YukiData.main_keybinds.pop("show_clock")

        seq = get_seq()

        def setShortcutState(st1):
            YukiData.shortcuts_state = st1
            for shortcut_arr in shortcuts:
                for shortcut in shortcuts[shortcut_arr]:
                    if shortcut.key() in seq:
                        shortcut.setEnabled(st1)

        def reload_keybinds():
            for shortcut_1 in shortcuts:
                if not shortcut_1.endswith("_INTERNAL"):
                    sc_new_keybind = QtGui.QKeySequence(get_keybind(shortcut_1))
                    for shortcut_2 in shortcuts[shortcut_1]:
                        shortcut_2.setKey(sc_new_keybind)
            reload_menubar_shortcuts()

        all_keybinds = YukiData.main_keybinds.copy()
        all_keybinds.update(main_keybinds_internal)
        for kbd in all_keybinds:
            shortcuts[kbd] = [
                # Main window
                QShortcut(
                    QtGui.QKeySequence(all_keybinds[kbd]), win, activated=funcs[kbd]
                ),
                # Control panel widget
                QShortcut(
                    QtGui.QKeySequence(all_keybinds[kbd]),
                    YukiGUI.controlpanel_widget,
                    activated=funcs[kbd],
                ),
                # Playlist widget
                QShortcut(
                    QtGui.QKeySequence(all_keybinds[kbd]),
                    YukiGUI.playlist_widget,
                    activated=funcs[kbd],
                ),
            ]
        all_keybinds = False

        setShortcutState(False)

        app.aboutToQuit.connect(myExitHandler)

        vol_remembered = 100
        volume_option = read_option("volume")
        if volume_option is not None:
            vol_remembered = int(volume_option)
            YukiData.volume = vol_remembered
        YukiData.firstVolRun = False

        def restore_compact_state():
            try:
                compactstate = read_option("compactstate")
                if compactstate:
                    if compactstate["compact_mode"]:
                        showhideeverything()
                    else:
                        if compactstate["playlist_hidden"]:
                            show_hide_playlist()
                        if compactstate["controlpanel_hidden"]:
                            lowpanel_ch()
            except Exception:
                pass

        if YukiData.settings["m3u"] and m3u_exists:
            win.show()
            YukiData.aot_action = init_mpv_player()
            win.raise_()
            win.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
            win.activateWindow()
            try:
                combobox_index1 = read_option("comboboxindex")
                if combobox_index1:
                    if combobox_index1["m3u"] == YukiData.settings["m3u"]:
                        if combobox_index1["index"] < YukiData.combobox.count():
                            YukiData.combobox.setCurrentIndex(combobox_index1["index"])
            except Exception:
                pass

            def after_mpv_init():
                if YukiData.needs_resize:
                    logger.info("Fix window size")
                    win.resize(WINDOW_SIZE[0], WINDOW_SIZE[1])
                    qr = win.frameGeometry()
                    qr.moveCenter(
                        QtGui.QScreen.availableGeometry(
                            QtWidgets.QApplication.primaryScreen()
                        ).center()
                    )
                    win.move(qr.topLeft())
                if enable_libmpv_render_context:
                    logger.info("Render context enabled, switching vo to libmpv")
                    YukiData.player["vo"] = "libmpv"
                if not playLastChannel():
                    logger.info("Show splash")
                    mpv_override_play(str(Path("yuki_iptv", ICONS_FOLDER, "main.png")))
                    YukiData.player.pause = True
                else:
                    logger.info("Playing last channel, splash turned off")
                restore_compact_state()

            if enable_libmpv_render_context:
                # Workaround for "No render context set"
                QtCore.QTimer.singleShot(0, after_mpv_init)
            else:
                after_mpv_init()

            YukiData.ic, YukiData.ic1, YukiData.ic2, YukiData.ic3 = 0, 0, 0, 0
            timers_array = {}
            timers = {
                timer_shortcuts: 25,
                timer_mouse: 50,
                timer_cursor: 50,
                timer_channels_redraw: 100,
                timer_record: 100,
                timer_osc: 100,
                timer_check_tvguide_obsolete: 100,
                timer_tvguide_progress: 100,
                timer_update_time: 1000,
                timer_logos_update: 1000,
                record_timer: 1000,
                record_timer_2: 1000,
                timer_afterrecord: 50,
                timer_bitrate: UPDATE_BR_INTERVAL * 1000,
                dockwidget_controlpanel_resize_timer: 50,
            }
            for timer in timers:
                timers_array[timer] = QtCore.QTimer()
                timers_array[timer].timeout.connect(timer)
                timers_array[timer].start(timers[timer])

            # Updating EPG, async
            thread_tvguide_update()
            thread_tvguide_update_pt2()
            update_epg_func()
        else:
            YukiData.first_start = True
            show_playlists()
            YukiGUI.playlists_win.show()
            YukiGUI.playlists_win.raise_()
            YukiGUI.playlists_win.setFocus(QtCore.Qt.FocusReason.PopupFocusReason)
            YukiGUI.playlists_win.activateWindow()
            moveWindowToCenter(YukiGUI.playlists_win)

        app_exit_code = _exec(app)
        if YukiData.do_save_settings:
            start_args = sys.argv
            if "python" not in sys.executable:
                start_args.pop(0)
            subprocess.Popen([sys.executable] + start_args)
        sys.exit(app_exit_code)
    except Exception:
        logger.error("ERROR")
        logger.error("")
        exc = traceback.format_exc()
        show_exception(exc)
        try:
            myExitHandler_before()
        except Exception:
            pass
        for process_4 in active_children():
            try:
                process_4.kill()
            except Exception:
                process_4.terminate()
        sys.exit(1)
