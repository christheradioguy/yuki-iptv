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
import gettext
import logging

_ = gettext.gettext
logger = logging.getLogger("yuki-iptv exception")


def get_qt_library():
    """Get correct Qt library - PyQt6/PyQt5"""
    qt_library = "none"
    QShortcut = False
    QtWidgets = False
    QtCore = False
    QtGui = False
    QtOpenGLWidgets = False
    try:
        from PyQt6 import QtWidgets
        from PyQt6 import QtCore
        from PyQt6 import QtGui
        from PyQt6 import QtOpenGLWidgets

        QShortcut = QtGui.QShortcut
        qt_library = "PyQt6"
    except Exception:
        from PyQt5 import QtWidgets
        from PyQt5 import QtCore
        from PyQt5 import QtGui

        QtOpenGLWidgets = QtWidgets
        QShortcut = QtWidgets.QShortcut
        qt_library = "PyQt5"
    return qt_library, QtWidgets, QtCore, QtGui, QShortcut, QtOpenGLWidgets


def show_exception(error, title=""):
    qt_library, QtWidgets, QtCore, QtGui, QShortcut, QtOpenGLWidgets = get_qt_library()
    if not QtWidgets.QApplication.instance():
        app = QtWidgets.QApplication(sys.argv)
    else:
        app = QtWidgets.QApplication.instance()  # noqa: F841
    if title:
        title = f"\n{title}"
    message = "{}{}\n\n{}".format(_("yuki-iptv error") + ":", title, str(error))
    logger.warning(message)
    try:
        qt_icon_critical = QtWidgets.QMessageBox.Icon.Critical
    except Exception:
        qt_icon_critical = 3
    msg = QtWidgets.QMessageBox(
        qt_icon_critical,
        _("Error"),
        message,
        QtWidgets.QMessageBox.StandardButton.Ok,
    )
    msg.exec()
