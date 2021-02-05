#!/usr/bin/python3

# Copyright 2020 Mohamed El Morabity
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

"""tv_grab_fr_teleloisirs.py - Grab French television listings using the Télé
Loisirs mobile API in XMLTV format.
"""

from argparse import ArgumentParser
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from datetime import time
from datetime import datetime
from datetime import timedelta
import functools
import logging
import re
import sys
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import Optional
from typing import Union
import urllib.parse

from lxml.etree import Element  # type: ignore # nosec
from lxml.etree import ElementTree  # nosec
from pytz.reference import LocalTimezone  # type: ignore
from requests import Response
from requests import Session
from requests.exceptions import RequestException


class TeleLoisirsException(Exception):
    """Base class for exceptions raised by the module."""


class TeleLoisirs:
    """Implements grabbing and processing functionalities required to generate
    XMLTV data from Télé Loisirs mobile API.
    """

    _API_URL = "https://api-tel.programme-tv.net"
    _API_USER_AGENT = (
        "Tele-Loisirs(7.0.0|7000001) ~ Android(9|28) ~ "
        "mobile(xiaomi|Redmi_Note_8|density=2.75) ~ okhttp(4.8.0)"
    )
    _API_BROADCAST_PROJECTION = [
        "id",
        "startedAt",
        "soundFormat",
        "isMultiLanguage",
        "isVOST",
        "aspectRatio",
        "hasDeafSubtitles",
        "CSAAgeRestriction",
        "isHD",
        "isNew",
        "isRebroadcast",
        "channel{id}",
        "program{id}",
        "endedAt",
    ]
    _API_PROGRAM_PROJECTION = [
        "collectionItemPartIndex",
        "collectionItemTitle",
        "collectionItemIndex",
        "collectionItemPartCount",
        "title",
        "duration",
        "country",
        "releasedYear",
        "originalTitle",
        "isSilent",
        "isInColor",
        "rating",
        "collection{itemIndex,childCount,parentCollection{childCount}}",
        "formatGenre{format{title},genre{name}}",
        "image{height,urlTemplate,width}",
        "programProviderPeople{role,person{fullname},position}",
        "synopsis",
        "review",
        "_links{url}",
    ]
    _API_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S%z"
    _API_XMLTV_CREDIT = {
        "Acteur": "actor",
        "Auteur": "writer",
        "Créateur": "writer",
        "Dialogue": "writer",
        "Guest Star": "guest",
        "Interprète": "actor",
        "Invité": "guest",
        "Mise en scène": "director",
        "Musique": "composer",
        "Présentateur": "presenter",
        "Réalisateur": "director",
        "Scénariste": "writer",
    }
    _API_ETSI_CATEGORIES = {
        "Ballet": "Music / Ballet / Dance",
        "Concert": "Music / Ballet / Dance",
        "Dessin animé": "Children's / Youth programmes",
        "Documentaire sportif": "Sports",
        "Documentaire": "News / Current affairs",
        "Emission sportive": "Sports",
        "Feuilleton": "Movie / Drama",
        "Film": "Movie / Drama",
        "Magazine sportif": "Sports",
        "Magazine": "Magazines / Reports / Documentary",
        "Opéra": "Music / Ballet / Dance",
        "Spectacle": "Show / Game show",
        "Série": "Movie / Drama",
        "Théâtre": "Arts / Culture (without music)",
        "Téléfilm": "Movie / Drama",
    }

    _XMLTV_DATETIME_FORMAT = "%Y%m%d%H%M%S %z"

    def __init__(
        self,
        generator: Optional[str] = None,
        generator_url: Optional[str] = None,
    ):
        self._generator = generator
        self._generator_url = generator_url

        self._session = Session()
        self._session.headers.update({"User-Agent": self._API_USER_AGENT})
        self._session.hooks = {"response": [self._requests_raise_status]}

        self._channels = self._get_channels()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._session:
            self._session.close()

    @staticmethod
    def _requests_raise_status(response: Response, *args, **kwargs) -> None:
        try:
            response.raise_for_status()
        except RequestException as ex:
            logging.debug(
                "Error while retrieving URL %s", response.request.url
            )
            try:
                raise TeleLoisirsException(
                    response.json().get("message") or ex
                )
            except ValueError:
                raise TeleLoisirsException(ex)

    def _query_api(
        self, path: str, **query: Union[int, str]
    ) -> Dict[str, Any]:
        url = "{}/{}".format(self._API_URL, path.strip("/"))
        response = self._session.get(url, params=query)

        logging.debug("Retrieved URL %s", response.request.url)

        data = response.json().get("data", {})

        # Paginated results
        if next_url := data.get("next"):
            parsed_url = urllib.parse.urlparse(next_url)
            query = dict(urllib.parse.parse_qs(parsed_url.query))
            data["items"] += self._query_api(parsed_url.path, **query).get(
                "items", []
            )

        return data

    @classmethod
    def _teleloisirs_to_xmltv_id(cls, channel_id: int) -> str:

        return f"{channel_id}.api-tel.programme-tv.net"

    @staticmethod
    def _get_icon_url(
        url_template: Optional[str],
        width: Optional[int],
        height: Optional[int],
    ) -> Optional[str]:
        if not url_template or not width or not height:
            return None

        return url_template.format(
            transformation="fit",
            width=width,
            height=height,
            parameters="_",
            title="image",
        )

    def _get_channels(self) -> Dict[str, Any]:
        channels = {}
        for package in self._query_api("v2/bouquets.json", limit="auto").get(
            "items", []
        ):
            for channel in package.get("channelBouquets", []):
                channel_data = channel.get("channel", {})
                channel_id = channel_data.get("id")
                channel_name = channel_data.get("title")
                if not channel_id or not channel_name:
                    continue
                image = channel_data.get("image", {})
                channels[self._teleloisirs_to_xmltv_id(channel_id)] = {
                    "id": channel_id,
                    "display-name": channel_name,
                    "icon": {
                        "src": self._get_icon_url(
                            image.get("urlTemplate"),
                            image.get("width"),
                            image.get("height"),
                        ),
                        "width": image.get("width"),
                        "height": image.get("height"),
                    },
                    "url": channel_data.get("_links", {}).get("url"),
                }

        return channels

    def get_available_channels(self) -> Dict[str, str]:
        """Return the list of all available channels on Télé Loisirs, with
        their XMLTV ID and name.
        """

        return {k: v["display-name"] for k, v in self._channels.items()}

    @staticmethod
    def _to_string(value: Union[None, bool, int, str]) -> Optional[str]:
        if isinstance(value, bool):
            return "yes" if value else "no"

        if value:
            stripped_value = str(value).strip()

        if not value or not stripped_value:
            return None

        return stripped_value

    @staticmethod
    def _xmltv_element(
        tag: str,
        text: Union[None, int, str] = None,
        parent: Element = None,
        **attributes: Union[None, int, str],
    ) -> Element:
        attributes = {
            k: w
            for k, v in attributes.items()
            if (w := TeleLoisirs._to_string(v))
        }

        element = Element(tag, **attributes)
        element.text = TeleLoisirs._to_string(text)

        if parent is not None:
            parent.append(element)

        return element

    @staticmethod
    def _xmltv_element_with_text(
        tag: str,
        text: Union[None, int, str],
        parent: Element = None,
        **attributes: Optional[str],
    ) -> Optional[Element]:
        if not TeleLoisirs._to_string(text):
            return None

        return TeleLoisirs._xmltv_element(
            tag, text=text, parent=parent, **attributes
        )

    def _to_xmltv_channel(self, channel_id: str) -> Optional[Element]:
        xmltv_channel = Element("channel", id=channel_id)

        channel_data = self._channels.get(channel_id)
        if not channel_data:
            return None

        # Channel display name
        self._xmltv_element_with_text(
            "display-name",
            channel_data.get("display-name"),
            parent=xmltv_channel,
        )

        # Icon associated to the programme
        self._xmltv_element(
            "icon", parent=xmltv_channel, **channel_data.get("icon", {})
        )

        # URL where you can find out more about the channel
        self._xmltv_element_with_text(
            "url", channel_data.get("url"), parent=xmltv_channel
        )

        return xmltv_channel

    @staticmethod
    # pylint: disable=too-many-arguments
    def _get_xmltv_ns_episode_number(
        season: Optional[int],
        total_seasons: Optional[int],
        episode: Optional[int],
        total_episodes: Optional[int],
        part: Optional[int],
        total_parts: Optional[int],
    ) -> Optional[str]:
        if not season and not episode and not part:
            return None

        result = ""

        if season:
            result = f"{season - 1}"
            if total_seasons:
                result += f"/{total_seasons}"

        result += "."

        if episode:
            result += f"{episode - 1}"
            if total_episodes:
                result += f"/{total_episodes}"

        result += "."

        if not part:
            part = 1
        if not total_parts:
            total_parts = 1

        if part:
            result += f"{part - 1}"
            if total_parts:
                result += f"/{total_parts}"

        return result

    # pylint: disable=too-many-branches
    # pylint: disable=too-many-locals
    # pylint: disable=too-many-statements
    def _to_xmltv_program(
        self, broadcast: Dict[str, Any], program: Dict[str, Any]
    ) -> Optional[Element]:
        broadcast_id = broadcast.get("id")

        # Channel ID
        channel_id = self._teleloisirs_to_xmltv_id(
            broadcast.get("channel", {}).get("id")
        )
        if not channel_id:
            logging.debug(
                "Broadcast %s has no channel ID, skipping", broadcast_id
            )
            return None

        # Start time
        try:
            start = datetime.strptime(
                broadcast.get("startedAt", ""),
                self._API_DATETIME_FORMAT,
            ).strftime(self._XMLTV_DATETIME_FORMAT)
        except ValueError:
            logging.debug(
                "Broadcast %s has no valid start time, skipping",
                broadcast_id,
            )
            return None

        # End time
        stop = None
        try:
            stop = datetime.strptime(
                broadcast.get("endedAt", ""), self._API_DATETIME_FORMAT
            ).strftime(self._XMLTV_DATETIME_FORMAT)
        except ValueError:
            pass

        xmltv_program = self._xmltv_element(
            "programme", start=start, stop=stop, channel=channel_id
        )

        # Programme title
        title = program.get("title") or program.get("collectionItemTitle")
        xmltv_title = self._xmltv_element_with_text(
            "title", title, parent=xmltv_program
        )
        if xmltv_title is None:
            logging.warning(
                "Program %s has no title, skipping", broadcast.get("id")
            )
            return None

        if original_title := program.get("originalTitle"):
            xmltv_title.set("lang", "fr")
            self._xmltv_element_with_text(
                "title", original_title, parent=xmltv_program
            )

        # Sub-title or episode title
        if title != (item_title := program.get("collectionItemTitle")):
            self._xmltv_element_with_text(
                "sub-title", item_title, parent=xmltv_program
            )

        # Description of the programme or episode
        self._xmltv_element_with_text(
            "desc", program.get("synopsis"), parent=xmltv_program
        )

        # Credits for the programme
        xmltv_credits = self._xmltv_element("credits")
        _credits = {
            "director": {},
            "actor": {},
            "writer": {},
            "adapter": {},
            "producer": {},
            "composer": {},
            "editor": {},
            "presenter": {},
            "commentator": {},
            "guest": {},
        }  # type: Dict[str, Dict[str, Element]]

        for people in program.get("programProviderPeople", []):
            position = people.get("position")
            credit = self._API_XMLTV_CREDIT.get(position)
            if not credit:
                if position:
                    logging.debug(
                        'No XMLTV credit defined for function "%s"',
                        position,
                    )
                continue

            if full_name := people.get("person", {}).get("fullname"):
                _credits[credit][full_name] = self._xmltv_element_with_text(
                    credit,
                    full_name,
                    role=people.get("role") if credit == "actor" else None,
                )

        xmltv_credits.extend(
            [e for s in _credits.values() for e in s.values()]
        )
        if len(xmltv_credits) > 0:
            xmltv_program.append(xmltv_credits)

        # Date the programme or film was finished
        self._xmltv_element_with_text(
            "date",
            program.get("releasedYear"),
            parent=xmltv_program,
        )

        # Type of programme
        genres = program.get("formatGenre", {})
        genre = genres.get("format", {}).get("title", "").capitalize()
        self._xmltv_element_with_text(
            "category",
            genre,
            parent=xmltv_program,
            lang="fr",
        )
        if genre != (
            sub_genre := genres.get("genre", {}).get("name", "").capitalize()
        ):
            self._xmltv_element_with_text(
                "category",
                sub_genre,
                parent=xmltv_program,
                lang="fr",
            )
        etsi_category = self._API_ETSI_CATEGORIES.get(genre)
        self._xmltv_element_with_text(
            "category",
            etsi_category,
            parent=xmltv_program,
            lang="en",
        )
        if genre and not etsi_category:
            logging.debug('No ETSI category found for genre "%s"', genre)

        # True length of the programme
        self._xmltv_element_with_text(
            "length",
            program.get("duration"),
            parent=xmltv_program,
            units="seconds",
        )

        # Icon associated to the programme
        image = program.get("image") or {}
        if url_template := image.get("urlTemplate"):
            self._xmltv_element(
                "icon",
                parent=xmltv_program,
                src=self._get_icon_url(
                    url_template,
                    image.get("width"),
                    image.get("height"),
                ),
                width=image.get("width"),
                height=image.get("height"),
            )

        # URL where you can find out more about the programme
        self._xmltv_element_with_text(
            "url",
            program.get("_links", {}).get("url"),
            parent=xmltv_program,
        )

        # Country where the programme was made or one of the countries in a
        # joint production
        if countries := program.get("country", ""):
            for country in countries.split(" - "):
                self._xmltv_element_with_text(
                    "country",
                    country,
                    parent=xmltv_program,
                    lang="fr",
                )

        # Episode number
        collection = program.get("collection") or {}
        self._xmltv_element_with_text(
            "episode-num",
            self._get_xmltv_ns_episode_number(
                collection.get("itemIndex"),
                (collection.get("parentCollection") or {}).get("childCount"),
                program.get("collectionItemIndex"),
                collection.get("childCount"),
                program.get("collectionItemPartIndex"),
                program.get("collectionItemPartCount"),
            ),
            parent=xmltv_program,
            system="xmltv_ns",
        )

        # Video details
        xmltv_video = self._xmltv_element("video", parent=xmltv_program)
        self._xmltv_element_with_text("present", True, parent=xmltv_video)
        self._xmltv_element_with_text(
            "colour", program.get("isInColor"), parent=xmltv_video
        )
        self._xmltv_element_with_text(
            "aspect", broadcast.get("aspectRatio"), parent=xmltv_video
        )
        if broadcast.get("isHD"):
            self._xmltv_element_with_text(
                "quality", "HDTV", parent=xmltv_video
            )

        # Audio details
        xmltv_audio = self._xmltv_element("audio")
        if program.get("isSilent"):
            self._xmltv_element("present", False, parent=xmltv_audio)
        elif stereo := (
            "bilingual"
            if broadcast.get("isMultiLanguage")
            else broadcast.get("soundFormat")
        ):
            self._xmltv_element("present", True, parent=xmltv_audio)
            self._xmltv_element("stereo", stereo, parent=xmltv_audio)
        if xmltv_audio is not None:
            xmltv_program.append(xmltv_audio)

        # Previously shown programme?
        if broadcast.get("isRebroadcast"):
            self._xmltv_element(
                "previously-shown",
                parent=xmltv_program,
            )
        # Premiere programme?
        elif broadcast.get("isNew"):
            self._xmltv_element("premiere", parent=xmltv_program)

        # Subtitles
        if broadcast.get("hasDeafSubtitles"):
            self._xmltv_element(
                "subtitles", parent=xmltv_program, type="deaf-signed"
            )
        if broadcast.get("isVOST"):
            self._xmltv_element(
                "subtitles", parent=xmltv_program, type="onscreen"
            )

        # Rating
        if csa_age_restriction := broadcast.get("CSAAgeRestriction"):
            self._xmltv_element_with_text(
                "value",
                f"Interdit aux moins de {csa_age_restriction} ans",
                parent=self._xmltv_element(
                    "rating", parent=xmltv_program, system="CSA"
                ),
            )

        # Star rating
        if rating := int((program.get("rating") or 0) * 4):
            self._xmltv_element_with_text(
                "value",
                f"{rating}/4",
                parent=self._xmltv_element(
                    "star-rating", parent=xmltv_program, system="Télé Loisirs"
                ),
            )

        # Review
        self._xmltv_element_with_text(
            "review",
            program.get("review"),
            parent=xmltv_program,
            type="text",
            source="Télé Loisirs",
            lang="fr",
        )

        return xmltv_program

    def _get_xmltv_programs(
        self, channel_ids: List[str], days: int, offset: int
    ) -> Generator[Element, None, None]:

        start = datetime.combine(
            date.today(), time(0), tzinfo=LocalTimezone()
        ) + timedelta(days=offset)
        end = start + timedelta(days=days)

        teleloisirs_channel_ids = [
            str(channel_id)
            for c in channel_ids
            if (channel_id := self._channels.get(c, {}).get("id"))
        ]

        # Use dictionary to avoid broadcast duplicates
        broadcasts = {
            i: b
            for b in self._query_api(
                "v2/broadcasts.json",
                channels=",".join(teleloisirs_channel_ids),
                limit="auto",
                projection=",".join(self._API_BROADCAST_PROJECTION),
                since=start.strftime(self._API_DATETIME_FORMAT),
                until=end.strftime(self._API_DATETIME_FORMAT),
            ).get("items", [])
            if (i := b.get("id"))
        }

        with ThreadPoolExecutor() as executor:
            futures = [
                executor.submit(
                    functools.partial(
                        self._query_api,
                        projection=",".join(self._API_PROGRAM_PROJECTION),
                    ),
                    "v2/programs/{}.json".format(
                        b.get("program", {}).get("id")
                    ),
                )
                for b in broadcasts.values()
            ]

            for broadcast, future in zip(broadcasts.values(), futures):
                try:
                    program = future.result().get("item", {})
                except TeleLoisirsException as ex:
                    logging.warning(
                        "Unable to retrieve program data %s: %s",
                        broadcast.get("program", {}).get("id"),
                        ex,
                    )
                    program = {}
                yield self._to_xmltv_program(broadcast, program)

    def _to_xmltv(
        self, channel_ids: List[str], days: int, offset: int
    ) -> ElementTree:
        xmltv = self._xmltv_element(
            "tv",
            **{
                "source-info-name": "Télé Loisirs",
                "source-info-url": "https://www.programme-tv.net/",
                "source-data-url": self._API_URL,
                "generator-info-name": self._generator,
                "generator-info-url": self._generator_url,
            },
        )

        xmltv_channels = {}  # type: Dict[str, Element]
        xmltv_programs = []

        for xmltv_program in self._get_xmltv_programs(
            channel_ids, days, offset
        ):
            if xmltv_program is None:
                continue
            channel_id = xmltv_program.get("channel")
            if channel_id not in xmltv_channels:
                xmltv_channels[channel_id] = self._to_xmltv_channel(channel_id)
            xmltv_programs.append(xmltv_program)

        xmltv.extend(xmltv_channels.values())
        xmltv.extend(xmltv_programs)

        return ElementTree(xmltv)

    def write_xmltv(
        self, channel_ids: List[str], output_file: Path, days: int, offset: int
    ) -> None:
        """Grab Télé Loisirs programs in XMLTV format and write them to
        file.
        """

        logging.debug("Writing XMLTV program to file %s", output_file)

        xmltv_data = self._to_xmltv(channel_ids, days, offset)
        xmltv_data.write(
            str(output_file),
            encoding="UTF-8",
            xml_declaration=True,
            pretty_print=True,
        )


