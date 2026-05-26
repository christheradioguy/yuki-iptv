# Fork of yuki IPTV

This is a fork of the prigional project which does the folowing:

- Removes the progress bars (I find them annoying)
- Increases the timeouts for connecting to IPTV streams (it seems to be about 5 seconds by default which is insufficient for programs like Streamlink)

# Building
To build a debian package:
- `dpkg-buildpackage -us -uc -b` the built file will be saved at ../yuki-iptv_0.0.12_all.deb


# Origonal README
The origional README for this project can be found below:


# yuki-iptv

[![Screenshot](https://codeberg.org/attachments/80c17041-40da-410a-848f-558019e69391)](https://codeberg.org/attachments/f0b6d5bb-fa6f-4b16-a375-9e2f9775d6dd)

**yuki-iptv** is an IPTV player with many features, such as:
- M3u / M3u8 / XSPF playlists support
- XTream API support
- Viewing unencrypted streams UDP (multicast), HTTP, HLS (M3u8)
- TV program (EPG) support in XMLTV and JTV formats
- Save channels as favorites
- Stream recording
- Hotkeys
- Channel search
- Display of technical information - video / audio codec, bitrate, resolution
- Channel groups (from playlist and custom)
- Hide channels
- Video settings for each channel - contrast, brightness, hue, saturation, gamma
- Change user agent / HTTP Referer for each channel
- Playlist editor
- TV archive / catchup
- MPRIS support
- and many more...

⚠️ Disclaimer: yuki-iptv doesn't provide content or TV channels.  
The channels and pictures in the screenshots are for demonstration purposes only.

## Content

- [Donate](#donate)
- [deb packages](#deb-packages)
- [Flatpak](#flatpak)
- [Snap Store](#snap-store)
- [Ubuntu PPA](#ubuntu-ppa)
- [Open Build Service (rpm packages)](#open-build-service-rpm-packages)
- [AUR](#aur)
- [HowTo make playlists for movies/series](#howto-make-playlists-for-movies-series)
- [License](#license)
- [Localization](#localization)

## Donate

[**Please donate to support development of yuki-iptv!**](https://codeberg.org/liya/yuki-iptv/src/branch/master/DONATE.md)

## deb packages

https://codeberg.org/liya/yuki-iptv/releases

## Flatpak

[Download on Flathub](https://flathub.org/apps/io.github.yuki_iptv.yuki-iptv)

## Snap Store

[Get it from the Snap Store](https://snapcraft.io/yuki-iptv-unofficial)

## Ubuntu PPA

[Stable PPA](https://launchpad.net/~yuki-iptv/+archive/ubuntu/yuki-iptv-stable)  
```
sudo add-apt-repository ppa:yuki-iptv/yuki-iptv-stable
sudo apt update
sudo apt install yuki-iptv
```
  
[Develop PPA](https://launchpad.net/~yuki-iptv/+archive/ubuntu/yuki-iptv-develop)  
```
sudo add-apt-repository ppa:yuki-iptv/yuki-iptv-develop
sudo apt update
sudo apt install yuki-iptv
```

## Open Build Service (rpm packages)

[Stable](https://software.opensuse.org/download.html?project=home%3Aame-chan%3Ayuki-iptv&package=yuki-iptv)  
[Develop](https://software.opensuse.org/download.html?project=home%3Aame-chan%3Ayuki-iptv-develop&package=yuki-iptv)  
  
**Multimedia codecs:**  
For **Fedora** and **CentOS** you'll need [RPM Fusion](https://rpmfusion.org) repository  
For **openSUSE** you'll need **libopenh264-7** (for h264 decoding) (run ```zypper install libopenh264-7``` as root)  

## AUR

This is an unofficial build maintained by community - it might be outdated.  
**Use at your own risk!**  
  
https://aur.archlinux.org/packages/yuki-iptv

## HowTo make playlists for movies/series
  
Use group **VOD** for movies  
example:  
  
```
#EXTM3U
#EXTINF:-1 group-title="VOD",Channel 1
https://example.com
#EXTINF:-1 group-title="VOD SomeGroup",Channel 2
https://example.com
```
  
Use **ExxSxx** in your playlist to get it shown as series.  
  
- S01E12 = Season 1 Episode 12  
  
example:  
  
```
#EXTM3U
#EXTINF:-1 tvg-name="SomeName S04E06 Season Title 1" group-title="SERIES SomeName",
file:///home/user/Videos/SomeName_4/SomeName.S04E06.mp4
#EXTINF:-1 tvg-name="SomeName S04E07 Season Title 2" group-title="SERIES SomeName",
file:///home/user/Videos/SomeName_4/SomeName.S04E07.mp4
#EXTINF:-1 tvg-name="SomeName S04E09 Season Title 3" group-title="SERIES SomeName",
file:///home/user/Videos/SomeName_4/SomeName.S04E09.mp4
#EXTINF:-1 tvg-name="SomeName S04E10 Season Title 4" group-title="SERIES SomeName",
file:///home/user/Videos/SomeName_4/SomeName.S04E10.mp4
```

## License

```monospace
yuki-iptv is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

yuki-iptv is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with yuki-iptv. If not, see <https://www.gnu.org/licenses/>.
```

[Clarification *(see LICENSE-NOTICE.txt)*](https://codeberg.org/liya/yuki-iptv/src/branch/master/LICENSE-NOTICE.txt)

```monospace
The Font Awesome pictograms are licensed under the CC BY 4.0 License.
Font Awesome Free 5.15.4 by @fontawesome - https://fontawesome.com
License - https://creativecommons.org/licenses/by/4.0/
```

## Localization

To help with localization you can use [Codeberg Translate](https://translate.codeberg.org/engage/yuki-iptv/).
