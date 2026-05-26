Summary:	IPTV player with EPG support
Name:		yuki-iptv
Version:	1.0
Release:	1
Group:		Multimedia
License:	GPL-3.0-or-later
URL:		https://codeberg.org/liya/yuki-iptv
Source0:	%{name}-%{version}.tar.gz
BuildRequires:	hicolor-icon-theme
BuildRequires:	gettext
Requires:	python3
Requires:	mpv
%if %{__isa_bits} == 64
Requires:	libmpv.so.2()(64bit)
%else
Requires:	libmpv.so.2
%endif
%if %{defined fedora} || 0%{?centos_version}
Requires:	python3-qt5
Requires:	python3-pillow
%else
Requires:	python3-qt6
%if %{defined mageia}
Requires:	qttranslations6
Requires:	python3-pillow
%else
Requires:	python3-Wand
%endif
%endif
Requires:	python3-gobject
%if 0%{?suse_version} || 0%{?sle_version}
Requires:	python3-Unidecode
%else
Requires:	python3-unidecode
%endif
Requires:	python3-chardet
Requires:	python3-requests
Requires:	python3-setproctitle
Requires:	ffmpeg
Requires:	yt-dlp

%description
IPTV player with EPG support

%files
%{_bindir}/%{name}
%{_datadir}/applications/%{name}.desktop
%{_datadir}/%{name}
/usr/share/locale/*/*/yuki-iptv.mo
%{_prefix}/lib/%{name}
/usr/share/icons/hicolor/scalable/apps/yuki-iptv.svg
/usr/share/metainfo/yuki-iptv.appdata.xml

%dir /usr/share/locale/*
%dir /usr/share/locale/*/*

%global debug_package %{nil}

%post
ldconfig

%prep
%setup -q

%build
make
sed -i "s/__DEB_VERSION__/%{version}/g" usr/lib/yuki-iptv/yuki-iptv.py

%install
cp -af usr %{buildroot}

%changelog
* Thu Jul 04 2024 Liya Astrova <liyaastrova@proton.me> - 0.0.12
  - Translations update from Weblate
  - Change record format to ts
  - Load Qt system translations
  - Add resize grip to playlist in separate window
  - Add support for custom channel logos
  - Better libmpv API version check
* Fri Apr 19 2024 Liya Astrova <liyaastrova@proton.me> - 0.0.11
  - Translations update from Weblate
  - libmpv 0.38.0 fixes
  - Restore channel sorting
  - More verbose exceptions
  - Add "Floating panel position" - "Separate window" option
  - Add option "Hide playlist by left mouse click"
  - Fix "Bad paging"
  - Fix "Jumpy label"
  - Fix "No autoplay after Playing error"
  - Fix "RuntimeError: wrapped C/C++ object of type QAction has been deleted"
  - Fix "Wrong Qt version reported"
  - Disable debug logging for MPRIS
* Sun Mar 17 2024 Liya Astrova <liyaastrova@proton.me> - 0.0.10
  - Translations update from Weblate
  - Show channel URL in stream information window
  - Do not crash if save folder is not writable
  - Fix aspect ratio in channel logos
  - xspf: Add support for logos and VLC extensions
  - Fix crash if EPG title is None
  - Fix playing error not shown correctly in fullscreen mode
  - Remove channel sorting
  - Fix MPRIS
  - Fix VOD matching
  - Add EPG id for XTream channels
* Tue Jan 23 2024 Liya Astrova <liyaastrova@proton.me> - 0.0.9
  - l10n: Update translations
  - Respect XDG environment variables
  - Fix YouTube channels recording
  - Add file filter when adding playlist
  - Add file filter when opening file in M3U editor
* Tue Nov 07 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.8
  - l10n: Update translations
  - Fix parsing channel name with comma
  - Show audio/video desynchronization
  - Show group in channel tooltip
  - Add ability to select EPG date
  - Fix osc disabling via mpv options
  - Fix crashing if save settings called when EPG is updating
  - Fix page selection taking focus and blocking keybinds
  - Fix crash in channel search
  - Add rewind
  - Add frame-step and frame-back-step commands
  - Fix loading playlists with trailing spaces in URL
  - Fix crashing in movies
  - Fix requests timeout
  - Fix changing playlist name when editing it
  - Update XTream library
  - Show XTream expiration dates in playlists window
  - Fix catchup on XTream playlists
  - Load EPG for XTream
* Mon Jun 05 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.7
  - Fix encoding in remote playlists
  - Use Wand for channel logos
  - Add tvg-group tag support
* Sun May 14 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.6
  - Add 'Hide EPG from playlist' option
  - Add 'Multicast optimization' option
  - Fix control panel not aware of multiple screens
  - Detect group in xspf playlists
  - Increase connect and read timeout
* Tue May 02 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.5
  - Load EPG for all days available
* Sat Apr 15 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.4
  - Optimize channel logos load
  - Show warning if ffmpeg crashed
  - Remember video / audio / subtitle tracks for channel
  - Add subtitles selector
  - Fix pause button behaviour on changing channels
  - UI improvements
  - packaging: add python3-pydbus to depends
* Sun Apr 09 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.3
  - EPG: trying to fix EPG cache load freezing
  - EPG: fix XMLTV timezone parsing
  - EPG: re-implement JTV support from scratch
  - EPG: Add support for XMLTV inside zip archive
  - Show Qt and libmpv versions in about window
  - Add TV.ALL EPG format support
  - Fix autoreconnection option
  - Show logo when file ended
  - Fix channel looping
* Thu Apr 06 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.2
  - l10n: Update translations
  - Fix disabling osc via MPV options
  - Make libmpv respect loglevel option
  - Lazy load EPG cache
  - Lazy load channel logos
  - Fix crash if no TV channels found
  - tv guide: add channel search
  - scheduler: add channel search
  - channel logos: respect useragent and referer when downloading
  - settings: disable hardware acceleration and deinterlace by default
  - Change default User-Agent to Mozilla/5.0
  - Allow custom HTTP Referer for individual channels
  - Allow custom user agent
  - Verbose EPG loading
  - Show channel name on changing in fullscreen mode
  - Fix custom channel sort in multiple playlists with same channel name
  - Fix favourites in multiple playlists with same channel name
  - Fix channel settings in multiple playlists with same channel name
  - Update pyxtream library
  - Check playlist name is not empty before saving
  - Drop default EPG URLs
  - Drop import from Hypnotix
* Mon Mar 27 2023 Liya Astrova <liyaastrova@proton.me> - 0.0.1
  - Change project name to yuki-iptv