_PROGRAM = "tv_grab_fr_teleloisirs"
__version__ = "1.0"
__url__ = "https://github.com/melmorabity/tv_grab_fr_teleloisirs"

_DESCRIPTION = "France (Télé Loisirs)"
_CAPABILITIES = ["baseline", "manualconfig"]

_DEFAULT_DAYS = 1
_DEFAULT_OFFSET = 0

_DEFAULT_CONFIG_FILE = Path.home().joinpath(".xmltv", f"{_PROGRAM}.conf")


def _print_description() -> None:
    print(_DESCRIPTION)


def _print_version() -> None:
    print("This is {} version {}".format(_PROGRAM, __version__))


def _print_capabilities() -> None:
    print("\n".join(_CAPABILITIES))


def _parse_cli_args() -> Namespace:
    parser = ArgumentParser(
        description="get French television listings using Télé Loisirs mobile "
        "API in XMLTV format"
    )
    parser.add_argument(
        "--description",
        action="store_true",
        help="print the description for this grabber",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="show the version of this grabber",
    )
    parser.add_argument(
        "--capabilities",
        action="store_true",
        help="show the capabilities this grabber supports",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="generate the configuration file by asking the users which "
        "channels to grab",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=_DEFAULT_DAYS,
        help="grab DAYS days of TV data (default: %(default)s)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=_DEFAULT_OFFSET,
        help="grab TV data starting at OFFSET days in the future (default: "
        "%(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/dev/stdout"),
        help="write the XML data to OUTPUT instead of the standard output",
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=_DEFAULT_CONFIG_FILE,
        help="file name to write/load the configuration to/from (default: "
        "%(default)s)",
    )

    log_level_group = parser.add_mutually_exclusive_group()
    log_level_group.add_argument(
        "--quiet",
        action="store_true",
        help="only print error-messages on STDERR",
    )
    log_level_group.add_argument(
        "--debug",
        action="store_true",
        help="provide more information on progress to stderr to help in"
        "debugging",
    )

    return parser.parse_args()


