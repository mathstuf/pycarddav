#!/usr/bin/python
# vim: set fileencoding=utf-8 :
# Copyright (c) 2011-2013 Christian Geier & contributors
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import argparse
import ConfigParser
import logging
import os
import signal
import sys

__productname__ = 'pyCardDAV'
__version__ = '0.4.1'
__author__ = 'Christian Geier'
__copyright__ = 'Copyright 2011-2013 Christian Geier & contributors'
__author_email__ = 'pycarddav@lostpackets.de'
__description__ = 'A CardDAV based address book tool'
__license__ = 'Expat/MIT, see COPYING'
__homepage__ = 'http://lostpackets.de/pycarddav/'


def capture_user_interruption():
    """
    Tries to hide to the user the ugly python backtraces generated by
    pressing Ctrl-C.
    """
    signal.signal(signal.SIGINT, lambda x, y: sys.exit(0))


def enum(**enums):
    return type('Enum', (object,), enums)


class XdgBaseDirectoryHelper(object):
    def __init__(self):
        self._home = os.path.expanduser('~')

        self.config_dirs = [os.environ.get('XDG_CONFIG_HOME') or \
            os.path.join(self._home, '.config')]
        self.config_dirs.extend(
            (os.environ.get('XDG_CONFIG_DIRS') or '/etc/xdg').split(':'))
        self.data_dirs = [os.environ.get('XDG_DATA_HOME') or \
            os.path.join(self._home, '.local', 'share')]
        self.data_dirs.extend(
            (os.environ.get('XDG_DATA_DIRS') or '/usr/local/share:/usr/share').split(':'))

    def build_config_paths(self, resource):
        return [os.path.join(d, resource) for d in self.config_dirs]


class Configuration(object):
    """The pycardsyncer configuration holder.

    Inspired by NameSpace from argparse, Configuration is a simple
    object providing equality by attribute names and values, and a
    representation.

    The Configuration is a collection of attributes which result from
    command line and configuration parsing. Attributes coming from the
    configuration file are named after their matching section and
    option names, with the following format: section__option. For
    consistency, attributes coming from the command line should be
    prefixed with cmd__. However, for the sake of simplicity, options
    which are stored in the default section (like debug), do not keep
    their default__ prefix.
    """

    SECTIONS = enum(CMD='cmd', DAV='dav', DB='sqlite', SSL='ssl', DEFAULT='default')
    DEFAULT_DB_PATH = '~/.pycard/abook.db'
    DEFAULT_PATH = "pycard"
    DEFAULT_FILE = "pycard.conf"

    @classmethod
    def mangle_name(cls, section, option):
        """Mangle a configuration option name.

        This function smartly concatenates section and option names to
        build attributes named like: section__option.
        """
        if section == Configuration.SECTIONS.DEFAULT or not section:
            return option
        else:
            return '__'.join([section, option])

    @classmethod
    def unmangle_name(cls, name):
        """Unmangle a configuration option name."""
        try:
            section, option = name.split('__', 1)
            return section, option
        except ValueError:
            return '', name

    @classmethod
    def prettify_name(cls, section, option):
        """Format a configuration option name to be displayed in logs."""
        if section == Configuration.SECTIONS.DEFAULT or not section:
            return option
        else:
            return '[%s]%s' % (section, option)

    def __init__(self, args):
        for k, v in args.iteritems():
            setattr(self, k, v)

    __hash__ = None

    def __getattr__(self, name):
        # This is only called when the normal mechanism fails, so in
        # practice this should never be called. It is only provided to
        # satisfy pylint that it is okay not to raise E1101 errors on
        # Configuration objects (which holds dynamic attributes).
        raise AttributeError("%r instance has no attribute %r" % (self, name))

    def __eq__(self, other):
        return vars(self) == vars(other)

    def __ne__(self, other):
        return not (self == other)

    def __contains__(self, key):
        return key in self.__dict__

    def __repr__(self):
        strings = []
        for name, value in sorted(self.__dict__.items()):
            if name != 'dav_passwd':
                section, option = Configuration.unmangle_name(name)
                strings.append('%s: %s' % (Configuration.prettify_name(
                    section, option), value))
        return '%s(%s)' % (type(self).__name__, ', '.join(strings))

    def dump(self):
        """Dump the loaded configuration using the logging framework.

        The values displayed here are the exact values which are seen by
        the program, and not the raw values as they are read in the
        configuration file.
        """
        logging.debug('Using configuration:')
        for name, value in sorted(self.__dict__.items()):
            if name != 'dav__passwd':
                section, option = Configuration.unmangle_name(name)
                logging.debug('\t%s: %s', Configuration.prettify_name(
                    section, option), value)


