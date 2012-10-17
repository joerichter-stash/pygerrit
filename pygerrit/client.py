""" Gerrit client interface.

The MIT License

Copyright 2012 Sony Mobile Communications. All rights reserved.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

"""

from json import JSONDecoder
from Queue import Queue, Empty, Full

from pygerrit import escape_string
from pygerrit.error import GerritError
from pygerrit.events import GerritEventFactory
from pygerrit.models import Change
from pygerrit.ssh import GerritSSHClient
from pygerrit.stream import GerritStream

_GERRIT_VERSION_PREFIX = "gerrit version "


class GerritClient(object):

    """ Gerrit client interface. """

    def __init__(self, host):
        self._factory = GerritEventFactory()
        self._events = Queue()
        self._stream = None
        self._ssh_client = GerritSSHClient(host)
        self._gerrit_version = self._get_gerrit_version()

    def _get_gerrit_version(self):
        """ Run `gerrit version` to get the version of Gerrit connected to.

        Return the version as a string.  Empty if version was not returned.

        """
        _stdin, stdout, _stderr = self._ssh_client.run_gerrit_command("version")
        version_string = stdout.read()
        if version_string:
            if version_string.startswith(_GERRIT_VERSION_PREFIX):
                return version_string[len(_GERRIT_VERSION_PREFIX):].strip()
            return version_string.strip()
        return ""

    def query(self, term):
        """ Run `gerrit query` with the given term.

        Return a list of results as `Change` objects.

        """
        results = []
        command = ["query", "--current-patch-set", "--all-approvals",
                   "--format JSON", "--commit-message"]
        if isinstance(term, list):
            command += [escape_string(" ".join(term))]
        else:
            command += [escape_string(term)]
        _stdin, stdout, _stderr = self._ssh_client.run_gerrit_command(command)
        decoder = JSONDecoder()
        for line in stdout.read().splitlines():
            # Gerrit's response to the query command contains one or more
            # lines of JSON-encoded strings.  The last one is a status
            # dictionary containing the key "type" whose value indicates
            # whether or not the operation was successful.
            # According to http://goo.gl/h13HD it should be safe to use the
            # presence of the "type" key to determine whether the dictionary
            # represents a change or if it's the query status indicator.
            data = decoder.decode(line)
            if "type" in data:
                if data["type"] == "error":
                    raise GerritError("Query error: %s" % data["message"])
            else:
                results.append(Change(data))
        return results

    def start_event_stream(self):
        """ Start streaming events from `gerrit stream-events`. """
        if not self._stream:
            self._stream = GerritStream(self, ssh_client=self._ssh_client)
            self._stream.start()

    def stop_event_stream(self):
        """ Stop streaming events from `gerrit stream-events`."""
        if self._stream:
            self._stream.stop()
            self._stream.join()
            self._stream = None
            with self._events.mutex:
                self._events.queue.clear()

    def get_event(self, block=True, timeout=None):
        """ Get the next event from the queue.

        Return a `GerritEvent` instance, or None if:
         - `block` is False and there is no event available in the queue, or
         - `block` is True and no event is available within the time
        specified by `timeout`.

        """
        try:
            return self._events.get(block, timeout)
        except Empty:
            return None

    def put_event(self, json_data):
        """ Create event from `json_data` and add it to the queue.

        Raise GerritError if the queue is full, or the factory could not
        create the event.

        """
        try:
            event = self._factory.create(json_data)
            self._events.put(event)
        except Full:
            raise GerritError("Unable to add event: queue is full")