def _read_configuration(
    available_channels: Dict[str, str], config_file: Path
) -> List[str]:

    channel_ids = set()
    with config_file.open("r") as config_reader:
        for line in config_reader:
            match = re.search(r"^\s*channel\s*=\s*(.+)\s*$", line)
            if match is None:
                continue

            channel_id = match.group(1)
            if channel_id in available_channels:
                channel_ids.add(channel_id)

    return list(channel_ids)


def _write_configuration(channel_ids: List[str], config_file: Path) -> None:

    config_file.parent.mkdir(parents=True, exist_ok=True)

    with open(config_file, "w") as config:
        for channel_id in channel_ids:
            print("channel={}".format(channel_id), file=config)


def _configure(available_channels: Dict[str, str], config_file: Path) -> None:
    channel_ids = []
    answers = ["yes", "no", "all", "none"]
    select_all = False
    select_none = False
    print(
        "Select the channels that you want to receive data for.",
        file=sys.stderr,
    )
    for channel_id, channel_name in available_channels.items():
        if not select_all and not select_none:
            while True:
                prompt = f"{channel_name} [{answers} (default=no)] "
                answer = input(prompt).strip()  # nosec
                if answer in answers or answer == "":
                    break
                print(
                    f"invalid response, please choose one of {answers}",
                    file=sys.stderr,
                )
            select_all = answer == "all"
            select_none = answer == "none"
        if select_all or answer == "yes":
            channel_ids.append(channel_id)
        if select_all:
            print(f"{channel_name} yes", file=sys.stderr)
        elif select_none:
            print(f"{channel_name} no", file=sys.stderr)

    _write_configuration(channel_ids, config_file)


