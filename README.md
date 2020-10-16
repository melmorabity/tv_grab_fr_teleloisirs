# tv_grab_fr_teleloisirs

Grab French television listings using the Télé Loisirs mobile API in XMLTV format.

## Authors

Mohamed El Morabity

## Requirements

The grabber requires Python 3.8 or above. It depends on the following libraries:

* [lxml](https://pypi.python.org/pypi/lxml)
* [pytz](https://pypi.python.org/pypi/pytz)
* [requests](https://pypi.python.org/pypi/requests)

## Usage

    tv_grab_fr_teleloisirs.py --help
    tv_grab_fr_teleloisirs.py [--config-file FILE] --configure
    tv_grab_fr_teleloisirs.py [--config-file FILE] [--output FILE] [--days N] [--offset N] [--quiet] [--debug]
    tv_grab_fr_teleloisirs.py --description
    tv_grab_fr_teleloisirs.py --capabilities
    tv_grab_fr_teleloisirs.py --version

## Description

Output TV listings for several channels available in France and (partly) Europe. The data comes from the Télé Loisirs mobile API.

First run `tv_grab_fr_teleloisirs.py --configure` to choose which channels you want to download. Then running `tv_grab_fr_teleloisirs.py` with no arguments will output listings in XMLTV format to standard output.

    --configure

Ask for each available channel whether to download and write the configuration file.

    --config-file FILE

Set the name of the configuration file, the default is `~/.xmltv/tv_grab_fr_teleloisirs.conf`. This is the file written by `--configure` and read when grabbing.

    --output FILE

Write to `FILE` rather than standard output.

    --days N

Grab `N` days. The default is 1.

    --offset N

Start `N` days in the future. The default is to start from now on (= 0).

    --quiet

Only print error messages to standard error.

    --debug

Provide more information on progress to standard error to help in debugging.

    --capabilities

Show which capabilities the grabber supports. For more information, see http://wiki.xmltv.org/index.php/XmltvCapabilities.

    --description

Show the description of the grabber.

    --version

Show the version of the grabber.

    --help

Print a help message and exit.