class ConfigurationParser(object):
    """A Configuration setup tool.

    This object takes care of command line parsing as well as
    configuration loading. It also prepares logging and updates its
    output level using the debug flag read from the command-line or
    the configuration file.
    """

    READERS = { bool: ConfigParser.SafeConfigParser.getboolean,
                float: ConfigParser.SafeConfigParser.getfloat,
                int: ConfigParser.SafeConfigParser.getint,
                str: ConfigParser.SafeConfigParser.get }

    def __init__(self, desc):
        self._xdg_helper = XdgBaseDirectoryHelper()

        # Set the configuration current schema.
        self._schema = [
            (Configuration.SECTIONS.DAV, 'user', ''),
            (Configuration.SECTIONS.DAV, 'passwd', ''),
            (Configuration.SECTIONS.DAV, 'resource', ''),
            (Configuration.SECTIONS.DAV, 'auth',
                (self._parse_auth, 'basic')),
            (Configuration.SECTIONS.DAV, 'verify',
                (self._parse_bool_string, 'True')),
            (Configuration.SECTIONS.DB, 'path',
             (os.path.expanduser, Configuration.DEFAULT_DB_PATH)),
            (Configuration.SECTIONS.DEFAULT, 'debug', False),
            (Configuration.SECTIONS.DEFAULT, 'write_support',
             (lambda x: x == 'YesPleaseIDoHaveABackupOfMyData' or False, False)) ]
        self._mandatory = []

        # Build parsers and set common options.
        self._conf_parser = ConfigParser.SafeConfigParser()
        self._arg_parser = argparse.ArgumentParser(
            description=desc, version=__version__)

        self._arg_parser.add_argument(
            "-c", "--config", action="store", dest="cmd__inifile",
            default=self._get_default_configuration_file(), metavar="FILE",
            help="an alternate configuration file")
        self._arg_parser.add_argument(
            "--debug", action="store_true", dest="debug", help="enables debugging")

    def set_mandatory_options(self, options):
        self._mandatory = options

    def _parse_bool_string(self, value):
        """if value is either 'True' or 'False' it returns that value as a bool,
        otherwise it returns the value"""
        if value == 'True':
            return True
        elif value == 'False':
            return False
        else:
            return os.path.expanduser(value)

    def _parse_auth(self, value):  #TODO clean this up, exception etc
        """parse the auth string from the config file"""
        if value == 'digest' or value == 'basic':
            return value
        else:
            raise Exception('value %s not allowed for auth' % value)

    def parse(self):
        """Start parsing.

        Once the commandline parser is eventually configured with specific
        options, this function must be called to start parsing. It first
        parses the command line, and then the configuration file.

        If parsing is successful, the function check is then called.
        When check is a success, the Configuration instance is
        returned. On any error, None is returned.
        """
        args = self._arg_parser.parse_args()

        # Prepare the logger with the level read from command line.
        logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

        filename = args.cmd__inifile
        if not filename:
            logging.error('Could not find configuration file')
            return None
        if not self._conf_parser.read(os.path.expanduser(filename)):
            logging.error('Cannot read %s', filename)
            return None
        else:
            logging.debug('Using configuration from %s', filename)

        conf = self._read_configuration(args)

        # Update the logger using the definitive output level.
        logging.getLogger().setLevel(logging.DEBUG if conf.debug else logging.INFO)

        return conf if self.check(conf) else None

    def check(self, conf):
        """Check the configuration before returning it from parsing.

        This default implementation first warns the user of the
        remaining options found in the configuration file. Then it
        checks if mandatory options are present in the parsed
        configuration. It returns True on success, False otherwise.

        This function can be overriden to augment the checks achieved
        before the parsing function returns.
        """
        result = True

        for section in self._conf_parser.sections():
            for option in self._conf_parser.options(section):
                logging.debug("Ignoring %s in configuration file",
                              Configuration.prettify_name(section, option))

        for section, option in self._mandatory:
            if not getattr(conf, Configuration.mangle_name(section, option)):
                logging.error('Mandatory option %s is missing',
                              Configuration.prettify_name(section, option))
                result = False

        return result

    def _read_configuration(self, overrides):
        """Build the configuration holder.

        First, data declared in the configuration schema are extracted
        from the configuration file, with type checking and possibly
        through a filter. Then these data are completed or overriden
        using the values read from the command line.
        """
        items = {}
        for section, option, kind in self._schema:
            name = Configuration.mangle_name(section, option)
            if type(kind) is tuple:
                items[name] = self._read_filter(section, option, kind)
            else:
                items[name] = self._read_value(section, option, kind)

            # Remove option once handled (see the check function).
            try:
                self._conf_parser.remove_option(section, option)
            except ConfigParser.Error:
                pass

        for key, value in vars(overrides).iteritems():
            items[key] = value

        return Configuration(items)

    def _read_value(self, section, option, default):
        """Parse an option from the configuration file with the correct type."""
        try:
            reader = ConfigurationParser.READERS[type(default)]
            return reader(self._conf_parser, section, option)
        except ConfigParser.Error:
            return default

    def _read_filter(self, section, option, filter_def):
        """Read an option from the configuration file through a filter."""
        f, default = filter_def
        try:
            return f(self._conf_parser.get(section, option))
        except ConfigParser.Error:
            return f(default)

    def _get_default_configuration_file(self):
        """Return the configuration filename.

        This function builds the list of paths known by pycarddav and
        then return the first one which exists. The first paths
        searched are the ones described in the XDG Base Directory
        Standard. Each one of this path ends with
        DEFAULT_PATH/DEFAULT_FILE.

        On failure, the path DEFAULT_PATH/DEFAULT_FILE, prefixed with
        a dot, is searched in the home user directory. Ultimately,
        DEFAULT_FILE is searched in the current directory.
        """
        paths = []

        resource = os.path.join(
            Configuration.DEFAULT_PATH, Configuration.DEFAULT_FILE)
        paths.extend(self._xdg_helper.build_config_paths(resource))
        paths.append(os.path.expanduser(os.path.join('~', '.' + resource)))
        paths.append(os.path.expanduser(Configuration.DEFAULT_FILE))

        for path in paths:
            if os.path.exists(path):
                return path

        return None

    def _get_data_path(self):
        pass