def _main() -> None:
    args = _parse_cli_args()

    if args.version:
        _print_version()
        sys.exit()

    if args.description:
        _print_description()
        sys.exit()

    if args.capabilities:
        _print_capabilities()
        sys.exit()

    logging_level = logging.INFO
    if args.quiet:
        logging_level = logging.ERROR
    elif args.debug:
        logging_level = logging.DEBUG
    logging.basicConfig(
        level=logging_level,
        format="%(levelname)s: %(message)s",
    )

    try:
        tele_loisirs = TeleLoisirs(generator=_PROGRAM, generator_url=__url__)
    except TeleLoisirsException as ex:
        logging.error(ex)
        sys.exit(1)

    logging.info("Using configuration file %s", args.config_file)

    available_channels = tele_loisirs.get_available_channels()
    if args.configure:
        _configure(available_channels, args.config_file)
        sys.exit()

    if not args.config_file.is_file():
        logging.error(
            "You need to configure the grabber by running it with --configure"
        )
        sys.exit(1)

    channel_ids = _read_configuration(available_channels, args.config_file)
    if not channel_ids:
        logging.error(
            "Configuration file %s is empty or malformed, delete and run with "
            "--configure",
            args.config_file,
        )
        sys.exit(1)

    try:
        tele_loisirs.write_xmltv(
            channel_ids, args.output, days=args.days, offset=args.offset
        )
    except TeleLoisirsException as ex:
        logging.error(ex)


if __name__ == "__main__":
    _main()
